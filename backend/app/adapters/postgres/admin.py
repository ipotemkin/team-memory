"""Служебные операции над схемой: частичный HNSW-индекс модели эмбеддингов и партиции аудита.

DDL генерируют SQL-функции из миграции 0002 (``app.hnsw_index_ddl``, ``app.ensure_audit_partitions``),
здесь они только вызываются.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def create_hnsw_index(engine: AsyncEngine, model: str) -> str:
    """Создаёт частичный HNSW-индекс для модели (CONCURRENTLY, вне транзакции).

    Индекс строится по выражению ``embedding::vector(dim)`` с условием ``WHERE embedding_model = model``:
    у каждой модели своя размерность, а одна колонка ``vector`` без размерности хранит векторы всех
    моделей.
    CREATE INDEX CONCURRENTLY нельзя выполнять в транзакции, поэтому используется AUTOCOMMIT.

    Returns:
        Выполненный DDL (для логов).
    """
    async with engine.connect() as conn:
        ddl = (await conn.execute(text("SELECT app.hnsw_index_ddl(:m)"), {"m": model})).scalar_one()
    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await ac.execute(text(str(ddl)))
    return str(ddl)


async def ensure_audit_partitions(engine: AsyncEngine, months_ahead: int = 2) -> int:
    """Создаёт месячные партиции ``app.audit_runs`` на ``months_ahead`` месяцев вперёд.

    Вызывается при старте и периодически Celery beat. Возвращает число созданных партиций.
    """
    async with engine.begin() as conn:
        n = (
            await conn.execute(text("SELECT app.ensure_audit_partitions(:n)"), {"n": months_ahead})
        ).scalar_one()
    return int(n)


async def bootstrap_embedding_model(engine: AsyncEngine, name: str, provider: str, dim: int) -> str:
    """Регистрирует модель из настроек, делает её активной, если активной нет, и создаёт её HNSW-индекс.

    Возвращает имя активной модели. Если активна другая модель, она не трогается (переиндексация и
    переключение — явная операция администратора), а индекс создаётся для неё.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO app.embedding_models (name, provider, dim) VALUES (:n, :p, :d) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"n": name, "p": provider, "d": dim},
        )
        active = (
            await conn.execute(text("SELECT name FROM app.embedding_models WHERE status = 'active'"))
        ).scalar_one_or_none()
        if active is None:
            await conn.execute(
                text(
                    "UPDATE app.embedding_models SET status = 'active', activated_at = now() WHERE name = :n"
                ),
                {"n": name},
            )
            active = name
    await create_hnsw_index(engine, str(active))
    return str(active)
