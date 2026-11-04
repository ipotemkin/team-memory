"""Асинхронное окружение Alembic (asyncpg).

URL берётся из ``sqlalchemy.url`` (alembic.ini / -x) или из настроек приложения (DB_URL). Миграции
пишутся сырым SQL, поэтому ``target_metadata`` нет (автогенерация не используется); таблица версий —
в схеме public.
"""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings


def _url() -> str:
    """URL базы: из конфигурации Alembic, иначе DB_URL приложения."""
    return context.config.get_main_option("sqlalchemy.url") or get_settings().DB_URL


target_metadata = None


def _do_run(conn: Connection) -> None:
    """Запускает миграции на синхронном соединении (внутри ``run_sync``)."""
    context.configure(connection=conn, target_metadata=target_metadata, version_table_schema="public")
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    """Онлайн-режим: async-движок, миграции через ``run_sync``."""
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


# Офлайн-режим (alembic upgrade --sql) печатает SQL без подключения к БД.
if context.is_offline_mode():
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
