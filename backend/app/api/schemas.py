"""HTTP DTOs (request/response schemas). Limits come from the domain."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import AiSource, DocumentStatus, Question, ReviewReason, Source, Text, Title


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentIn(_In):
    title: Title
    text: Text


class AskIn(_In):
    question: Question


class SourceOut(BaseModel):
    document_id: str = Field(examples=["42"])
    quote: str


def sources_out(sources: list[Source]) -> list[SourceOut]:
    return [SourceOut(document_id=str(s.document_id), quote=s.quote) for s in sources]


class DocumentCreated(BaseModel):
    status: Literal["ok"] = "ok"
    document_id: str = Field(examples=["42"])
    document_status: DocumentStatus = Field(
        description="indexing — индексация идёт в фоне (Celery); ready — уже готов (inline-очередь)"
    )


class DocumentListItem(BaseModel):
    id: str
    title: str
    created_at: datetime
    status: DocumentStatus
    status_error: str | None = None


class DocumentDetail(DocumentListItem):
    text: str | None
    lines_total: int
    lines_returned: int
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    download_url: str | None = None
    snippets_count: int


class AskOut(BaseModel):
    answer: str
    sources: list[SourceOut]
    needs_review: bool


class AiAnswerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    sources: list[AiSource]
    confidence: Literal["high", "medium", "low"]
    needs_review: bool


class QaRunOut(BaseModel):
    id: str
    created_at: datetime
    question: str
    answer: str
    sources: list[SourceOut]
    needs_review: bool
    error: ReviewReason | None
    confidence: str | None = None
    chat_model: str | None = None
    embedding_model: str | None = None
    retrieval_mode: str


class FieldError(BaseModel):
    field: str
    msg: str


class ErrorOut(BaseModel):
    error: str = Field(examples=["validation_error"])
    detail: str | list[FieldError] | None = None


ERRORS: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorOut, "description": "Invalid JSON body"},
    422: {"model": ErrorOut, "description": "Validation error"},
}
