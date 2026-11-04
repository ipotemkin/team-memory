"""ORM-модели SQLAlchemy 2.0 — форма хранения, а не доменные модели.

Таблицы создаются миграциями Alembic (``backend/migrations``), а не ``metadata.create_all``.
Документы и qa_runs — ORM-классы; фрагменты и эмбеддинги — Core-таблицы для массовых вставок.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

SCHEMA = "app"
metadata = MetaData(schema=SCHEMA)

# Postgres-enum статусов документа; create_type=False — тип создаёт миграция.
DOCUMENT_STATUS = ENUM(
    "uploaded", "indexing", "ready", "failed", name="document_status", schema=SCHEMA, create_type=False
)


class Vector(UserDefinedType[list[float]]):
    """Тип pgvector ``vector`` без фиксированной размерности.

    Значения передаются текстом и приводятся в SQL (``CAST(... AS vector)``); размерность у каждой модели
    своя и проверяется через частичный индекс ``embedding::vector(dim)``.
    """

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector"


class Base(DeclarativeBase):
    """Базовый декларативный класс со схемой ``app``."""

    metadata = metadata


class DocumentRow(Base):
    """Таблица app.documents."""

    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(DOCUMENT_STATUS, server_default="uploaded")
    status_error: Mapped[str | None] = mapped_column(Text)
    storage_bucket: Mapped[str | None] = mapped_column(Text)
    object_key: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    mime_type: Mapped[str | None] = mapped_column(String(127))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_sha256: Mapped[str] = mapped_column(String(64))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QaRunRow(Base):
    """Таблица app.qa_runs (история вопросов и ответов)."""

    __tablename__ = "qa_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    question: Mapped[str] = mapped_column(String(2000))
    answer: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    needs_review: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(String(10))
    chat_model: Mapped[str | None] = mapped_column(String(200))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    retrieval_mode: Mapped[str] = mapped_column(String(20))
    retrieved_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))


class AuditRunRow(Base):
    """Таблица app.audit_runs, партиционированная по месяцам created_at (поэтому created_at входит в PK)."""

    __tablename__ = "audit_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(100))
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))


# Core-таблицы (массовые вставки и выборки)
# tsv — tsvector('russian'), генерируемая колонка в БД (см. миграцию 0002).
snippets = Table(
    "snippets",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("created_at", DateTime(timezone=True)),
    Column("document_id", BigInteger, nullable=False),
    Column("ord", Integer, nullable=False),
    Column("snippet_text", Text, nullable=False),
    Column("text_sha256", String(64), nullable=False),
    Column("char_start", Integer, nullable=False),
    Column("char_end", Integer, nullable=False),
    Column("tsv", TSVECTOR),
)

# Эмбеддинги: PK (snippet_id, embedding_model) — у фрагмента по вектору на каждую модель.
# text_sha256 фиксирует версию текста: при изменении фрагмента вектор считается устаревшим.
snippet_embeddings = Table(
    "snippet_embeddings",
    metadata,
    Column("snippet_id", BigInteger, primary_key=True),
    Column("embedding_model", String(200), primary_key=True),
    Column("text_sha256", String(64), nullable=False),
    Column("embedding", Vector, nullable=False),
    Column("created_at", DateTime(timezone=True)),
)
