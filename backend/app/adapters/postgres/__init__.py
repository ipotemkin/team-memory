"""PostgreSQL + pgvector adapter (SQLAlchemy 2.0 async, asyncpg)."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.postgres.registry import PostgresEmbeddingRegistry
from app.adapters.postgres.retriever import PostgresFtsRetriever, PostgresHybridRetriever
from app.adapters.postgres.uow import SqlAlchemyUnitOfWork


@dataclass
class PostgresBackend:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    retriever: PostgresHybridRetriever
    registry: PostgresEmbeddingRegistry
    audit_enabled: bool = True

    def uow_factory(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.session_factory, audit_enabled=self.audit_enabled)

    async def dispose(self) -> None:
        await self.engine.dispose()


def build(url: str, pool_size: int = 10, rrf_k: int = 60, audit: bool = True) -> PostgresBackend:
    engine = create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)
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
