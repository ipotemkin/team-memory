"""Порты гексагональной архитектуры (``typing.Protocol``).

Сервисы (``app/services``) зависят только от этих протоколов и доменных моделей; конкретные
реализации — адаптеры в ``app/adapters`` (postgres, memory, s3, openai_compat, stubs, celery_queue,
clickhouse, extractors). Какой адаптер подставить, решает composition root (``composition.py``)
по настройкам окружения. Протоколы структурные: адаптеру не нужно от них наследоваться.
"""

from __future__ import annotations

from typing import Any, Protocol, Self

from app.domain.models import (
    AiAnswer,
    AuditRecord,
    Document,
    DocumentStatus,
    EmbeddingModelInfo,
    NewDocument,
    Page,
    QaRun,
    ScoredSnippet,
    Snippet,
    StoredObject,
)


class DocumentRepository(Protocol):
    """Хранилище документов (таблица documents)."""

    async def add(self, doc: NewDocument) -> Document:
        """Сохраняет новый документ со статусом uploaded и возвращает его с id."""
        ...

    async def get(self, id: int) -> Document | None:
        """Документ по id или None."""
        ...

    async def list(self, page: Page) -> list[Document]:
        """Страница документов, новые первыми."""
        ...

    async def set_status(self, id: int, status: DocumentStatus, error: str | None = None) -> None:
        """Меняет статус документа; ``error`` — текст ошибки для статуса failed."""
        ...


class SnippetRepository(Protocol):
    """Хранилище фрагментов документов и их эмбеддингов (по модели)."""

    async def replace_for_document(self, document_id: int, snippets: list[Snippet]) -> list[Snippet]:
        """Атомарно заменяет все фрагменты документа новыми; возвращает их с id."""
        ...

    async def list_for_document(self, document_id: int) -> list[Snippet]:
        """Фрагменты документа в порядке ord."""
        ...

    async def upsert_embeddings(self, model: str, items: list[tuple[int, str, list[float]]]) -> int:
        """Сохраняет эмбеддинги модели ``model``.

        ``items`` — кортежи (snippet_id, text_sha256, вектор); sha фиксирует, для какой версии текста
        посчитан вектор. Возвращает число записанных строк.
        """
        ...

    async def missing_embeddings(self, model: str, batch: int) -> list[Snippet]:
        """До ``batch`` фрагментов, у которых нет актуального эмбеддинга модели ``model``."""
        ...


class Retriever(Protocol):
    """Поиск фрагментов по вопросу (гибридный: полнотекстовый + векторный)."""

    async def search(
        self, query: str, qvec: list[float] | None, model: str | None, k: int
    ) -> list[ScoredSnippet]:
        """Возвращает до ``k`` лучших фрагментов документов в статусе ready.

        Args:
            query: текст вопроса (для полнотекстового поиска).
            qvec: эмбеддинг вопроса или None — тогда только полнотекстовый поиск.
            model: имя модели эмбеддингов, чьи векторы использовать.
            k: сколько фрагментов вернуть.
        """
        ...


class QaRunRepository(Protocol):
    """История вопросов и ответов (таблица qa_runs)."""

    async def add(self, run: QaRun) -> QaRun:
        """Сохраняет запуск и возвращает его с id."""
        ...

    async def get(self, id: int) -> QaRun | None:
        """Запуск по id или None."""
        ...

    async def list(self, page: Page, needs_review: bool | None = None) -> list[QaRun]:
        """Страница запусков, новые первыми; ``needs_review`` — необязательный фильтр."""
        ...


class AuditSink(Protocol):
    """Приёмник записей аудита (Postgres, ClickHouse, оба или память)."""

    async def record(self, rec: AuditRecord) -> None:
        """Записывает одну запись аудита."""
        ...


class FileStorage(Protocol):
    """Хранилище оригиналов файлов (S3/MinIO или память)."""

    async def put(self, key: str, data: bytes, mime: str) -> StoredObject:
        """Кладёт объект и возвращает его описание (bucket, key, размер, sha256, mime)."""
        ...

    async def get(self, key: str) -> bytes:
        """Читает объект целиком."""
        ...

    async def presign(self, key: str, ttl_s: int = 900) -> str:
        """Временная (``ttl_s`` секунд) ссылка на скачивание объекта."""
        ...

    async def delete(self, key: str) -> None:
        """Удаляет объект (используется для отката при ошибке сохранения)."""
        ...


