"""Model clients for the review loop.

- OllamaModel      — live local model via Ollama /api/chat with native tool
                     calling. The model name is mandatory; there is no default
                     and no fallback to another model (DECISIONS.md D13).
- MockReviewModel  — offline, deterministic stand-in that picks tools from the
                     tool results it sees. It is NOT an LLM and every result it
                     produces is labelled is_mock=True.
- ScriptedModel    — test double that replays fixed or data-dependent turns.

Messages use Ollama's native shape: {"role", "content", "tool_calls"?,
"tool_name"?}.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

import httpx

from sanctionscreen.config import AssistantConfig


class ModelUnavailable(Exception):
    """The configured model could not be reached or returned an unusable reply."""


class ModelRateLimited(ModelUnavailable):
    """The same configured provider may be retried after its cooldown."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"rate limited; retry after {retry_after:.0f}s")


@dataclass
class ToolCall:
    name: str
    arguments: dict | str | None
    id: str = ""


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""


class ChatModel(Protocol):
    provider: str
    model: str
    is_mock: bool

    def complete(
        self, messages: list[dict], tools: list[dict], *, max_tokens: int, timeout: float
    ) -> ModelTurn: ...


def _post_chat(
    url: str,
    payload: dict,
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None,
    headers: dict | None = None,
) -> httpx.Response:
    """An absolute deadline, including connect, write and read together."""

    async def send() -> httpx.Response:
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
                    return await client.post(url, json=payload, headers=headers)
        except TimeoutError as exc:
            raise httpx.ReadTimeout("model request deadline reached") from exc

    return asyncio.run(send())


class OllamaModel:
    provider = "ollama"
    is_mock = False

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        think: bool = False,
        context: int = 8192,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not model:
            raise ValueError("assistant.model must be set explicitly for provider 'ollama'")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self.think = think
        self.context = context

    def complete(
        self, messages: list[dict], tools: list[dict], *, max_tokens: int, timeout: float
    ) -> ModelTurn:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": [{"type": "function", "function": t} for t in tools],
            "stream": False,
            "think": self.think,
            "keep_alive": "5m",
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": self.context},
        }
        try:
            response = _post_chat(
                f"{self.base_url}/api/chat", payload, timeout=timeout, transport=self._transport
            )
        except httpx.TimeoutException as exc:
            raise ModelUnavailable(f"ollama model {self.model!r} timed out") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailable(
                f"ollama not reachable at {self.base_url} ({exc.__class__.__name__})"
            ) from exc
        if response.status_code == 404:
            raise ModelUnavailable(
                f"not installed at {self.base_url}; run `ollama pull {self.model}` "
                "(no other model is substituted)"
            )
        if response.status_code != 200:
            raise ModelUnavailable(f"ollama returned HTTP {response.status_code}")
        try:
            body = response.json()
            message = body["message"]
            if not isinstance(message, dict):
                raise ValueError("message must be an object")
            calls = _parse_calls(message.get("tool_calls"))
            input_tokens = _tokens(body.get("prompt_eval_count"))
            output_tokens = _tokens(body.get("eval_count"))
            if message.get("content") is not None and not isinstance(message["content"], str):
                raise ValueError("content must be text")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ModelUnavailable("ollama returned an unparseable response") from exc
        return ModelTurn(
            text=message.get("content") or "",
            tool_calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=str(body.get("done_reason") or ""),
        )


