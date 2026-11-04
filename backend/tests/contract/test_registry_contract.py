"""Контракт EmbeddingModelRegistry (memory всегда; postgres при TEST_DATABASE_URL)."""

from collections.abc import Callable

import pytest

from app.domain.errors import Conflict, NotFound
from app.domain.models import NewDocument, Snippet
from app.domain.ports import EmbeddingModelRegistry, UnitOfWork


async def _snippets(uow_factory: Callable[[], UnitOfWork], n: int = 3) -> list[Snippet]:
    async with uow_factory() as uow:
        d = await uow.documents.add(NewDocument(title="T", text="x"))
        saved = await uow.snippets.replace_for_document(
            d.id,
            [
                Snippet(
                    document_id=d.id, ord=i, text=f"t{i}", char_start=0, char_end=2, text_sha256=f"{i:064d}"
                )
                for i in range(n)
            ],
        )
        await uow.commit()
    return saved


async def test_register_get_list_conflict(registry: EmbeddingModelRegistry) -> None:
    info = await registry.register("m1", "stub", 8)
    assert (info.name, info.provider, info.dim, info.status) == ("m1", "stub", 8, "indexing")
    assert (await registry.get("m1")) == info
    assert await registry.get("nope") is None
    with pytest.raises(Conflict):
        await registry.register("m1", "stub", 8)
    assert [i.name for i in await registry.list()] == ["m1"]


async def test_bootstrap_activates_only_when_none_active(registry: EmbeddingModelRegistry) -> None:
    assert await registry.bootstrap("a", "stub", 4) == "a"
    assert await registry.bootstrap("b", "stub", 4) == "a"
    act = await registry.active()
    assert act is not None and act.name == "a" and act.activated_at is not None
    assert (await registry.get("b")) is not None


async def test_progress_status_coverage(
    registry: EmbeddingModelRegistry, uow_factory: Callable[[], UnitOfWork]
) -> None:
    sn = await _snippets(uow_factory)
    await registry.register("m", "stub", 2)
    assert await registry.coverage("m") == (0, 3)
    async with uow_factory() as uow:
        await uow.snippets.upsert_embeddings("m", [(s.id or 0, s.text_sha256, [1.0, 0.0]) for s in sn[:2]])
        await uow.commit()
    assert await registry.coverage("m") == (2, 3)
    await registry.set_progress("m", 2, 3)
    await registry.set_status("m", "ready")
    got = await registry.get("m")
    assert got is not None and (got.progress_done, got.progress_total, got.status) == (2, 3, "ready")
    with pytest.raises(NotFound):
        await registry.set_status("zzz", "ready")


async def test_activate_switches_atomically_and_delete(
    registry: EmbeddingModelRegistry, uow_factory: Callable[[], UnitOfWork]
) -> None:
    sn = await _snippets(uow_factory, 1)
    await registry.bootstrap("old", "stub", 2)
    await registry.register("new", "stub", 2)
    async with uow_factory() as uow:
        await uow.snippets.upsert_embeddings("old", [(sn[0].id or 0, sn[0].text_sha256, [1.0, 0.0])])
        await uow.commit()
    await registry.activate("new")
    act = await registry.active()
    assert act is not None and act.name == "new"
    old = await registry.get("old")
    assert old is not None and old.status == "retired" and old.retired_at is not None
    assert [i.name for i in await registry.list() if i.status == "active"] == ["new"]
    with pytest.raises(Conflict):
        await registry.delete("new")
    assert await registry.delete("old") == 1
    assert await registry.get("old") is None
    with pytest.raises(NotFound):
        await registry.delete("old")
    with pytest.raises(NotFound):
        await registry.activate("ghost")


async def test_failed_status_and_error_roundtrip(registry: EmbeddingModelRegistry) -> None:
    await registry.register("f", "stub", 4)
    assert (await registry.get("f")).error is None  # type: ignore[union-attr]
    msg = "RuntimeError: " + "x" * 1990
    await registry.set_status("f", "failed", msg)
    got = await registry.get("f")
    assert got is not None and got.status == "failed" and got.error == msg
    assert [i.error for i in await registry.list() if i.name == "f"] == [msg]
    await registry.set_status("f", "indexing")  # restart clears the error
    got = await registry.get("f")
    assert got is not None and got.status == "indexing" and got.error is None
