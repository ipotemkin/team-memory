"""Контракт Retriever: ранжирование, нормированный score, нет совпадений, только ready-документы."""

from app.domain.models import DocumentStatus
from tests.contract.conftest import Backend
from tests.contract.helpers import new_doc, snip


async def _seed(b: Backend, ready: bool = True) -> int:
    async with b.uow_factory() as uow:
        d = await uow.documents.add(new_doc())
        await uow.snippets.replace_for_document(
            d.id,
            [
                snip(d.id, 0, "Ежедневный стендап проходит в 11:00."),
                snip(d.id, 1, "Отпуск согласуется с руководителем."),
            ],
        )
        if ready:
            await uow.documents.set_status(d.id, DocumentStatus.READY)
        await uow.commit()
    return d.id


async def test_search_ranks_and_normalizes(backend: Backend) -> None:
    doc_id = await _seed(backend)
    res = await backend.retriever.search("Когда стендап?", None, None, 5)
    assert res and res[0].document_id == doc_id and "стендап" in res[0].text
    assert all(0.0 <= r.score <= 1.0 for r in res)
    assert res == sorted(res, key=lambda r: -r.score)
    assert len(await backend.retriever.search("стендап отпуск", None, None, 1)) == 1


async def test_search_no_match_and_not_ready(backend: Backend) -> None:
    await _seed(backend, ready=False)
    assert await backend.retriever.search("стендап", None, None, 5) == []
    assert await backend.retriever.search("зарплата тимлида", None, None, 5) == []