class Embedder(Protocol):
    """Модель эмбеддингов: имя и размерность вектора."""

    model_name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Считает эмбеддинги для списка текстов (порядок сохраняется)."""
        ...


class ChatLLM(Protocol):
    """Чат-модель, отвечающая на вопрос по контексту строгим JSON."""

    model_name: str

    async def answer_with_sources(self, question: str, context: str) -> AiAnswer:
        """Возвращает проверенный по схеме ``AiAnswer``.

        Raises:
            LLMError: LLM недоступна (таймаут, 5xx, нет ключа).
            LLMInvalidJSON: ответ не соответствует схеме.
        """
        ...


class TaskQueue(Protocol):
    """Очередь фоновых задач (Celery или выполнение «на месте» в памяти)."""

    async def enqueue(self, task: str, **kwargs: Any) -> str:
        """Ставит задачу ``task`` с именованными аргументами; возвращает id задачи."""
        ...


class UnitOfWork(Protocol):
    """Единица работы: репозитории, работающие в одной транзакции.

    Использование: ``async with uow: ...; await uow.commit()``. Без commit изменения откатываются.
    """

    documents: DocumentRepository
    snippets: SnippetRepository
    qa_runs: QaRunRepository
    audit: AuditSink  # транзакционный аудит (Postgres) или память

    async def __aenter__(self) -> Self:
        """Открывает транзакцию."""
        ...

    async def __aexit__(self, *exc: object) -> None:
        """Закрывает транзакцию; если commit не вызывали — откат."""
        ...

    async def commit(self) -> None:
        """Фиксирует транзакцию."""
        ...


class EmbeddingModelRegistry(Protocol):
    """Реестр моделей эмбеддингов и данные эмбеддингов по моделям (blue/green-переиндексация).

    Новая модель регистрируется (indexing), фоном считаются её векторы (ready), затем атомарно
    переключается активная модель (active), а старая становится retired. Поиск всегда идёт по
    активной модели, поэтому переиндексация не мешает ответам.
    """

    async def list(self) -> list[EmbeddingModelInfo]:
        """Все зарегистрированные модели."""
        ...

    async def get(self, name: str) -> EmbeddingModelInfo | None:
        """Модель по имени или None."""
        ...

    async def active(self) -> EmbeddingModelInfo | None:
        """Текущая активная модель или None."""
        ...

    async def register(self, name: str, provider: str, dim: int) -> EmbeddingModelInfo:
        """Регистрирует модель в статусе indexing.

        Raises:
            Conflict: модель с таким именем уже есть.
        """
        ...

    async def bootstrap(self, name: str, provider: str, dim: int) -> str:
        """Регистрирует модель, если её нет; активирует, если активной модели нет.

        Вызывается при старте. Возвращает имя активной модели.
        """
        ...

    async def set_status(self, name: str, status: str, error: str | None = None) -> None:
        """Задаёт статус и ошибку одновременно (error=None стирает прошлую ошибку)."""
        ...

    async def set_progress(self, name: str, done: int, total: int) -> None:
        """Сохраняет прогресс переиндексации (готово / всего)."""
        ...

    async def coverage(self, name: str) -> tuple[int, int]:
        """(число фрагментов с актуальным эмбеддингом этой модели, всего фрагментов)."""
        ...

    async def activate(self, name: str) -> None:
        """Атомарно переводит текущую активную модель в retired и делает ``name`` активной."""
        ...

    async def delete(self, name: str) -> int:
        """Удаляет эмбеддинги модели, её индекс и строку реестра; возвращает число удалённых эмбеддингов."""
        ...

    async def ensure_index(self, name: str) -> None:
        """Создаёт векторный индекс модели (HNSW в Postgres), если его нет."""
        ...


class TextExtractor(Protocol):
    """Извлекает обычный текст из одного формата загружаемых файлов (txt, md, docx, pdf, ...).

    Новый формат = новый адаптер в ``adapters/extractors`` + регистрация (``ALL_EXTRACTORS``,
    список ``UPLOAD_FORMATS``). Выбор адаптера и проверки — в ``services/extraction.py``.
    """

    @property
    def format(self) -> str:
        """Короткое имя формата для UPLOAD_FORMATS, например "pdf"."""
        ...

    @property
    def mime_type(self) -> str:
        """Канонический MIME-тип, который сохраняется вместе с оригиналом."""
        ...

    def supports(self, extension: str, content_type: str) -> bool:
        """Подходит ли адаптер по расширению и MIME-типу.

        ``extension`` вида ".pdf" (в нижнем регистре, может быть ""), ``content_type`` без параметров.
        """
        ...

    def matches(self, data: bytes) -> bool:
        """Проверка сигнатуры (magic bytes): похоже ли содержимое на этот формат."""
        ...

    def extract(self, data: bytes) -> str:
        """Возвращает текст (абзацы разделены пустой строкой).

        Raises:
            UnsupportedFile: содержимое нельзя прочитать как этот формат (например, текст не в UTF-8).
            ExtractionFailed: файл повреждён или в нём нет текста.
        """
        ...
