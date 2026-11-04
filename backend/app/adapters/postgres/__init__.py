"""Адаптер PostgreSQL + pgvector (SQLAlchemy 2.0 async, драйвер asyncpg).

Реализует порты UnitOfWork (репозитории документов/фрагментов/qa_runs и транзакционный аудит),
Retriever (гибридный поиск FTS + векторы) и EmbeddingModelRegistry. Схема БД — в ``backend/migrations``.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.postgres.registry import PostgresEmbeddingRegistry
from app.adapters.postgres.retriever import PostgresFtsRetriever, PostgresHybridRetriever
from app.adapters.postgres.uow import SqlAlchemyUnitOfWork


@dataclass
class PostgresBackend:
    """Набор Postgres-адаптеров над общим пулом соединений; интерфейс совпадает с ``MemoryBackend``.

    ``audit_enabled=False`` (AUDIT_BACKEND=clickhouse) — аудит в Postgres не пишется.
    """

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    retriever: PostgresHybridRetriever
    registry: PostgresEmbeddingRegistry
    audit_enabled: bool = True

    def uow_factory(self) -> SqlAlchemyUnitOfWork:
        """Новый UnitOfWork (своя сессия и транзакция)."""
        return SqlAlchemyUnitOfWork(self.session_factory, audit_enabled=self.audit_enabled)

    async def dispose(self) -> None:
        """Закрывает пул соединений (при остановке приложения/воркера)."""
        await self.engine.dispose()


def build(url: str, pool_size: int = 10, rrf_k: int = 60, audit: bool = True) -> PostgresBackend:
    # pool_pre_ping: проверяем соединение перед выдачей из пула (переживает рестарт Postgres).
    """Создаёт движок SQLAlchemy и адаптеры.

    Args:
        url: DSN вида ``postgresql+asyncpg://...``.
        pool_size: размер пула соединений (DB_POOL_SIZE).
        rrf_k: константа Reciprocal Rank Fusion для гибридного поиска (RRF_K).
        audit: писать ли аудит в Postgres.
    """
    engine = create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)
    # expire_on_commit=False: объекты остаются читаемыми после commit (мы сразу маппим их в домен).
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return PostgresBackend(
        engine=engine,
        session_factory=factory,
        retriever=PostgresHybridRetriever(engine, rrf_k),
        registry=PostgresEmbeddingRegistry(engine),
        audit_enabled=audit,
    )


__all__ = [
    "PostgresBackend",
    "PostgresEmbeddingRegistry",
    "PostgresFtsRetriever",
    "PostgresHybridRetriever",
    "SqlAlchemyUnitOfWork",
    "build",
]
