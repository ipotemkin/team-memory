"""UnitOfWork на SQLAlchemy: одна AsyncSession и одна транзакция на каждый ``async with``."""

from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.postgres.repositories import (
    PgDocumentRepository,
    PgQaRunRepository,
    PgSnippetRepository,
    PostgresAuditSink,
)
from app.domain.models import AuditRecord
from app.domain.ports import AuditSink, DocumentRepository, QaRunRepository, SnippetRepository


class SqlAlchemyUnitOfWork:
    """Порт UnitOfWork для Postgres.

    Args:
        session_factory: фабрика сессий.
        audit_enabled: False — аудит не пишется в Postgres (подставляется NullAuditSink).
    """

    documents: DocumentRepository
    snippets: SnippetRepository
    qa_runs: QaRunRepository
    audit: AuditSink

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], audit_enabled: bool = True) -> None:
        self._factory = session_factory
        self._audit_enabled = audit_enabled
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        s = self._factory()  # транзакция начнётся автоматически с первого запроса
        self._session = s
        self.documents = PgDocumentRepository(s)
        self.snippets = PgSnippetRepository(s)
        self.qa_runs = PgQaRunRepository(s)
        self.audit = PostgresAuditSink(s) if self._audit_enabled else NullAuditSink()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Откатывает незафиксированное (после commit это no-op) и закрывает сессию."""
        if self._session is not None:
            await self._session.rollback()  # после commit ничего не делает
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        assert self._session is not None, "UnitOfWork not entered"
        await self._session.commit()


class NullAuditSink:
    """AUDIT_BACKEND=clickhouse: записи аудита уходят только во внешний приёмник, не в Postgres."""

    async def record(self, rec: AuditRecord) -> None:
        return None