def _tokens(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("invalid token count")
    return value


def _parse_calls(raw: Any) -> list[ToolCall]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 40:
        raise ValueError("invalid tool calls")
    calls = []
    for call in raw:
        fn = call["function"]
        if not isinstance(fn["name"], str):
            raise ValueError("invalid tool name")
        args = fn.get("arguments")
        if not isinstance(args, (dict, str, type(None))):
            raise ValueError("invalid argument type")
        calls.append(ToolCall(fn["name"], args, str(call.get("id") or uuid.uuid4().hex)))
    return calls


class HostedModel:
    """Native function calling on pinned hosted providers; no model substitution.

    OpenRouter accepts only explicit :free variants and disables provider
    fallback. Groq must use an account on its free plan (billing is account
    controlled). Keys stay out of results, configuration reprs and errors.
    """

    is_mock = False
    ENDPOINTS: ClassVar[dict[str, str]] = {
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if provider not in self.ENDPOINTS or not model:
            raise ValueError("hosted provider and model must be set explicitly")
        if provider == "openrouter" and (not model.endswith(":free") or model == "openrouter/free"):
            raise ValueError("openrouter requires an explicit model ID ending in :free")
        self.provider, self.model = provider, model
        self._api_key, self._transport = api_key, transport
        self._retry_at = 0.0

    def complete(
        self, messages: list[dict], tools: list[dict], *, max_tokens: int, timeout: float
    ) -> ModelTurn:
        remaining = self._retry_at - time.monotonic()
        if remaining > 0:
            raise ModelRateLimited(remaining)
        if not self._api_key:
            raise ModelUnavailable(
                f"set {self.provider.upper()}_API_KEY in the service environment"
            )
        # Translate the shared transcript while preserving IDs on every tool result.
        wire = []
        for message in messages:
            m = {"role": message["role"], "content": message.get("content", "")}
            if message.get("tool_calls"):
                m["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["function"]["name"],
                            "arguments": (
                                c["function"]["arguments"]
                                if isinstance(c["function"]["arguments"], str)
                                else json.dumps(c["function"]["arguments"])
                            ),
                        },
                    }
                    for c in message["tool_calls"]
                ]
            if m["role"] == "tool":
                m["tool_call_id"] = message["tool_call_id"]
            wire.append(m)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": wire,
            "tools": [{"type": "function", "function": t} for t in tools],
            "tool_choice": "required",
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.provider == "openrouter":
            payload["reasoning"] = {"enabled": False, "exclude": True}
            payload["provider"] = {
                "allow_fallbacks": False,
                "require_parameters": True,
                "max_price": {"prompt": 0, "completion": 0},
            }
        try:
            response = _post_chat(
                f"{self.ENDPOINTS[self.provider]}/chat/completions",
                payload,
                timeout=timeout,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            raise ModelUnavailable(
                f"{self.provider} request failed ({type(exc).__name__})"
            ) from exc
        if response.status_code == 429:
            try:
                delay = min(86400, max(1, float(response.headers.get("retry-after", "60"))))
            except ValueError:
                delay = 60.0
            self._retry_at = time.monotonic() + delay
            raise ModelRateLimited(delay)
        if response.status_code != 200:
            raise ModelUnavailable(f"{self.provider} returned HTTP {response.status_code}")
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            calls = _parse_calls(message.get("tool_calls"))
            content = message.get("content") or ""
            if not isinstance(content, str):
                raise ValueError("content must be text")
            usage = body.get("usage") or {}
            return ModelTurn(
                content,
                calls,
                _tokens(usage.get("prompt_tokens")),
                _tokens(usage.get("completion_tokens")),
                str(body["choices"][0].get("finish_reason") or ""),
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ModelUnavailable(f"{self.provider} returned an unparseable response") from exc


ScriptStep = ModelTurn | Callable[[list[dict]], ModelTurn]


class ScriptedModel:
    """Replays a fixed script; callables receive the message history."""

    provider = "scripted"
    is_mock = True

    def __init__(self, steps: Sequence[ScriptStep], model: str = "scripted-test") -> None:
        self.steps = list(steps)
        self.model = model
        self.calls = 0

    def complete(
        self, messages: list[dict], tools: list[dict], *, max_tokens: int, timeout: float
    ) -> ModelTurn:
        if self.calls >= len(self.steps):
            return ModelTurn(text="(script exhausted)")
        step = self.steps[self.calls]
        self.calls += 1
        return step(messages) if callable(step) else step


def _tool_results(messages: list[dict]) -> list[tuple[str, dict]]:
    out = []
    for m in messages:
        if m.get("role") == "tool":
            try:
                out.append((m.get("tool_name", ""), json.loads(m["content"])))
            except (ValueError, KeyError):
                continue
    return out


def customer_input(messages: list[dict]) -> dict:
    """The analyst's input block from the first user message."""
    for m in messages:
        if m.get("role") == "user":
            text = m["content"]
            start, end = text.find("<customer_input>"), text.find("</customer_input>")
            if start != -1 and end != -1:
                return json.loads(text[start + len("<customer_input>") : end])
    return {}


_KIND = {"match": "supports", "conflict": "conflicts", "unknown": "unknown"}


class MockReviewModel:
    """Deterministic offline policy that reacts to tool results.

    screen -> fetch every candidate -> ask for details when nothing can be
    compared -> otherwise draft strictly from the deterministic comparisons.
    """

    provider = "mock"
    model = "mock-review-policy-v1"
    is_mock = True

    def complete(
        self, messages: list[dict], tools: list[dict], *, max_tokens: int, timeout: float
    ) -> ModelTurn:
        cust = customer_input(messages)
        results = _tool_results(messages)
        screens = [r for n, r in results if n == "screen_name" and "candidates" in r]
        records = {r["record_id"]: r for n, r in results if n == "get_record" and "record_id" in r}
        if results and results[-1][0] == "draft_case" and "problems" in results[-1][1]:
            return ModelTurn(text="Draft was rejected; stopping for human review.")

        if not screens:
            args: dict[str, Any] = {"name": cust.get("name", "")}
            if cust.get("entity_type"):
                args["entity_type"] = cust["entity_type"]
            return ModelTurn(tool_calls=[ToolCall("screen_name", args)])

        screen = screens[-1]
        for cand in screen["candidates"]:
            if cand["record_id"] not in records:
                return ModelTurn(
                    tool_calls=[ToolCall("get_record", {"record_id": cand["record_id"]})]
                )

        given = [f for f in ("date_of_birth", "nationality", "entity_type") if cust.get(f)]
        if screen["candidates"] and not given:
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        "request_information",
                        {
                            "fields": ["date_of_birth", "nationality"],
                            "reason": f"{len(screen['candidates'])} candidate record(s) "
                            "match on name alone; date of birth and nationality are needed "
                            "to compare them.",
                        },
                    )
                ]
            )
        return ModelTurn(tool_calls=[ToolCall("draft_case", self._draft(screen, records, cust))])

    @staticmethod
    def _draft(screen: dict, records: dict[str, dict], cust: dict) -> dict:
        candidates = []
        for cand in screen["candidates"]:
            rec = records[cand["record_id"]]
            evidence = [
                {
                    "record_id": rec["record_id"],
                    "field": "names",
                    "value": cand["matched_name"],
                    "kind": "supports",
                    "statement": f"Name similarity score {cand['score']} on "
                    f"{cand['matched_name_type']} name (layers: "
                    f"{', '.join(cand['layers_fired'])}).",
                }
            ]
            for comp in rec["comparison"]:
                if comp["customer_value"] is None:
                    continue
                evidence.append(
                    {
                        "record_id": rec["record_id"],
                        "field": comp["field"],
                        "value": comp["record_value"] or "",
                        "kind": _KIND[comp["result"]],
                        "statement": comp["detail"],
                    }
                )
            kinds = {e["kind"] for e in evidence[1:]}
            if "conflicts" in kinds:
                assessment = "unlikely_match"
                rationale = "At least one identifying detail conflicts with the record."
            elif "supports" in kinds:
                assessment = "possible_match"
                rationale = "Name and at least one identifying detail are consistent."
            else:
                assessment = "insufficient_information"
                rationale = "Only name similarity is available for this record."
            candidates.append(
                {
                    "record_id": rec["record_id"],
                    "assessment": assessment,
                    "evidence": evidence,
                    "rationale": rationale,
                }
            )
        n = len(candidates)
        summary = (
            f"Screening of the customer name returned {n} candidate record(s) at threshold "
            f"{screen['threshold']:g}."
            if n
            else f"Screening returned no candidate records at threshold {screen['threshold']:g}."
        )
        steps = (
            ["Reviewer to compare the evidence above with customer documents and decide."]
            if n
            else ["Reviewer to note the screening_id and proceed under the usual procedure."]
        )
        missing = [f for f in ("date_of_birth", "nationality") if not cust.get(f)]
        return {
            "summary": summary,
            "candidates": candidates,
            "missing_information": missing,
            "suggested_next_steps": steps,
        }


def build_model(config: AssistantConfig) -> ChatModel:
    """The configured model — never a substitute (DECISIONS.md D13)."""
    if config.provider == "mock":
        return MockReviewModel()
    if config.provider == "ollama":
        return OllamaModel(
            config.model,
            config.ollama_url,
            think=config.ollama_think,
            context=config.ollama_context,
        )
    if config.provider in {"groq", "openrouter"}:
        key = config.api_key.get_secret_value() or os.environ.get(
            f"{config.provider.upper()}_API_KEY", ""
        )
        return HostedModel(config.provider, config.model, key)
    raise ValueError(f"unknown assistant provider {config.provider!r}")
