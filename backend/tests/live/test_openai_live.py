"""Живой smoke против реального провайдера (ProxyAPI/OpenAI). Только при заданном OPENAI_API_KEY.

    cd backend && OPENAI_API_KEY=sk-... uv run pytest -m live -q
Стоит доли цента (gpt-4o-mini + text-embedding-3-small, несколько сотен токенов).
"""

import math
from pathlib import Path

import pytest

from app.adapters.openai_compat import OpenAICompatChatLLM, OpenAICompatClient, OpenAICompatEmbedder
from app.config import Settings
from app.domain.guardrails import verify_quotes
from app.domain.models import INSUFFICIENT_DATA, ScoredSnippet
from app.domain.prompts import build_context

ROOT = Path(__file__).resolve().parents[3]
ENV_FILES = (str(ROOT / ".env"), ".env")  # env vars win over the project-root .env


def live_settings() -> Settings:
    return Settings(_env_file=ENV_FILES)  # type: ignore[call-arg]


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not live_settings().OPENAI_API_KEY, reason="OPENAI_API_KEY not set"),
]


def _client() -> tuple[Settings, OpenAICompatClient]:
    s = live_settings()
    return s, OpenAICompatClient(
        s.OPENAI_BASE_URL, s.OPENAI_API_KEY, s.OPENAI_TIMEOUT_S, s.OPENAI_MAX_RETRIES
    )


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.hypot(*a) * math.hypot(*b))


async def test_live_embeddings() -> None:
    s, c = _client()
    try:
        emb = OpenAICompatEmbedder(c, s.EMBEDDINGS_MODEL, s.EMBEDDINGS_DIM)
        a, b, z = await emb.embed(["Во сколько стендап?", "Стендап проходит в 11:00.", "Рецепт борща"])
    finally:
        await c.aclose()
    assert len(a) == s.EMBEDDINGS_DIM
    assert cos(a, b) > cos(a, z)


async def test_live_answer_and_insufficient() -> None:
    s, c = _client()
    snip = ScoredSnippet(snippet_id=1, document_id=1, text="Ежедневный стендап проходит в 11:00.", score=1.0)
    try:
        llm = OpenAICompatChatLLM(c, s.CHAT_MODEL, s.OPENAI_STRUCTURED_OUTPUTS)
        ans = await llm.answer_with_sources("Во сколько стендап?", build_context([snip]))
        verified, bad = verify_quotes(ans, [snip])
        assert verified and not bad and "11" in ans.answer
        none = await llm.answer_with_sources("Какая зарплата у тимлида?", build_context([snip]))
    finally:
        await c.aclose()
    assert none.needs_review is True or none.answer == INSUFFICIENT_DATA
