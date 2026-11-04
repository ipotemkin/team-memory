"""Фикстуры контрактов портов, параметризованные по реализациям.

memory/stub запускаются всегда; postgres и s3 помечены ``integration`` и идут только при
TEST_DATABASE_URL / TEST_S3_ENDPOINT_URL (см. ``make test-integration``).
Каждый Postgres-тест начинается и заканчивается TRUNCATE ... RESTART IDENTITY; каждый S3-тест
использует свой бакет, который затем очищается и удаляется.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import pytest

from app.adapters.memory import InlineTaskQueue, MemoryAuditSink, MemoryFileStorage
from app.adapters.memory import build as build_memory
from app.adapters.stubs import StubChatLLM, StubEmbedder
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
from tests.support import ch_sink, migrate_once, needs_ch, needs_pg, needs_s3, pg_backend, s3_storage

integration = pytest.mark.integration

BACKENDS = ["memory", pytest.param("postgres", marks=[integration, needs_pg])]
STORAGES = ["memory", pytest.param("s3", marks=[integration, needs_s3])]
QUEUES = ["inline"]
EMBEDDERS = ["stub"]
CHATS = ["stub"]
AUDIT_SINKS = [
    "memory",
    pytest.param("postgres", marks=[integration, needs_pg]),
    pytest.param("clickhouse", marks=[integration, needs_ch]),
]


@dataclass
class Backend:
    uow_factory: Callable[[], UnitOfWork]
    retriever: Retriever
    registry: EmbeddingModelRegistry


@pytest.fixture(params=BACKENDS)
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    if request.param == "memory":
        b = build_memory()
        yield Backend(uow_factory=b.uow_factory, retriever=b.retriever, registry=b.registry)
        return
    migrate_once()
    async for pg in pg_backend():
        p: Any = pg
        yield Backend(uow_factory=p.uow_factory, retriever=p.retriever, registry=p.registry)


@pytest.fixture
def uow_factory(backend: Backend) -> Callable[[], UnitOfWork]:
    return backend.uow_factory


@pytest.fixture(params=STORAGES)
async def storage(request: pytest.FixtureRequest) -> AsyncIterator[FileStorage]:
    if request.param == "memory":
        yield MemoryFileStorage()
        return
    async for st in s3_storage():
        s: Any = st
        yield s


@pytest.fixture(params=QUEUES)
def queue(request: pytest.FixtureRequest) -> TaskQueue:
    return InlineTaskQueue()


@pytest.fixture(params=EMBEDDERS)
def embedder(request: pytest.FixtureRequest) -> Embedder:
    return StubEmbedder()


@pytest.fixture(params=CHATS)
def chat(request: pytest.FixtureRequest) -> ChatLLM:
    return StubChatLLM()


@pytest.fixture(params=AUDIT_SINKS)
async def audit_sink(request: pytest.FixtureRequest) -> AsyncIterator[AuditSink]:
    if request.param == "memory":
        yield MemoryAuditSink()
        return
    if request.param == "clickhouse":
        async for ch in ch_sink():
            c: Any = ch
            yield c
        return
    migrate_once()
    async for pg in pg_backend():
        p: Any = pg
        async with p.uow_factory() as uow:
            yield uow.audit
            await uow.commit()


@pytest.fixture
def registry(backend: Backend) -> EmbeddingModelRegistry:
    return backend.registry
