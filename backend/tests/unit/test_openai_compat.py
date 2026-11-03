"""OpenAI-compatible adapters against httpx.MockTransport (no network)."""

import json
from typing import Any

import httpx
import pytest

from app.adapters.openai_compat import (
    AI_ANSWER_SCHEMA,
    OpenAICompatChatLLM,
    OpenAICompatClient,
    OpenAICompatEmbedder,
)
from app.domain.errors import LLMError, LLMInvalidJSON
from app.domain.prompts import SYSTEM_PROMPT

BASE = "https://api.proxyapi.ru/openai/v1"
GOOD = {
    "answer": "В 11:00.",
    "sources": [{"quote": "стендап проходит в 11:00"}],
    "confidence": "high",
    "needs_review": False,
}


class Recorder:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        assert isinstance(r, httpx.Response)
        return r

    def body(self, i: int) -> dict[str, Any]:
        out: dict[str, Any] = json.loads(self.requests[i].content)
        return out


def client(rec: Recorder, key: str = "sk-test", retries: int = 2) -> OpenAICompatClient:
    return OpenAICompatClient(
        BASE, key, timeout_s=1, max_retries=retries, backoff_s=0, transport=httpx.MockTransport(rec)
    )


def chat_resp(content: str | None, refusal: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content, "refusal": refusal}}]})


def emb_resp(n: int, dim: int, offset: int = 0) -> httpx.Response:
    data = [{"index": i, "embedding": [float(offset + i)] * dim} for i in reversed(range(n))]
    return httpx.Response(200, json={"data": data})


# --- embeddings ---------------------------------------------------------------------------------


async def test_embedder_batches_and_keeps_order() -> None:
    rec = Recorder(emb_resp(2, 3), emb_resp(1, 3, offset=10))
    emb = OpenAICompatEmbedder(client(rec), "text-embedding-3-small", 3, batch_size=2)
    out = await emb.embed(["a", "b\nc", "d"])
    assert out == [[0.0] * 3, [1.0] * 3, [10.0] * 3]
    assert [len(rec.body(i)["input"]) for i in range(2)] == [2, 1]
    assert rec.body(0) == {"model": "text-embedding-3-small", "input": ["a", "b c"], "dimensions": 3}
    req = rec.requests[0]
    assert str(req.url) == f"{BASE}/embeddings" and req.headers["authorization"] == "Bearer sk-test"


async def test_embedder_retries_429_and_timeouts() -> None:
    rec = Recorder(httpx.Response(429, text="rate"), httpx.ReadTimeout("slow"), emb_resp(1, 2))
    out = await OpenAICompatEmbedder(client(rec), "m", 2).embed(["x"])
    assert out == [[0.0, 0.0]] and len(rec.requests) == 3


async def test_embedder_gives_up_after_retries() -> None:
    rec = Recorder(*[httpx.Response(503, text="down")] * 3)
    with pytest.raises(LLMError, match="503"):
        await OpenAICompatEmbedder(client(rec, retries=2), "m", 2).embed(["x"])
    assert len(rec.requests) == 3


async def test_embedder_no_retry_on_401_and_dim_mismatch() -> None:
    rec = Recorder(httpx.Response(401, text="bad key"))
    with pytest.raises(LLMError, match="401"):
        await OpenAICompatEmbedder(client(rec), "m", 2).embed(["x"])
    assert len(rec.requests) == 1
    with pytest.raises(LLMError, match="shape"):
        await OpenAICompatEmbedder(client(Recorder(emb_resp(1, 5))), "m", 2).embed(["x"])


async def test_missing_key_is_llm_error() -> None:
    rec = Recorder()
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        await OpenAICompatEmbedder(client(rec, key=""), "m", 2).embed(["x"])
    assert rec.requests == []


# --- chat ---------------------------------------------------------------------------------------


async def test_chat_uses_strict_json_schema_and_russian_prompt() -> None:
    rec = Recorder(chat_resp(json.dumps(GOOD)))
    llm = OpenAICompatChatLLM(client(rec), "gpt-4o-mini")
    ans = await llm.answer_with_sources("Когда стендап?", "[S1] (doc 1) стендап проходит в 11:00")
    assert ans.confidence == "high" and ans.sources[0].quote == "стендап проходит в 11:00"
    body = rec.body(0)
    assert body["model"] == "gpt-4o-mini" and body["temperature"] == 0.0
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == AI_ANSWER_SCHEMA
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert "ТОЛЬКО по контексту" in SYSTEM_PROMPT and "Данных недостаточно" in SYSTEM_PROMPT
    assert "Когда стендап?" in body["messages"][1]["content"]


def test_schema_is_strict_everywhere() -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)

    walk(AI_ANSWER_SCHEMA)


async def test_chat_falls_back_to_json_object_once() -> None:
    rec = Recorder(
        httpx.Response(400, json={"error": {"message": "Invalid parameter: response_format json_schema"}}),
        chat_resp(json.dumps(GOOD)),
        chat_resp(json.dumps(GOOD)),
    )
    llm = OpenAICompatChatLLM(client(rec), "some-model")
    await llm.answer_with_sources("q?", "ctx")
    await llm.answer_with_sources("q?", "ctx")
    kinds = [rec.body(i)["response_format"]["type"] for i in range(3)]
    assert kinds == ["json_schema", "json_object", "json_object"]
    assert "JSON-объект" in rec.body(1)["messages"][0]["content"]


async def test_chat_400_other_reason_is_llm_error() -> None:
    rec = Recorder(httpx.Response(400, json={"error": {"message": "context length exceeded"}}))
    with pytest.raises(LLMError):
        await OpenAICompatChatLLM(client(rec), "m").answer_with_sources("q?", "c")


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({**GOOD, "extra": 1}),
        json.dumps({**GOOD, "confidence": "very"}),
        json.dumps({k: v for k, v in GOOD.items() if k != "sources"}),
        json.dumps({**GOOD, "needs_review": "false"}),  # strict: no coercion
    ],
)
async def test_chat_invalid_json(content: str) -> None:
    with pytest.raises(LLMInvalidJSON):
        await OpenAICompatChatLLM(client(Recorder(chat_resp(content))), "m").answer_with_sources("q?", "c")


async def test_chat_refusal_and_empty() -> None:
    with pytest.raises(LLMInvalidJSON, match="refused"):
        await OpenAICompatChatLLM(client(Recorder(chat_resp(None, "no"))), "m").answer_with_sources("q?", "c")
    with pytest.raises(LLMInvalidJSON):
        await OpenAICompatChatLLM(client(Recorder(chat_resp(None))), "m").answer_with_sources("q?", "c")


async def test_chat_5xx_is_llm_error_after_retries() -> None:
    rec = Recorder(*[httpx.Response(500, text="oops")] * 3)
    with pytest.raises(LLMError) as ei:
        await OpenAICompatChatLLM(client(rec), "m").answer_with_sources("q?", "c")
    assert not isinstance(ei.value, LLMInvalidJSON)
