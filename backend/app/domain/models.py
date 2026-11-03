"""Domain models (Pydantic v2). No infrastructure dependencies."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# --- validation limits (see architecture Page, "Контракты API") ---
TITLE_MAX = 200
TEXT_MAX = 100_000
QUESTION_MIN = 3
QUESTION_MAX = 2000
CONTEXT_MAX = 20_000
CONTEXT_PROMPT_LIMIT = 8000  # assembled RAG context for ChatLLM
PAGE_LIMIT_MAX = 200
PAGE_LIMIT_DEFAULT = 50

INSUFFICIENT_DATA = "Данных недостаточно для ответа. Вопрос передан на ручную проверку."

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TEXT_MAX)]
Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=QUESTION_MIN, max_length=QUESTION_MAX)
]
Context = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CONTEXT_MAX)]


def utcnow() -> datetime:
    return datetime.now(UTC)


class ReviewReason(StrEnum):
    NO_SOURCES = "no_sources"
    LOW_CONFIDENCE = "low_confidence"
    UNVERIFIED_QUOTE = "unverified_quote"
    LLM_INVALID_JSON = "llm_invalid_json"
    LLM_ERROR = "llm_error"


ReasonCode = ReviewReason


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Page(_Frozen):
    limit: int = Field(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)


class NewDocument(_Frozen):
    title: Title
    text: Text
    content_sha256: str = ""
    storage_bucket: str | None = None
    object_key: str | None = None
    sha256: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class Document(_Frozen):
    id: int
    title: str
    text: str | None
    status: DocumentStatus = DocumentStatus.UPLOADED
    status_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    content_sha256: str = ""
    storage_bucket: str | None = None
    object_key: str | None = None
    sha256: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    indexed_at: datetime | None = None


class Snippet(_Frozen):
    id: int | None = None
    document_id: int
    ord: int = Field(ge=0)
    text: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    text_sha256: str


class ScoredSnippet(_Frozen):
    snippet_id: int
    document_id: int
    text: str
    score: float = Field(ge=0.0, le=1.0)


class Source(_Frozen):
    document_id: int
    quote: str


class AskResult(_Frozen):
    answer: str
    sources: list[Source]
    needs_review: bool


class AiSource(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    quote: str


class AiAnswer(BaseModel):
    """Strict schema of the LLM JSON answer (additionalProperties=false)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    answer: str
    sources: list[AiSource]
    confidence: Literal["high", "medium", "low"]
    needs_review: bool


class QaRun(_Frozen):
    id: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    question: str
    answer: str
    sources: list[Source] = Field(default_factory=list)
    needs_review: bool
    error: ReviewReason | None = None
    confidence: str | None = None
    chat_model: str | None = None
    embedding_model: str | None = None
    retrieval_mode: Literal["hybrid", "fts_only"] = "hybrid"
    retrieved_ids: list[int] = Field(default_factory=list)


AuditStatus = Literal["ok", "needs_review", "client_error", "error"]


class AuditRecord(_Frozen):
    created_at: datetime = Field(default_factory=utcnow)
    action: str = Field(min_length=1, max_length=100)
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    status: AuditStatus
    error: str | None = None
    duration_ms: int = Field(ge=0)
    request_id: UUID = Field(default_factory=uuid4)


class StoredObject(_Frozen):
    bucket: str
    key: str
    size_bytes: int
    sha256: str
    mime_type: str


EmbeddingModelStatus = Literal["indexing", "ready", "active", "retired", "failed"]
EMBEDDING_ERROR_MAX = 2000
EMBEDDING_DIM_MAX = 4000


class EmbeddingModelInfo(_Frozen):
    """Row of the embedding-model registry (blue/green reindex: indexing -> ready -> active -> retired)."""

    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=50)
    dim: int = Field(gt=0, le=EMBEDDING_DIM_MAX)
    status: EmbeddingModelStatus = "indexing"
    created_at: datetime = Field(default_factory=utcnow)
    activated_at: datetime | None = None
    retired_at: datetime | None = None
    progress_done: int = 0
    progress_total: int = 0
    error: str | None = None
