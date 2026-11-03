"""SQLAlchemy 2.0 ORM models (persistence shape; not domain models)."""

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

DOCUMENT_STATUS = ENUM(
    "uploaded", "indexing", "ready", "failed", name="document_status", schema=SCHEMA, create_type=False
)


class Vector(UserDefinedType[list[float]]):
    """pgvector `vector` without fixed dimension (values bound as text and cast in SQL)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector"


class Base(DeclarativeBase):
    metadata = metadata


class DocumentRow(Base):
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


# Core tables (bulk paths)
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

snippet_embeddings = Table(
    "snippet_embeddings",
    metadata,
    Column("snippet_id", BigInteger, primary_key=True),
    Column("embedding_model", String(200), primary_key=True),
    Column("text_sha256", String(64), nullable=False),
    Column("embedding", Vector, nullable=False),
    Column("created_at", DateTime(timezone=True)),
)
