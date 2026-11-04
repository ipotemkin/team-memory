"""Преобразование строк ORM/Core ↔ доменные модели.

Держит доменные модели независимыми от SQLAlchemy: репозитории возвращают только доменные объекты.
"""

from typing import Any

from app.adapters.postgres.orm import AuditRunRow, DocumentRow, QaRunRow
from app.domain.models import AuditRecord, Document, DocumentStatus, QaRun, ReviewReason, Snippet, Source


def document_to_domain(r: DocumentRow) -> Document:
    """Строка documents → ``Document`` (sha-колонки CHAR дополняются пробелами — обрезаем)."""
    return Document(
        id=r.id,
        title=r.title,
        text=r.text,
        status=DocumentStatus(r.status),
        status_error=r.status_error,
        created_at=r.created_at,
        content_sha256=r.content_sha256.strip(),
        storage_bucket=r.storage_bucket,
        object_key=r.object_key,
        sha256=r.sha256.strip() if r.sha256 else None,
        mime_type=r.mime_type,
        size_bytes=r.size_bytes,
        indexed_at=r.indexed_at,
    )


def snippet_from_row(m: Any) -> Snippet:
    """Строка snippets (mapping из Core-запроса) → ``Snippet``."""
    return Snippet(
        id=m["id"],
        document_id=m["document_id"],
        ord=m["ord"],
        text=m["snippet_text"],
        char_start=m["char_start"],
        char_end=m["char_end"],
        text_sha256=str(m["text_sha256"]).strip(),
    )


def qa_run_to_row(run: QaRun) -> QaRunRow:
    """``QaRun`` → новая ORM-строка qa_runs (источники — JSONB)."""
    return QaRunRow(
        created_at=run.created_at,
        question=run.question,
        answer=run.answer,
        sources_json=[s.model_dump(mode="json") for s in run.sources],
        needs_review=run.needs_review,
        error=run.error.value if run.error else None,
        confidence=run.confidence,
        chat_model=run.chat_model,
        embedding_model=run.embedding_model,
        retrieval_mode=run.retrieval_mode,
        retrieved_ids=list(run.retrieved_ids),
    )


def qa_run_to_domain(r: QaRunRow) -> QaRun:
    """ORM-строка qa_runs → ``QaRun``."""
    return QaRun(
        id=r.id,
        created_at=r.created_at,
        question=r.question,
        answer=r.answer,
        sources=[Source.model_validate(s) for s in r.sources_json],
        needs_review=r.needs_review,
        error=ReviewReason(r.error) if r.error else None,
        confidence=r.confidence,
        chat_model=r.chat_model,
        embedding_model=r.embedding_model,
        retrieval_mode="fts_only" if r.retrieval_mode == "fts_only" else "hybrid",
        retrieved_ids=list(r.retrieved_ids or []),
    )


def audit_to_row(rec: AuditRecord) -> AuditRunRow:
    """``AuditRecord`` → ORM-строка audit_runs."""
    return AuditRunRow(
        created_at=rec.created_at,
        action=rec.action,
        input=rec.input,
        output=rec.output,
        status=rec.status,
        error=rec.error,
        duration_ms=rec.duration_ms,
        request_id=rec.request_id,
    )
