"""Проба доступности Postgres (``SELECT 1``) для ``/admin/health``."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.health import ComponentStatus


class PostgresProbe:
    """Порт HealthProbe для Postgres; использует отдельный движок, чтобы не занимать основной пул."""

    name = "postgres"

    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url, pool_pre_ping=True)

    async def check(self) -> ComponentStatus:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return ComponentStatus(self.name, True)
