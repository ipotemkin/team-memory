"""Composition root: ЕДИНСТВЕННОЕ место, где адаптеры подключаются к портам.

По настройкам (DB_BACKEND, STORAGE_BACKEND, QUEUE_BACKEND, LLM_PROVIDER, EMBEDDINGS_PROVIDER,
AUDIT_BACKEND...) выбираются реализации портов и собирается ``Container``, из которого API (main.py,
api/deps.py) и Celery-воркер получают готовые сервисы. Остальные модули адаптеры не импортируют —
это проверяет import-linter.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.adapters.celery_queue.health import RedisProbe
from app.adapters.celery_queue.queue import CeleryTaskQueue
from app.adapters.clickhouse import ClickHouseAuditSink, ClickHouseProbe
from app.adapters.extractors import build_extractors
from app.adapters.memory import InlineTaskQueue, MemoryAuditSink, MemoryFileStorage
from app.adapters.memory import build as build_memory
from app.adapters.openai_compat import OpenAICompatChatLLM, OpenAICompatClient, OpenAICompatEmbedder
from app.adapters.openai_compat.health import OpenAICompatProbe
from app.adapters.postgres import PostgresBackend
from app.adapters.postgres import build as build_postgres
from app.adapters.postgres.admin import bootstrap_embedding_model, ensure_audit_partitions
from app.adapters.postgres.health import PostgresProbe
from app.adapters.s3 import S3FileStorage
from app.adapters.s3.health import S3Probe
from app.adapters.stubs import StubChatLLM, StubEmbedder
from app.config import Settings
from app.domain.health import HealthProbe
from app.domain.models import EmbeddingModelInfo, Page
from app.domain.ports import (
    AuditSink,
    ChatLLM,
    Embedder,
    EmbeddingModelRegistry,
    FileStorage,
    Retriever,
    TaskQueue,
    UnitOfWork,
)
from app.services.admin import REINDEX_MODEL_TASK, AdminService
from app.services.ai_answer import AiAnswerService
from app.services.ask import AskService
from app.services.audit import AuditService
from app.services.documents import DocumentService
from app.services.embedding import ActiveEmbedder, EmbedderFactory
from app.services.extraction import TextExtractionService
from app.services.health import EmbedderProbe, HealthService, StaticProbe
from app.services.indexing import INDEX_TASK, IndexingService
from app.services.qa_runs import QaRunService


def infra_probes(s: Settings) -> list[HealthProbe]:
    """Пробы инфраструктуры для публичного /health: Postgres, S3/MinIO, Redis."""
    return [
        PostgresProbe(s.DB_URL),
        S3Probe(s.S3_ENDPOINT_URL, s.S3_ACCESS_KEY, s.S3_SECRET_KEY, s.S3_BUCKET, s.S3_REGION),
        RedisProbe(s.REDIS_URL),
    ]


def build_health_service(s: Settings) -> HealthService:
    """HealthService для /health (только инфраструктура, без внешних LLM)."""
    return HealthService(infra_probes(s))


log = logging.getLogger("app.composition")
Job = Callable[[], Awaitable[Any]]


@dataclass
class Container:
    """Контейнер зависимостей: готовые адаптеры + фабрики сервисов.

    Сервисы создаются на каждый вызов (они лёгкие, без состояния); общее состояние — в адаптерах.
    При наличии реестра моделей поиск и индексация используют ``ActiveEmbedder`` (активная модель
    реестра), а не модель из настроек напрямую.
    """

    uow_factory: Callable[[], UnitOfWork]
    retriever: Retriever
    storage: FileStorage
    embedder: Embedder
    chat: ChatLLM
    queue: TaskQueue
    extra_audit_sinks: list[AuditSink] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    closers: list[Job] = field(default_factory=list)
    startup: list[Job] = field(default_factory=list)  # например, регистрация модели эмбеддингов + HNSW-индекс
    maintenance: dict[str, Job] = field(default_factory=dict)  # периодические задачи (Celery beat)
    registry: EmbeddingModelRegistry | None = None  # реестр моделей эмбеддингов (админка, blue/green)
    embedder_factory: EmbedderFactory | None = None  # создаёт Embedder для зарегистрированной модели
    embedder_provider: str = "stub"
    health_probes: list[HealthProbe] = field(default_factory=list)  # дополнительные пробы /admin/health
    extraction: TextExtractionService | None = None  # форматы загрузки; по умолчанию из UPLOAD_FORMATS

    def __post_init__(self) -> None:
        if self.extraction is None:
            self.extraction = TextExtractionService(build_extractors(self.settings.upload_formats))
        self._active: ActiveEmbedder | None = None
        if self.registry is not None:
            self._active = ActiveEmbedder(self.embedder, self.registry, self.embedder_factory or stub_factory)
        if isinstance(self.queue, InlineTaskQueue):  # тесты / QUEUE_BACKEND=inline: индексация синхронно
            self.queue.register(INDEX_TASK, self._index_inline)
            self.queue.register(REINDEX_MODEL_TASK, self._reindex_inline)

    async def _index_inline(self, document_id: int) -> None:
        """Обработчик INDEX_TASK для inline-очереди."""
        await self.indexing_service().index(document_id)

    async def _reindex_inline(self, name: str) -> None:
        """Обработчик REINDEX_MODEL_TASK для inline-очереди; ошибка уже сохранена в реестре."""
        try:
            await self.admin_service().run_reindex_task(name)
        except Exception:  # noqa: BLE001 - как фоновая задача: ошибка сохранена в реестре
            logging.getLogger("app.admin").warning("inline reindex of %s failed", name)

    def query_embedder(self) -> Embedder:
        """Эмбеддер активной модели реестра (если реестра нет — модель из настроек)."""
        return self._active if self._active is not None else self.embedder

    def admin_service(self) -> AdminService:
        """AdminService (реестр моделей, blue/green).

        Raises:
            RuntimeError: реестр не настроен (API отвечает 503).
        """
        if self.registry is None or self._active is None:
            raise RuntimeError("embedding-model registry is not configured")
        active = self._active
        return AdminService(
            self.registry,
            self.uow_factory,
            active.for_model,
            self.queue,
            EmbeddingModelInfo(
                name=self.embedder.model_name, provider=self.embedder_provider, dim=self.embedder.dim
            ),
            batch_size=self.settings.REINDEX_BATCH_SIZE,
            on_switch=active.invalidate,
        )

    def admin_health_service(self) -> HealthService:
        """HealthService для /admin/health: все пробы + эмбеддер активной модели (таймаут 10 с)."""
        return HealthService([*self.health_probes, EmbedderProbe(self.query_embedder())], timeout=10.0)

    def document_service(self) -> DocumentService:
        """Сервис документов."""
        return DocumentService(self.uow_factory, self.storage, self.queue, self.extraction)

    def indexing_service(self) -> IndexingService:
        """Сервис индексации (эмбеддинги активной модели)."""
        return IndexingService(self.uow_factory, self.query_embedder())

    def qa_run_service(self) -> QaRunService:
        """Сервис истории вопросов и ответов."""
        return QaRunService(self.uow_factory)

    def audit_service(self) -> AuditService:
        """Сервис аудита (транзакционный sink в UoW + дополнительные sink'и)."""
        return AuditService(self.uow_factory, self.extra_audit_sinks)

    async def run_startup(self) -> None:
        """Выполняет стартовые задачи; ошибка любой из них логируется и не мешает запуску."""
        for job in self.startup:
            try:
                await job()
            except Exception:  # стартовые задачи не должны мешать API работать
                log.exception("startup job failed")

    async def aclose(self) -> None:
        """Закрывает клиенты и пулы соединений."""
        for close in self.closers:
            await close()

    def ask_service(self) -> AskService:
        """RAG-сервис /kb/ask (TOP_K, MIN_SCORE из настроек)."""
        return AskService(
            self.uow_factory,
            self.retriever,
            self.query_embedder(),
            self.chat,
            top_k=self.settings.TOP_K,
            min_score=self.settings.MIN_SCORE,
            extra_audit_sinks=self.extra_audit_sinks,
        )

    def ai_answer_service(self) -> AiAnswerService:
        """Сервис /ai/answer_with_sources."""
        return AiAnswerService(self.uow_factory, self.chat, self.extra_audit_sinks)


def stub_factory(info: EmbeddingModelInfo) -> Embedder:
    """Фабрика эмбеддеров по умолчанию: только провайдер stub.

    Raises:
        NotImplementedError: провайдер не stub.
    """
    if info.provider != "stub":
        raise NotImplementedError(f"embedding provider {info.provider!r} is not configured")
    return StubEmbedder(dim=info.dim, model_name=info.name)


def _unknown(kind: str, value: str) -> NotImplementedError:
    """Ошибка конфигурации: значение настройки без реализации адаптера."""
    return NotImplementedError(f"{kind}={value!r} adapter is not implemented")


class _Db(Protocol):
    """Общий интерфейс бэкендов БД (memory и Postgres): UoW, retriever, реестр моделей."""

    @property
    def retriever(self) -> Retriever: ...

    @property
    def registry(self) -> EmbeddingModelRegistry: ...

    def uow_factory(self) -> UnitOfWork: ...

    async def dispose(self) -> None: ...


def _db(s: Settings) -> _Db:
    """Бэкенд БД по DB_BACKEND.

    Raises:
        NotImplementedError: неизвестное значение.
    """
    if s.DB_BACKEND == "memory":
        return build_memory()
    if s.DB_BACKEND in ("postgres", "postgres_sqlalchemy"):
        return build_postgres(s.DB_URL, pool_size=s.DB_POOL_SIZE, rrf_k=s.RRF_K, audit=s.audit_to_postgres)
    raise _unknown("DB_BACKEND", s.DB_BACKEND)


def _storage(s: Settings) -> FileStorage:
    """Хранилище оригиналов файлов по STORAGE_BACKEND (memory или S3/MinIO)."""
    if s.STORAGE_BACKEND == "memory":
        return MemoryFileStorage()
    return S3FileStorage(
        s.S3_ENDPOINT_URL,
        s.S3_ACCESS_KEY,
        s.S3_SECRET_KEY,
        s.S3_BUCKET,
        s.S3_REGION,
        s.S3_PUBLIC_ENDPOINT_URL,
    )


def _queue(s: Settings) -> TaskQueue:
    """Очередь задач по QUEUE_BACKEND; Celery импортируется лениво (воркер тянет настройки при импорте)."""
    if s.QUEUE_BACKEND in ("memory", "inline"):
        return InlineTaskQueue()
    from app.adapters.celery_queue.worker import celery_app

    return CeleryTaskQueue(celery_app)


OPENAI_PROVIDERS = ("openai_compat", "openai", "ollama")


class _Clients:
    """Лениво создаваемые OpenAI-совместимые клиенты (LLM и эмбеддинги могут смотреть на разные серверы).

    Если адрес и ключ совпадают, LLM и эмбеддинги делят один клиент (один пул соединений).
    """

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._llm: OpenAICompatClient | None = None
        self._emb: OpenAICompatClient | None = None

    def _new(self, url: str, key: str) -> OpenAICompatClient:
        """Новый клиент с таймаутом и ретраями из настроек."""
        return OpenAICompatClient(url, key, self._s.OPENAI_TIMEOUT_S, self._s.OPENAI_MAX_RETRIES)

    def llm(self) -> OpenAICompatClient:
        """Клиент для ChatLLM (LLM_BASE_URL/LLM_API_KEY, по умолчанию OPENAI_*)."""
        if self._llm is None:
            s = self._s
            if self._emb is not None and (s.llm_base_url, s.llm_api_key) == (
                s.embed_base_url,
                s.embed_api_key,
            ):
                self._llm = self._emb
            else:
                self._llm = self._new(s.llm_base_url, s.llm_api_key)
        return self._llm

    def embed(self) -> OpenAICompatClient:
        """Клиент для эмбеддингов (EMBED_BASE_URL/EMBED_API_KEY, по умолчанию OPENAI_*)."""
        if self._emb is None:
            s = self._s
            if self._llm is not None and (s.llm_base_url, s.llm_api_key) == (
                s.embed_base_url,
                s.embed_api_key,
            ):
                self._emb = self._llm
            else:
                self._emb = self._new(s.embed_base_url, s.embed_api_key)
        return self._emb

    async def aclose(self) -> None:
        """Закрывает клиенты; общий клиент закрывается один раз."""
        for c in {id(c): c for c in (self._llm, self._emb) if c is not None}.values():
            await c.aclose()


def _clickhouse(s: Settings) -> ClickHouseAuditSink:
    """Приёмник аудита ClickHouse по настройкам CLICKHOUSE_*."""
    return ClickHouseAuditSink(
        s.CLICKHOUSE_HOST,
        s.CLICKHOUSE_PORT,
        s.CLICKHOUSE_USER,
        s.CLICKHOUSE_PASSWORD,
        s.CLICKHOUSE_DATABASE,
        s.CLICKHOUSE_TABLE,
        s.CLICKHOUSE_SECURE,
    )


def build_container(s: Settings) -> Container:
    """Собирает Container по настройкам.

    Raises:
        NotImplementedError: значение настройки без реализации адаптера.
    """
    db = _db(s)
    clients = _Clients(s)
    closers: list[Job] = [clients.aclose]

    embedder: Embedder
    if s.EMBEDDINGS_PROVIDER == "openai_compat":
        embedder = OpenAICompatEmbedder(
            clients.embed(), s.EMBEDDINGS_MODEL, s.EMBEDDINGS_DIM, s.EMBED_BATCH_SIZE
        )
    elif s.EMBEDDINGS_PROVIDER == "stub":
        embedder = StubEmbedder()
    else:
        raise _unknown("EMBEDDINGS_PROVIDER", s.EMBEDDINGS_PROVIDER)

    chat: ChatLLM
    probes: list[HealthProbe] = [
        StaticProbe("postgres", "memory backend") if s.DB_BACKEND == "memory" else PostgresProbe(s.DB_URL),
        StaticProbe("minio", "memory storage")
        if s.STORAGE_BACKEND == "memory"
        else S3Probe(s.S3_ENDPOINT_URL, s.S3_ACCESS_KEY, s.S3_SECRET_KEY, s.S3_BUCKET, s.S3_REGION),
        RedisProbe(s.REDIS_URL) if s.QUEUE_BACKEND == "celery" else StaticProbe("redis", "inline queue"),
    ]
    if s.LLM_PROVIDER == "openai_compat":
        chat = OpenAICompatChatLLM(
            clients.llm(), s.CHAT_MODEL, s.OPENAI_STRUCTURED_OUTPUTS, s.CHAT_TEMPERATURE
        )
        probes.append(OpenAICompatProbe("llm", clients.llm(), s.CHAT_MODEL))
    elif s.LLM_PROVIDER == "stub":
        chat = StubChatLLM()
        probes.append(StaticProbe("llm", "stub"))
    else:
        raise _unknown("LLM_PROVIDER", s.LLM_PROVIDER)

    # Эмбеддер для модели из реестра (blue/green может переключить провайдера/модель).
    def embedder_factory(info: EmbeddingModelInfo) -> Embedder:
        if info.provider in OPENAI_PROVIDERS:
            return OpenAICompatEmbedder(clients.embed(), info.name, info.dim, s.EMBED_BATCH_SIZE)
        return stub_factory(info)

    extra: list[AuditSink] = []
    for sink in s.audit_sinks:
        if sink == "memory":
            extra.append(MemoryAuditSink())
        elif sink not in ("db", "postgres"):  # транзакционный sink живёт внутри UoW
            raise _unknown("AUDIT_SINKS", sink)

    startup: list[Job] = []
    maintenance: dict[str, Job] = {}
    queue = _queue(s)

    if s.audit_to_clickhouse:
        ch = _clickhouse(s)
        extra.append(ch)
        probes.append(ClickHouseProbe(ch))
        startup.append(ch.ensure_schema)
        closers.append(ch.aclose)

    async def _reindex_all() -> int:
        """Ставит в очередь переиндексацию всех документов (после смены EMBEDDINGS_MODEL), пачками по 200."""
        n, offset = 0, 0
        while True:
            async with db.uow_factory() as uow:
                batch = await uow.documents.list(Page(limit=200, offset=offset))
            for d in batch:
                await queue.enqueue(INDEX_TASK, document_id=d.id)
            n += len(batch)
            offset += 200
            if len(batch) < 200:
                return n

    # Только для Postgres: регистрация модели из настроек (первой стартовой задачей) и партиции аудита.
    if isinstance(db, PostgresBackend):
        engine = db.engine
        provider = s.EMBEDDINGS_PROVIDER

        async def _bootstrap() -> str:
            return await bootstrap_embedding_model(engine, embedder.model_name, provider, embedder.dim)

        async def _partitions() -> int:
            return await ensure_audit_partitions(engine, s.AUDIT_PARTITIONS_MONTHS)

        startup.insert(0, _bootstrap)
        maintenance = {"ensure_audit_partitions": _partitions, "bootstrap_embedding_model": _bootstrap}
    maintenance["reindex_all"] = _reindex_all

    return Container(
        uow_factory=db.uow_factory,
        retriever=db.retriever,
        storage=_storage(s),
        embedder=embedder,
        chat=chat,
        queue=queue,
        extra_audit_sinks=extra,
        settings=s,
        closers=[*closers, db.dispose],
        startup=startup,
        maintenance=maintenance,
        registry=db.registry,
        embedder_factory=embedder_factory,
        embedder_provider=s.EMBEDDINGS_PROVIDER,
        health_probes=probes,
    )
