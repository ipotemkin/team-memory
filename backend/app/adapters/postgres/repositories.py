"""Репозитории Postgres: документы и qa_runs через ORM, фрагменты — через Core (массово); аудит.

Все репозитории работают в сессии UnitOfWork и не делают commit сами — транзакцией управляет UoW.
"""

from __future__ import annotations

import json

from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.postgres.mappers import (
    audit_to_row,
    document_to_domain,
    qa_run_to_domain,
    qa_run_to_row,
    snippet_from_row,
)
from app.adapters.postgres.orm import DocumentRow, QaRunRow, snippets
from app.domain.models import AuditRecord, Document, DocumentStatus, NewDocument, Page, QaRun, Snippet


class PgDocumentRepository:
    """Порт DocumentRepository (ORM)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, doc: NewDocument) -> Document:
        row = DocumentRow(**doc.model_dump())
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return document_to_domain(row)

    async def get(self, id: int) -> Document | None:
        """populate_existing: перечитываем строку из БД, даже если она уже есть в сессии."""
        row = await self._s.get(DocumentRow, id, populate_existing=True)
        return document_to_domain(row) if row else None

    async def list(self, page: Page) -> list[Document]:
        stmt = (
            select(DocumentRow)
            .order_by(DocumentRow.created_at.desc(), DocumentRow.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        return [document_to_domain(r) for r in (await self._s.scalars(stmt)).all()]

    async def set_status(self, id: int, status: DocumentStatus, error: str | None = None) -> None:
        """Меняет статус; при переходе в ready проставляет indexed_at."""
        await self._s.execute(
            text(
                "UPDATE app.documents SET status = CAST(:st AS app.document_status), status_error = :err, "
                "updated_at = now(), indexed_at = CASE WHEN :st = 'ready' THEN now() ELSE indexed_at END "
                "WHERE id = :id"
            ),
            {"st": status.value, "err": error, "id": id},
        )


class PgSnippetRepository:
    """Порт SnippetRepository (Core, массовые операции)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def replace_for_document(self, document_id: int, items: list[Snippet]) -> list[Snippet]:
        """Удаляет фрагменты документа и вставляет новые одним INSERT ... RETURNING.

        Эмбеддинги старых фрагментов удаляются каскадно (FK ON DELETE CASCADE).
        """
        await self._s.execute(delete(snippets).where(snippets.c.document_id == document_id))
        if not items:
            return []
        rows = [
            {
                "document_id": document_id,
                "ord": sn.ord,
                "snippet_text": sn.text,
                "text_sha256": sn.text_sha256,
                "char_start": sn.char_start,
                "char_end": sn.char_end,
            }
            for sn in items
        ]
        cols = [
            snippets.c[c]
            for c in ("id", "document_id", "ord", "snippet_text", "text_sha256", "char_start", "char_end")
        ]
        res = await self._s.execute(insert(snippets).values(rows).returning(*cols))
        return sorted((snippet_from_row(m) for m in res.mappings()), key=lambda s: s.ord)

    async def list_for_document(self, document_id: int) -> list[Snippet]:
        res = await self._s.execute(
            select(snippets).where(snippets.c.document_id == document_id).order_by(snippets.c.ord)
        )
        return [snippet_from_row(m) for m in res.mappings()]

    async def upsert_embeddings(self, model: str, items: list[tuple[int, str, list[float]]]) -> int:
        """Вставляет или обновляет эмбеддинги модели одним запросом.

        Пачка передаётся одним JSONB-параметром и разворачивается ``jsonb_to_recordset``. JOIN со
        snippets
        отбрасывает фрагменты, удалённые, пока считались векторы (переиндексация документа). Если модели
        ещё нет в реестре, она регистрируется (провайдер — префикс имени до ":").
        """
        if not items:
            return 0
        dim = len(items[0][2])
        provider = model.split(":", 1)[0] if ":" in model else "unknown"
        await self._s.execute(
            text(
                "INSERT INTO app.embedding_models (name, provider, dim) VALUES (:n, :p, :d) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"n": model, "p": provider, "d": dim},
        )
        res = await self._s.execute(
            text(
                "INSERT INTO app.snippet_embeddings (snippet_id, embedding_model, text_sha256, embedding) "
                "SELECT x.sid, :m, x.sha, CAST(x.vec AS vector) "
                "FROM jsonb_to_recordset(CAST(:items AS jsonb)) AS x(sid bigint, sha text, vec text) "
                "JOIN app.snippets s ON s.id = x.sid "
                "ON CONFLICT (snippet_id, embedding_model) DO UPDATE "
                "SET text_sha256 = EXCLUDED.text_sha256, embedding = EXCLUDED.embedding, created_at = now()"
            ),
            {
                "m": model,
                "items": json.dumps(
                    [{"sid": sid, "sha": sha, "vec": json.dumps(vec)} for sid, sha, vec in items]
                ),
            },
        )
        return int(getattr(res, "rowcount", 0) or 0)

    async def missing_embeddings(self, model: str, batch: int) -> list[Snippet]:
        """Фрагменты без эмбеддинга модели или с эмбеддингом устаревшего текста (по text_sha256)."""
        res = await self._s.execute(
            text(
                "SELECT s.id, s.document_id, s.ord, s.snippet_text, s.text_sha256, s.char_start, s.char_end "
                "FROM app.snippets s LEFT JOIN app.snippet_embeddings e "
                "ON e.snippet_id = s.id AND e.embedding_model = :m "
                "WHERE e.snippet_id IS NULL OR e.text_sha256 <> s.text_sha256 ORDER BY s.id LIMIT :b"
            ),
            {"m": model, "b": batch},
        )
        return [snippet_from_row(m) for m in res.mappings()]


class PgQaRunRepository:
    """Порт QaRunRepository (ORM)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, run: QaRun) -> QaRun:
        row = qa_run_to_row(run)
        self._s.add(row)
        await self._s.flush()
        return qa_run_to_domain(row)

    async def get(self, id: int) -> QaRun | None:
        row = await self._s.get(QaRunRow, id)
        return qa_run_to_domain(row) if row else None

    async def list(self, page: Page, needs_review: bool | None = None) -> list[QaRun]:
        stmt = select(QaRunRow)
        if needs_review is not None:
            stmt = stmt.where(QaRunRow.needs_review.is_(needs_review))
        stmt = (
            stmt.order_by(QaRunRow.created_at.desc(), QaRunRow.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        return [qa_run_to_domain(r) for r in (await self._s.scalars(stmt)).all()]


class PostgresAuditSink:
    """Транзакционный приёмник аудита: пишет в сессию UoW (фиксируется вместе с бизнес-данными)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, rec: AuditRecord) -> None:
        self._s.add(audit_to_row(rec))
        await self._s.flush()
