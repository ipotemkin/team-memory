"""Admin helpers: per-model partial HNSW index and audit partitions."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def create_hnsw_index(engine: AsyncEngine, model: str) -> str:
    """Create the partial HNSW index for a registered model (CONCURRENTLY, outside a transaction)."""
    async with engine.connect() as conn:
        ddl = (await conn.execute(text("SELECT app.hnsw_index_ddl(:m)"), {"m": model})).scalar_one()
    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await ac.execute(text(str(ddl)))
    return str(ddl)


async def ensure_audit_partitions(engine: AsyncEngine, months_ahead: int = 2) -> int:
    async with engine.begin() as conn:
        n = (
            await conn.execute(text("SELECT app.ensure_audit_partitions(:n)"), {"n": months_ahead})
        ).scalar_one()
    return int(n)


async def bootstrap_embedding_model(engine: AsyncEngine, name: str, provider: str, dim: int) -> str:
    """Register the configured embedding model, make it active if none is, ensure its HNSW index.

    Returns the active model name. If another model is active, it is left alone (re-index
    and switch-over is an explicit admin operation) and its index is ensured instead.
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
