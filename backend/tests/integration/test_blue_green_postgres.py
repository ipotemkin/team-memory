"""Blue/green reindex against real Postgres: new model with another dimension, HNSW, atomic switch."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.composition import build_container
from app.config import Settings
from tests.support import TEST_DB_URL, migrate_once, needs_pg, pg_backend

pytestmark = [pytest.mark.integration, needs_pg]


@pytest.fixture
async def container() -> AsyncIterator[Any]:
    migrate_once()
    async for _ in pg_backend():  # truncates before/after
        c = build_container(
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                DB_URL=str(TEST_DB_URL),
                DB_BACKEND="postgres",
                STORAGE_BACKEND="memory",
                QUEUE_BACKEND="inline",
            )
        )
        await c.run_startup()
        yield c
        await c.aclose()


async def test_blue_green_switch_with_new_dimension(container: Any) -> None:
    c = container
    await c.document_service().add("Правила", "Ежедневный стендап проходит в 11:00.\n\nРелиз по четвергам.")
    admin = c.admin_service()
    assert [(m.name, m.status) for m in await admin.list_models()] == [("stub:hash-64", "active")]
    await admin.register("stub:hash-24", "stub", 24)
    await admin.start_reindex("stub:hash-24")
    info = await admin.get("stub:hash-24")
    assert info.status == "ready" and info.progress_done == info.progress_total == 2

    before = await c.ask_service().ask("Во сколько проходит стендап?")
    assert before.needs_review is False
    await admin.activate("stub:hash-24")
    assert (await admin.get("stub:hash-64")).status == "retired"
    after = await c.ask_service().ask("Во сколько проходит стендап?")
    assert after.needs_review is False and c.query_embedder().dim == 24
    assert await admin.delete("stub:hash-64") == 2
