"""OllamaModel against a fake transport — no network, no model download."""

import json

import httpx
import pytest

from sanctionscreen.config import AssistantConfig
from sanctionscreen.review.models import (
    MockReviewModel,
    ModelUnavailable,
    OllamaModel,
    build_model,
)
from sanctionscreen.review.tools import TOOL_SPECS


def model_with(handler):
    return OllamaModel("qwen2.5:7b", "http://ollama.test", transport=httpx.MockTransport(handler))


def complete(model):
    return model.complete(
        [{"role": "user", "content": "hi"}], TOOL_SPECS, max_tokens=256, timeout=5
    )


def test_request_shape_and_tool_call_parsing():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "screen_name", "arguments": {"name": "Ivan"}}}
                    ],
                },
                "prompt_eval_count": 812,
                "eval_count": 34,
            },
        )

    turn = complete(model_with(handler))
    assert seen["url"] == "http://ollama.test/api/chat"
    body = seen["body"]
    assert body["model"] == "qwen2.5:7b"
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0, "num_predict": 256, "num_ctx": 8192}
    assert body["think"] is False
    assert [t["function"]["name"] for t in body["tools"]] == [t["name"] for t in TOOL_SPECS]
    assert turn.tool_calls[0].name == "screen_name"
    assert turn.tool_calls[0].arguments == {"name": "Ivan"}
    assert (turn.input_tokens, turn.output_tokens) == (812, 34)


def test_missing_model_is_unavailable_not_substituted():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(404, json={"error": "model 'qwen2.5:7b' not found"})

    with pytest.raises(ModelUnavailable, match=r"ollama pull qwen2\.5:7b"):
        complete(model_with(handler))
    assert calls == ["qwen2.5:7b"]  # exactly one attempt, same model


def test_connection_error_and_timeout():
    def refused(request):
        raise httpx.ConnectError("refused")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ModelUnavailable, match="not reachable"):
        complete(model_with(refused))
    with pytest.raises(ModelUnavailable, match="timed out"):
        complete(model_with(slow))


def test_server_error_and_garbage():
    with pytest.raises(ModelUnavailable, match="HTTP 500"):
        complete(model_with(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(ModelUnavailable, match="unparseable"):
        complete(model_with(lambda r: httpx.Response(200, text="not json")))


def test_build_model_is_explicit():
    assert isinstance(build_model(AssistantConfig()), MockReviewModel)
    with pytest.raises(ValueError, match="must be set explicitly"):
        build_model(AssistantConfig(provider="ollama", model=""))
    live = build_model(AssistantConfig(provider="ollama", model="llama3.1:8b"))
    assert isinstance(live, OllamaModel) and live.model == "llama3.1:8b"
