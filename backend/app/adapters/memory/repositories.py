"""In-memory репозитории и приёмник аудита (эталонные реализации контрактов портов).

Работают с ``MemoryState`` — копией состояния, которую выдаёт MemoryUnitOfWork.
"""

from __future__ import annotations

from app.adapters.memory.store import MemoryState
from app.domain.models import (
    AuditRecord,
    Document,
    DocumentStatus,
    NewDocument,
    Page,
    QaRun,
    Snippet,
)


class MemoryDocumentRepository:
    """Порт DocumentRepository поверх словаря документов."""

    def __init__(self, state: MemoryState) -> None:
        self._s = state

    async def add(self, doc: NewDocument) -> Document:
        d = Document(id=self._s.next_id("documents"), **doc.model_dump())
        self._s.documents[d.id] = d
        return d

    async def get(self, id: int) -> Document | None:
        return self._s.documents.get(id)

    async def list(self, page: Page) -> list[Document]:
        docs = sorted(self._s.documents.values(), key=lambda d: (d.created_at, d.id), reverse=True)
        return docs[page.offset : page.offset + page.limit]

    async def set_status(self, id: int, status: DocumentStatus, error: str | None = None) -> None:
        d = self._s.documents[id]
        self._s.documents[id] = d.model_copy(update={"status": status, "status_error": error})


class MemorySnippetRepository:
    """Порт SnippetRepository: фрагменты и эмбеддинги ((snippet_id, модель) → (sha, вектор))."""

    def __init__(self, state: MemoryState) -> None:
        self._s = state

    async def replace_for_document(self, document_id: int, snippets: list[Snippet]) -> list[Snippet]:
        """Удаляет старые фрагменты документа (вместе с их эмбеддингами) и сохраняет новые с новыми id."""
        for sid in [k for k, v in self._s.snippets.items() if v.document_id == document_id]:
            del self._s.snippets[sid]
            for key in [k for k in self._s.embeddings if k[0] == sid]:
                del self._s.embeddings[key]
        out = []
        for sn in snippets:
            stored = sn.model_copy(update={"id": self._s.next_id("snippets"), "document_id": document_id})
            self._s.snippets[stored.id or 0] = stored
            out.append(stored)
        return out

    async def list_for_document(self, document_id: int) -> list[Snippet]:
        return sorted(
            (s for s in self._s.snippets.values() if s.document_id == document_id), key=lambda s: s.ord
        )

    async def upsert_embeddings(self, model: str, items: list[tuple[int, str, list[float]]]) -> int:
        """Сохраняет векторы; фрагменты, удалённые за время расчёта, пропускаются."""
        n = 0
        for sid, sha, vec in items:
            if sid in self._s.snippets:
                self._s.embeddings[(sid, model)] = (sha, list(vec))
                n += 1
        return n

    async def missing_embeddings(self, model: str, batch: int) -> list[Snippet]:
        """Фрагменты без эмбеддинга модели или с эмбеддингом устаревшего текста (sha не совпадает)."""
        out = []
        for sid in sorted(self._s.snippets):
            sn = self._s.snippets[sid]
            emb = self._s.embeddings.get((sid, model))
            if emb is None or emb[0] != sn.text_sha256:
                out.append(sn)
            if len(out) >= batch:
                break
        return out


class MemoryQaRunRepository:
    """Порт QaRunRepository поверх словаря запусков."""

    def __init__(self, state: MemoryState) -> None:
        self._s = state

    async def add(self, run: QaRun) -> QaRun:
        stored = run.model_copy(update={"id": self._s.next_id("qa_runs")})
        self._s.qa_runs[stored.id or 0] = stored
        return stored

    async def get(self, id: int) -> QaRun | None:
        return self._s.qa_runs.get(id)

    async def list(self, page: Page, needs_review: bool | None = None) -> list[QaRun]:
        runs = [r for r in self._s.qa_runs.values() if needs_review is None or r.needs_review == needs_review]
        runs.sort(key=lambda r: (r.created_at, r.id or 0), reverse=True)
        return runs[page.offset : page.offset + page.limit]


class MemoryAuditSink:
    """Приёмник аудита в список: внутри UoW (транзакционно) или самостоятельный."""

    def __init__(self, state: MemoryState | None = None) -> None:
        self._s = state or MemoryState()

    @property
    def records(self) -> list[AuditRecord]:
        """Все записанные записи аудита (для тестов)."""
        return self._s.audit

    async def record(self, rec: AuditRecord) -> None:
        self._s.audit.append(rec)
