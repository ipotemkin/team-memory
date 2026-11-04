"""In-memory адаптеры для тестов и локального запуска без БД (DB_BACKEND=memory).

Это эталонные реализации контрактов портов: те же контрактные тесты (``tests/contract``) гоняются
и для memory, и для Postgres/S3, поэтому поведение совпадает.
"""

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
    """Набор memory-адаптеров с общим состоянием; интерфейс совпадает с ``PostgresBackend``."""

    store: MemoryStore
    retriever: MemoryKeywordRetriever
    registry: MemoryEmbeddingRegistry

    def uow_factory(self) -> MemoryUnitOfWork:
        """Новый UnitOfWork поверх общего хранилища."""
        return MemoryUnitOfWork(self.store)

    async def dispose(self) -> None:
        """Освобождать нечего (совместимость с PostgresBackend)."""
        return None


def build() -> MemoryBackend:
    """Создаёт пустое in-memory хранилище и адаптеры поверх него."""
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
