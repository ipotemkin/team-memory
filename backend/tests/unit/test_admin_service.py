"""Blue/green reindex on memory adapters: register -> reindex -> activate -> ask uses new model."""

import pytest

from app.composition import build_container
from app.config import Settings
from app.domain.errors import Conflict, NotFound


def mem() -> Settings:
    return Settings(_env_file=None, DB_BACKEND="memory", STORAGE_BACKEND="memory", QUEUE_BACKEND="inline")  # type: ignore[call-arg]


async def test_blue_green_flow() -> None:
    c = build_container(mem())
    docs = c.document_service()
    await docs.add("Правила", "Ежедневный стендап проходит в 11:00.\n\nРелиз по четвергам.")
    admin = c.admin_service()
    models = await admin.list_models()
    assert [(m.name, m.status) for m in models] == [("stub:hash-64", "active")]

    new = await admin.register("stub:hash-32", "stub", 32)
    assert new.status == "indexing"
    with pytest.raises(Conflict):
        await admin.activate("stub:hash-32")  # not reindexed yet
    task_id = await admin.start_reindex("stub:hash-32")  # inline queue runs reindex right away
    assert task_id
    info = await admin.get("stub:hash-32")
    assert info.status == "ready" and info.progress_done == info.progress_total > 0

    await docs.add("Ещё", "Отпуск согласуют за две недели.")  # indexed with the old (active) model
    act = await admin.activate("stub:hash-32")  # catch-up pass embeds the new document too
    assert act.status == "active"
    assert (await admin.get("stub:hash-64")).status == "retired"
    assert c.query_embedder().dim in (32, 64)
    await c.query_embedder().embed(["x"])
    assert c.query_embedder().model_name == "stub:hash-32" and c.query_embedder().dim == 32

    res = await c.ask_service().ask("Во сколько проходит стендап?")
    assert res.needs_review is False
    with pytest.raises(Conflict):
        await admin.delete("stub:hash-32")
    assert await admin.delete("stub:hash-64") > 0
    with pytest.raises(NotFound):
        await admin.get("stub:hash-64")


async def test_register_validation_and_unknown_provider() -> None:
    admin = build_container(mem()).admin_service()
    with pytest.raises(ValueError):
        await admin.register("x", "stub", 0)
    await admin.register("y", "nope", 8)
    with pytest.raises(NotImplementedError):
        await admin.reindex("y")


async def test_openai_provider_factory_uses_embed_base_url() -> None:
    c = build_container(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            DB_BACKEND="memory",
            STORAGE_BACKEND="memory",
            QUEUE_BACKEND="inline",
            EMBED_BASE_URL="http://ollama:11434/v1",
            EMBED_API_KEY="ollama",
        )
    )
    assert c.embedder_factory is not None
    from app.domain.models import EmbeddingModelInfo

    e = c.embedder_factory(EmbeddingModelInfo(name="bge-m3", provider="ollama", dim=1024))
    assert e.model_name == "bge-m3" and e.dim == 1024
    assert str(e._c._http.base_url) == "http://ollama:11434/v1/"  # type: ignore[attr-defined]
    await c.aclose()


class _BoomEmbedder:
    model_name = "boom"
    dim = 8

    def __init__(self) -> None:
        self.fail = True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding server down " + "!" * 5000)
        return [[1.0] * self.dim for _ in texts]


async def test_reindex_failure_is_persisted_and_cleared_on_restart() -> None:
    from app.domain.models import EMBEDDING_ERROR_MAX, EmbeddingModelInfo
    from app.services.admin import AdminService

    c = build_container(mem())
    await c.document_service().add("Правила", "Стендап в 11:00.")
    boom = _BoomEmbedder()
    assert c.registry is not None
    admin = AdminService(
        c.registry,
        c.uow_factory,
        lambda _i: boom,
        c.queue,
        EmbeddingModelInfo(name=c.embedder.model_name, provider="stub", dim=c.embedder.dim),
    )
    c.queue.register("reindex_embedding_model", admin.run_reindex_task)  # type: ignore[attr-defined]
    await admin.register("boom", "stub", 8)
    with pytest.raises(RuntimeError):
        await admin.start_reindex("boom")  # inline queue surfaces the task exception here
    info = await admin.get("boom")
    assert info.status == "failed" and info.error is not None
    assert (
        info.error.startswith("RuntimeError: embedding server down")
        and len(info.error) == EMBEDDING_ERROR_MAX
    )
    with pytest.raises(Conflict):
        await admin.activate("boom")

    boom.fail = False
    await admin.start_reindex("boom")  # reindex allowed from 'failed'; error cleared
    info = await admin.get("boom")
    assert info.status == "ready" and info.error is None


async def test_inline_container_reindex_failure_returns_task_and_marks_failed() -> None:
    c = build_container(mem())
    await c.document_service().add("Правила", "Стендап в 11:00.")
    admin = c.admin_service()
    await admin.register("y", "nope", 8)  # unknown provider -> embedder factory raises
    assert await admin.start_reindex("y")
    info = await admin.get("y")
    assert info.status == "failed" and "NotImplementedError" in (info.error or "")
