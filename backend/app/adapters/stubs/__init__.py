"""Детерминированные stub-адаптеры (фейковые LLM и эмбеддинги) для тестов и офлайн-запуска.

Включаются LLM_PROVIDER=stub / EMBEDDINGS_PROVIDER=stub: приложение полностью работает без ключа
API, а ответы воспроизводимы (одинаковый вход → одинаковый выход).
"""

import hashlib
import json
import math
import re
from typing import Literal

from pydantic import ValidationError

from app.adapters.memory.retriever import terms
from app.domain.errors import LLMError, LLMInvalidJSON
from app.domain.models import INSUFFICIENT_DATA, AiAnswer
from app.domain.prompts import split_context

StubMode = Literal["normal", "low_confidence", "invalid_json", "error", "hallucinate"]
# Граница предложения или перевод строки.
_SENT = re.compile(r"(?<=[.!?…])\s+|\n+")
# Цитата, которой заведомо нет в контексте (режим hallucinate → unverified_quote).
HALLUCINATED_QUOTE = "Эта цитата отсутствует в контексте базы знаний"


class StubEmbedder:
    """Эмбеддер «мешок слов с хешированием»: детерминированный, L2-нормированный.

    Каждый термин (см. ``memory.retriever.terms``) хешируется в одну из ``dim`` координат; похожие по
    словам тексты получают близкие векторы, так что гибридный поиск работает и без реальной модели.
    """

    def __init__(self, dim: int = 64, model_name: str = "stub:hash-64") -> None:
        self.dim = dim
        self.model_name = model_name

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for term in terms(t):
                h = int.from_bytes(hashlib.sha256(term.encode()).digest()[:4], "big")
                v[h % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class StubChatLLM:
    """Экстрактивная «LLM»: берёт самые подходящие предложения контекста как дословные цитаты.

    mode: normal | low_confidence | invalid_json | error | hallucinate — режимы для тестов guardrails
    (низкая уверенность, битый JSON, недоступность, выдуманная цитата). ``calls`` считает вызовы.
    """

    def __init__(
        self, mode: StubMode = "normal", model_name: str = "stub:extractive", max_quotes: int = 2
    ) -> None:
        self.mode: StubMode = mode
        self.model_name = model_name
        self.max_quotes = max_quotes
        self.calls = 0

    async def answer_with_sources(self, question: str, context: str) -> AiAnswer:
        """Ответ по контексту, проверенный той же схемой ``AiAnswer``, что и у реальной LLM.

        Raises:
            LLMError: режим error.
            LLMInvalidJSON: ответ не прошёл схему (режим invalid_json).
        """
        self.calls += 1
        if self.mode == "error":
            raise LLMError("stub: upstream timeout")
        raw = self._raw(question, context)
        try:
            return AiAnswer.model_validate_json(raw)
        except ValidationError as exc:
            raise LLMInvalidJSON(f"schema validation failed: {exc.error_count()} errors") from exc

    def _raw(self, question: str, context: str) -> str:
        """Формирует «сырой» JSON-ответ.

        Предложения контекста ранжируются по доле терминов вопроса; цитатами становятся до ``max_quotes``
        лучших с долей ≥ 0.5. Нет таких — «данных недостаточно» с low confidence.
        """
        if self.mode == "invalid_json":
            return '{"answer": "oops", "sources": "not-a-list", "confidence": "sure"'
        q = terms(question)
        sentences = [s.strip() for frag in split_context(context) for s in _SENT.split(frag) if s.strip()]
        ranked = sorted(
            ((len(q & terms(s)) / max(len(q), 1), i, s) for i, s in enumerate(sentences)),
            key=lambda x: (-x[0], x[1]),
        )
        best = [s for score, _, s in ranked[: self.max_quotes] if score >= 0.5]
        if not best or self.mode == "low_confidence":
            payload = {"answer": INSUFFICIENT_DATA, "sources": [], "confidence": "low", "needs_review": True}
        else:
            quotes = [{"quote": s} for s in best]
            if self.mode == "hallucinate":
                quotes.append({"quote": HALLUCINATED_QUOTE})
            payload = {
                "answer": " ".join(best),
                "sources": quotes,
                "confidence": "high" if ranked[0][0] >= 0.75 else "medium",
                "needs_review": False,
            }
        return json.dumps(payload, ensure_ascii=False)


__all__ = ["HALLUCINATED_QUOTE", "StubChatLLM", "StubEmbedder", "StubMode"]
