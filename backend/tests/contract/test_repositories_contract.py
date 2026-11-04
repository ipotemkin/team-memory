"""Контракт репозиториев и UoW: документы, фрагменты и эмбеддинги, qa_runs, транзакционный аудит."""

from collections.abc import Callable

import pytest

from app.domain.models import AuditRecord, DocumentStatus, Page, QaRun, ReviewReason, Source
from app.domain.ports import UnitOfWork
from tests.contract.helpers import new_doc, snip

UowF = Callable[[], UnitOfWork]


async def test_document_add_get_list_status(uow_factory: UowF) -> None:
    async with uow_factory() as uow:
        a = await uow.documents.add(new_doc("A"))
        b = await uow.documents.add(new_doc("B"))
        await uow.documents.set_status(a.id, DocumentStatus.FAILED, "boom")
        await uow.commit()
    assert a.id != b.id and a.status is DocumentStatus.UPLOADED
    async with uow_factory() as uow:
        got = await uow.documents.get(a.id)
        assert got is not None and got.title == "A" and got.status is DocumentStatus.FAILED
        assert got.status_error == "boom"
        assert await uow.documents.get(10_000) is None
        assert [d.id for d in await uow.documents.list(Page())] == [b.id, a.id]
        assert [d.id for d in await uow.documents.list(Page(limit=1, offset=1))] == [a.id]


async def test_uow_rollback_without_commit(uow_factory: UowF) -> None:
    async with uow_factory() as uow:
        await uow.documents.add(new_doc())
    with pytest.raises(RuntimeError):
        async with uow_factory() as uow:
            await uow.documents.add(new_doc())
            raise RuntimeError("fail")
    async with uow_factory() as uow:
        assert await uow.documents.list(Page()) == []


async def test_snippets_replace_and_embeddings(uow_factory: UowF) -> None:
    async with uow_factory() as uow:
        d = await uow.documents.add(new_doc())
        first = await uow.snippets.replace_for_document(d.id, [snip(d.id, 0, "a"), snip(d.id, 1, "b")])
        assert all(s.id is not None for s in first)
        assert (
            await uow.snippets.upsert_embeddings("m", [(first[0].id or 0, first[0].text_sha256, [1.0])]) == 1
        )
        missing = await uow.snippets.missing_embeddings("m", 10)
        assert [s.text for s in missing] == ["b"]
        second = await uow.snippets.replace_for_document(d.id, [snip(d.id, 0, "c")])
        assert [s.text for s in await uow.snippets.list_for_document(d.id)] == ["c"]
        assert [s.id for s in await uow.snippets.missing_embeddings("m", 10)] == [second[0].id]
        await uow.commit()


async def test_qa_runs_add_and_filter(uow_factory: UowF) -> None:
    async with uow_factory() as uow:
        ok = await uow.qa_runs.add(
            QaRun(question="q1", answer="a", sources=[Source(document_id=1, quote="x")], needs_review=False)
        )
        rv = await uow.qa_runs.add(
            QaRun(question="q2", answer="a", needs_review=True, error=ReviewReason.NO_SOURCES)
        )
        await uow.commit()
    assert ok.id is not None and rv.id is not None and ok.id != rv.id
    async with uow_factory() as uow:
        assert [r.id for r in await uow.qa_runs.list(Page())] == [rv.id, ok.id]
        assert [r.id for r in await uow.qa_runs.list(Page(), needs_review=True)] == [rv.id]
        assert [r.id for r in await uow.qa_runs.list(Page(), needs_review=False)] == [ok.id]
        got = await uow.qa_runs.get(rv.id)
        assert got is not None and got.error == ReviewReason.NO_SOURCES and got.question == "q2"
        assert await uow.qa_runs.get(10_000_000) is None


async def test_transactional_audit_commit_and_rollback(uow_factory: UowF) -> None:
    rec = AuditRecord(action="test", status="ok", duration_ms=1)
    async with uow_factory() as uow:
        await uow.audit.record(rec)  # no commit -> dropped
    async with uow_factory() as uow:
        await uow.audit.record(rec)
        await uow.commit()
