"""Доменные модели (Pydantic v2) без зависимостей от инфраструктуры.

Используются всеми слоями: сервисы работают с ними, адаптеры (Postgres/memory) переводят их
в строки БД и обратно, API строит из них ответы. Модели неизменяемые (``frozen``) и не принимают
лишних полей (``extra="forbid"``).

Важно: docstring Pydantic-модели попадает в JSON-схему (description), поэтому пояснения к
моделям оставлены комментариями ``#``, а существующие docstring'и не менялись.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Лимиты валидации (совпадают с контрактом API и проверками в web/src/api/types.ts).
# --- validation limits (see architecture Page, "Контракты API") ---
TITLE_MAX = 200
TEXT_MAX = 100_000
QUESTION_MIN = 3
QUESTION_MAX = 2000
CONTEXT_MAX = 20_000
CONTEXT_PROMPT_LIMIT = 8000  # собранный RAG-контекст для ChatLLM
PAGE_LIMIT_MAX = 200
PAGE_LIMIT_DEFAULT = 50

# Стандартный ответ, когда ответить по источникам нельзя (needs_review=true).
INSUFFICIENT_DATA = "Данных недостаточно для ответа. Вопрос передан на ручную проверку."

# Строковые типы с обрезкой пробелов и ограничением длины.
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TEXT_MAX)]
Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=QUESTION_MIN, max_length=QUESTION_MAX)
]
Context = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CONTEXT_MAX)]


def utcnow() -> datetime:
    """Текущее время в UTC (aware datetime) — фабрика значений по умолчанию для created_at."""
    return datetime.now(UTC)


# Причина, по которой ответ отправлен на ручную проверку (поле error в qa_runs).
class ReviewReason(StrEnum):
    NO_SOURCES = "no_sources"
    LOW_CONFIDENCE = "low_confidence"
    UNVERIFIED_QUOTE = "unverified_quote"
    LLM_INVALID_JSON = "llm_invalid_json"
    LLM_ERROR = "llm_error"


# Старое имя, оставлено для совместимости импорта.
ReasonCode = ReviewReason


# Жизненный цикл документа: uploaded → indexing → ready (или failed с текстом ошибки).
class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


# База доменных моделей: неизменяемые, без лишних полей.
class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# Параметры пагинации списков (limit/offset).
class Page(_Frozen):
    limit: int = Field(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)


# Документ до сохранения. Поля хранилища (bucket/key/sha256/mime/size) заданы только для загруженных файлов.
class NewDocument(_Frozen):
    title: Title
    text: Text
    content_sha256: str = ""
    storage_bucket: str | None = None
    object_key: str | None = None
    sha256: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


# Сохранённый документ. text — извлечённый текст (по нему идёт индексация), оригинал файла лежит в S3.
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


# Фрагмент документа: дословный текст + позиция в документе; text_sha256 — для актуальности эмбеддинга.
class Snippet(_Frozen):
    id: int | None = None
    document_id: int
    ord: int = Field(ge=0)
    text: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    text_sha256: str


# Результат поиска: фрагмент и его score в [0, 1]. RRF задаёт только порядок, а score — лучшая
# из оценок релевантности (полнотекстовой и косинусной); с ним сравнивается MIN_SCORE.
class ScoredSnippet(_Frozen):
    snippet_id: int
    document_id: int
    text: str
    score: float = Field(ge=0.0, le=1.0)


# Источник ответа: документ и дословная цитата из него.
class Source(_Frozen):
    document_id: int
    quote: str


# Ответ сервиса /kb/ask пользователю.
class AskResult(_Frozen):
    answer: str
    sources: list[Source]
    needs_review: bool


# Цитата в ответе LLM (строгий режим: типы не приводятся).
class AiSource(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    quote: str


# Ответ LLM в формате structured output; схема строгая (additionalProperties=false).
# Строгая схема JSON-ответа LLM. Докстринг не переводим: он уходит в JSON-схему для модели.
class AiAnswer(BaseModel):
    """Strict schema of the LLM JSON answer (additionalProperties=false)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    answer: str
    sources: list[AiSource]
    confidence: Literal["high", "medium", "low"]
    needs_review: bool


# Запись истории вопросов-ответов (таблица qa_runs): что спросили, что ответили, какими моделями и на основе
# каких фрагментов.
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


# Статус записи аудита: ok / needs_review / ошибка клиента (4xx) / ошибка сервера.
AuditStatus = Literal["ok", "needs_review", "client_error", "error"]


# Запись аудита одного действия (HTTP-запрос или фоновая операция).
class AuditRecord(_Frozen):
    created_at: datetime = Field(default_factory=utcnow)
    action: str = Field(min_length=1, max_length=100)
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    status: AuditStatus
    error: str | None = None
    duration_ms: int = Field(ge=0)
    request_id: UUID = Field(default_factory=uuid4)


# Описание сохранённого в S3/MinIO объекта (оригинал файла).
class StoredObject(_Frozen):
    bucket: str
    key: str
    size_bytes: int
    sha256: str
    mime_type: str


# Статусы модели эмбеддингов для blue/green-переиндексации:
# indexing → ready → active → retired; failed — переиндексация упала (текст в error).
EmbeddingModelStatus = Literal["indexing", "ready", "active", "retired", "failed"]
EMBEDDING_ERROR_MAX = 2000
EMBEDDING_DIM_MAX = 4000


# Строка реестра моделей эмбеддингов вместе с прогрессом переиндексации.
# Строка реестра моделей эмбеддингов; жизненный цикл blue/green: indexing → ready → active → retired.
# Докстринг оставлен как есть: это описание схемы pydantic.
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
