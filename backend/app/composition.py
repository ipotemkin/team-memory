"""Composition root: the ONLY place where adapters are wired to ports."""

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
    return [
        PostgresProbe(s.DB_URL),
        S3Probe(s.S3_ENDPOINT_URL, s.S3_ACCESS_KEY, s.S3_SECRET_KEY, s.S3_BUCKET, s.S3_REGION),
        RedisProbe(s.REDIS_URL),
    ]


def build_health_service(s: Settings) -> HealthService:
    return HealthService(infra_probes(s))


log = logging.getLogger("app.composition")
Job = Callable[[], Awaitable[Any]]


@dataclass
class Container:
    uow_factory: Callable[[], UnitOfWork]
    retriever: Retriever
    storage: FileStorage
    embedder: Embedder
    chat: ChatLLM
    queue: TaskQueue
    extra_audit_sinks: list[AuditSink] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    closers: list[Job] = field(default_factory=list)
    startup: list[Job] = field(default_factory=list)  # e.g. register embedding model + HNSW index
    maintenance: dict[str, Job] = field(default_factory=dict)  # periodic jobs (Celery beat)
    registry: EmbeddingModelRegistry | None = None  # embedding-model registry (admin, blue/green)
    embedder_factory: EmbedderFactory | None = None  # builds an Embedder for a registered model
    embedder_provider: str = "stub"
    health_probes: list[HealthProbe] = field(default_factory=list)  # extra /admin/health probes
    extraction: TextExtractionService | None = None  # upload formats; default from UPLOAD_FORMATS

    def __post_init__(self) -> None:
        if self.extraction is None:
            self.extraction = TextExtractionService(build_extractors(self.settings.upload_formats))
        self._active: ActiveEmbedder | None = None
        if self.registry is not None:
            self._active = ActiveEmbedder(self.embedder, self.registry, self.embedder_factory or stub_factory)
        if isinstance(self.queue, InlineTaskQueue):  # tests / QUEUE_BACKEND=inline: index synchronously
            self.queue.register(INDEX_TASK, self._index_inline)
            self.queue.register(REINDEX_MODEL_TASK, self._reindex_inline)

    async def _index_inline(self, document_id: int) -> None:
        await self.indexing_service().index(document_id)

    async def _reindex_inline(self, name: str) -> None:
        try:
            await self.admin_service().run_reindex_task(name)
        except Exception:  # noqa: BLE001 - like a background task: failure is persisted in the registry
            logging.getLogger("app.admin").warning("inline reindex of %s failed", name)

    def query_embedder(self) -> Embedder:
        """The embedder of the registry's active model (falls back to the configured one)."""
        return self._active if self._active is not None else self.embedder

    def admin_service(self) -> AdminService:
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
        return HealthService([*self.health_probes, EmbedderProbe(self.query_embedder())], timeout=10.0)

    def document_service(self) -> DocumentService:
        return DocumentService(self.uow_factory, self.storage, self.queue, self.extraction)

    def indexing_service(self) -> IndexingService:
        return IndexingService(self.uow_factory, self.query_embedder())

    def qa_run_service(self) -> QaRunService:
        return QaRunService(self.uow_factory)

    def audit_service(self) -> AuditService:
        return AuditService(self.uow_factory, self.extra_audit_sinks)

    async def run_startup(self) -> None:
        for job in self.startup:
            try:
                await job()
            except Exception:  # startup helpers must not prevent the API from serving
                log.exception("startup job failed")

    async def aclose(self) -> None:
        for close in self.closers:
            await close()

    def ask_service(self) -> AskService:
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
        return AiAnswerService(self.uow_factory, self.chat, self.extra_audit_sinks)


def stub_factory(info: EmbeddingModelInfo) -> Embedder:
    if info.provider != "stub":
        raise NotImplementedError(f"embedding provider {info.provider!r} is not configured")
    return StubEmbedder(dim=info.dim, model_name=info.name)


def _unknown(kind: str, value: str) -> NotImplementedError:
    return NotImplementedError(f"{kind}={value!r} adapter is not implemented")


class _Db(Protocol):
    @property
    def retriever(self) -> Retriever: ...

    @property
    def registry(self) -> EmbeddingModelRegistry: ...

    def uow_factory(self) -> UnitOfWork: ...

    async def dispose(self) -> None: ...


def _db(s: Settings) -> _Db:
    if s.DB_BACKEND == "memory":
        return build_memory()
    if s.DB_BACKEND in ("postgres", "postgres_sqlalchemy"):
        return build_postgres(s.DB_URL, pool_size=s.DB_POOL_SIZE, rrf_k=s.RRF_K, audit=s.audit_to_postgres)
    raise _unknown("DB_BACKEND", s.DB_BACKEND)


def _storage(s: Settings) -> FileStorage:
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
    if s.QUEUE_BACKEND in ("memory", "inline"):
        return InlineTaskQueue()
    from app.adapters.celery_queue.worker import celery_app

    return CeleryTaskQueue(celery_app)


OPENAI_PROVIDERS = ("openai_compat", "openai", "ollama")


class _Clients:
    """Lazily created OpenAI-compatible clients (LLM and embeddings may point at different servers)."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._llm: OpenAICompatClient | None = None
        self._emb: OpenAICompatClient | None = None

    def _new(self, url: str, key: str) -> OpenAICompatClient:
        return OpenAICompatClient(url, key, self._s.OPENAI_TIMEOUT_S, self._s.OPENAI_MAX_RETRIES)

    def llm(self) -> OpenAICompatClient:
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
        for c in {id(c): c for c in (self._llm, self._emb) if c is not None}.values():
            await c.aclose()


def _clickhouse(s: Settings) -> ClickHouseAuditSink:
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

    def embedder_factory(info: EmbeddingModelInfo) -> Embedder:
        if info.provider in OPENAI_PROVIDERS:
            return OpenAICompatEmbedder(clients.embed(), info.name, info.dim, s.EMBED_BATCH_SIZE)
        return stub_factory(info)

    extra: list[AuditSink] = []
    for sink in s.audit_sinks:
        if sink == "memory":
            extra.append(MemoryAuditSink())
        elif sink not in ("db", "postgres"):  # transactional sink lives inside the UoW
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
        """Re-enqueue indexing of every document (after switching EMBEDDINGS_MODEL)."""
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
