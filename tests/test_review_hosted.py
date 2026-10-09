"""Hosted native-tool protocol, free routing, secret handling and quota recovery."""

import asyncio
import json
import time

import httpx
import pytest

from sanctionscreen.config import AssistantConfig
from sanctionscreen.review.models import (
    HostedModel,
    ModelRateLimited,
    ModelUnavailable,
    build_model,
)
from sanctionscreen.review.tools import TOOL_SPECS

MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


def response():
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "screen_name",
                                "arguments": '{"name":"Ivan Testov"}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 500, "completion_tokens": 20},
    }


def complete(model, messages=None):
    return model.complete(
        messages or [{"role": "user", "content": "data"}], TOOL_SPECS, max_tokens=256, timeout=5
    )


def test_native_tool_transcript_and_free_routing():
    seen = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    model = HostedModel("openrouter", MODEL, "test-secret", transport=httpx.MockTransport(handler))
    first = complete(model)
    assert first.tool_calls[0].id == "call_1"
    assert first.tool_calls[0].arguments == '{"name":"Ivan Testov"}'
    assert (first.input_tokens, first.output_tokens) == (500, 20)
    complete(
        model,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "screen_name",
                            "arguments": {"name": "Ivan Testov"},
                        },
                    }
                ],
            },
            {"role": "tool", "tool_name": "screen_name", "tool_call_id": "call_1", "content": "{}"},
        ],
    )
    body = seen[1]
    assert body["model"] == MODEL and "models" not in body
    assert body["provider"] == {
        "allow_fallbacks": False,
        "require_parameters": True,
        "max_price": {"prompt": 0, "completion": 0},
    }
    assert body["messages"][1]["tool_call_id"] == "call_1"
    assert json.loads(body["messages"][0]["tool_calls"][0]["function"]["arguments"]) == {
        "name": "Ivan Testov",
    }


@pytest.mark.parametrize("model", ["", "openrouter/free", "openrouter/auto", "some/paid-model"])
def test_rejects_unspecified_random_or_paid_model(model):
    with pytest.raises(ValueError):
        HostedModel("openrouter", model, "test-key")


def test_rate_limit_cooldown_and_same_model_recovery(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sanctionscreen.review.models.time.monotonic", lambda: now[0])
    requests = []

    def handler(request):
        requests.append(json.loads(request.content)["model"])
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(200, json=response())

    model = HostedModel("openrouter", MODEL, "key", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelRateLimited) as caught:
        complete(model)
    assert caught.value.retry_after == 30
    now[0] += 10
    with pytest.raises(ModelRateLimited):
        complete(model)
    assert len(requests) == 1  # cooldown avoids wasting the free quota
    now[0] += 21
    assert complete(model).tool_calls[0].name == "screen_name"
    assert requests == [MODEL, MODEL]


@pytest.mark.parametrize("status", [401, 402, 404, 500, 503])
def test_errors_never_echo_remote_body_or_key(status):
    model = HostedModel(
        "openrouter",
        MODEL,
        "test-secret",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, text="test-secret: sensitive body")
        ),
    )
    with pytest.raises(ModelUnavailable) as caught:
        complete(model)
    assert str(status) in str(caught.value)
    assert "test-secret" not in str(caught.value)
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"choices": []},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"tool_calls": [None]}}]},
        response() | {"usage": {"prompt_tokens": -1}},
    ],
)
def test_malformed_responses_are_unavailable(body):
    model = HostedModel(
        "openrouter",
        MODEL,
        "key",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )
    with pytest.raises(ModelUnavailable, match="unparseable"):
        complete(model)


def test_explicit_config_secret_and_missing_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = AssistantConfig(provider="openrouter", model=MODEL, api_key="test-secret")
    assert "test-secret" not in repr(config)
    assert build_model(config).model == MODEL
    model = build_model(AssistantConfig(provider="openrouter", model=MODEL))
    with pytest.raises(ModelUnavailable, match="OPENROUTER_API_KEY"):
        complete(model)


def test_groq_endpoint_is_pinned():
    def handler(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert "provider" not in json.loads(request.content)
        return httpx.Response(200, json=response())

    assert complete(
        HostedModel("groq", "explicit-model", "key", transport=httpx.MockTransport(handler))
    ).tool_calls


def test_slow_provider_is_cancelled_by_total_deadline():
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=response())

    model = HostedModel("openrouter", MODEL, "key", transport=httpx.MockTransport(slow))
    started = time.monotonic()
    with pytest.raises(ModelUnavailable, match="ReadTimeout"):
        model.complete([], TOOL_SPECS, max_tokens=256, timeout=0.02)
    assert time.monotonic() - started < 0.5
