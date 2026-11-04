"""Простой поиск по ключевым словам среди зафиксированных in-memory фрагментов (score в 0..1).

Заменяет гибридный поиск Postgres в режиме DB_BACKEND=memory. Вектор вопроса игнорируется.
"""

import re

from app.adapters.memory.store import MemoryStore
from app.domain.guardrails import normalize
from app.domain.models import DocumentStatus, ScoredSnippet

_WORD = re.compile(r"\w+", re.UNICODE)
# Стоп-слова (рус./англ.), которые не несут смысла для поиска.
STOP = frozenset(
    "и в во на не что как по с со к ко у о об от до за из для или а но же ли то это бы при над под "
    "the a an of to in is are what how".split()
)
STEM = 5  # грубый «стемминг» префиксом: отпуск/отпуска/отпуском


def terms(text: str) -> set[str]:
    """Множество «терминов» текста: нормализованные слова без стоп-слов, обрезанные до ``STEM`` символов.

    Слова короче 3 букв отбрасываются (кроме чисел — «15 минут» важно).
    """
    return {w[:STEM] for w in _WORD.findall(normalize(text)) if w not in STOP and (len(w) > 2 or w.isdigit())}


class MemoryKeywordRetriever:
    """Порт Retriever: score = доля терминов вопроса, найденных во фрагменте."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def search(
        self, query: str, qvec: list[float] | None, model: str | None, k: int
    ) -> list[ScoredSnippet]:
        """Лучшие ``k`` фрагментов документов в статусе ready по доле совпавших терминов."""
        q = terms(query)
        if not q or k <= 0:
            return []
        st = self._store.state
        scored: list[ScoredSnippet] = []
        for sid, sn in st.snippets.items():
            doc = st.documents.get(sn.document_id)
            if doc is None or doc.status != DocumentStatus.READY:
                continue
            hit = len(q & terms(sn.text))
            if hit:
                scored.append(
                    ScoredSnippet(
                        snippet_id=sid, document_id=sn.document_id, text=sn.text, score=hit / len(q)
                    )
                )
        scored.sort(key=lambda s: (-s.score, s.snippet_id))
        return scored[:k]
