"""In-memory adapters for tests and local runs (reference implementations of port contracts)."""

from dataclasses import dataclass

from app.adapters.memory.queue import InlineTaskQueue
from app.adapters.memory.registry import MemoryEmbeddingRegistry
from app.adapters.memory.repositories import MemoryAuditSink
from app.adapters.memory.retriever import MemoryKeywordRetriever
from app.adapters.memory.storage import MemoryFileStorage
from app.adapters.memory.store import MemoryStore
from app.adapters.memory.uow import MemoryUnitOfWork


@dataclass
class MemoryBackend:
    store: MemoryStore
    retriever: MemoryKeywordRetriever
    registry: MemoryEmbeddingRegistry

    def uow_factory(self) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self.store)

    async def dispose(self) -> None:
        return None


def build() -> MemoryBackend:
    store = MemoryStore()
    return MemoryBackend(
        store=store, retriever=MemoryKeywordRetriever(store), registry=MemoryEmbeddingRegistry(store)
    )


__all__ = [
    "InlineTaskQueue",
    "MemoryAuditSink",
    "MemoryBackend",
    "MemoryEmbeddingRegistry",
    "MemoryFileStorage",
    "MemoryKeywordRetriever",
    "MemoryStore",
    "MemoryUnitOfWork",
    "build",
]
