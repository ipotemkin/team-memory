"""Приёмка этапа 7 на заглушках (memory-бэкенд, детерминированно): 4 критерия на tests_data/.

1. 5 документов добавляются через API и видны в списке (ready, нарезка по абзацам).
2. 7 вопросов с ответом → needs_review=false, источники из ожидаемого документа, дословные цитаты.
3. 3 вопроса вне базы → «Данных недостаточно…», needs_review=true, без источников, причина в qa_runs.
4. Каждая команда пишется в audit_runs (documents, ask, ai), у отрицательных — причина ревью.
"""

from typing import Any

import pytest

from app.domain.models import INSUFFICIENT_DATA
from tests.api.conftest import Api


@pytest.fixture
async def seeded(api: Api, kb_documents: list[dict[str, Any]]) -> dict[str, str]:
    ids = {}
    for d in kb_documents:
        r = await api.client.post("/kb/documents", json=d)
        assert r.status_code == 200, r.text
        ids[d["title"]] = r.json()["document_id"]
    return ids


def test_dataset_shape(kb_documents: list[dict[str, Any]], kb_questions: list[dict[str, Any]]) -> None:
    assert [d["title"] for d in kb_documents] == [
        "Правила работы команды",
        "Частые вопросы клиентов",
        "Шаблоны ответов",
        "Словарь терминов",
        "Процесс запуска задачи",
    ]
    for d in kb_documents:
        assert set(d) == {"title", "text"} and 20 <= len(d["text"].splitlines()) <= 50
    assert len(kb_questions) == 10
    for q in kb_questions:
        assert {"question", "expected_needs_review", "why"} <= set(q)
    pos = [q for q in kb_questions if not q["expected_needs_review"]]
    neg = [q for q in kb_questions if q["expected_needs_review"]]
    assert len(pos) == 7 and len(neg) == 3
    assert {q["expected_document"] for q in pos} == {d["title"] for d in kb_documents}
    texts = {d["title"]: d["text"].lower() for d in kb_documents}
    for q in pos:
        assert q["expected_quote"].lower() in texts[q["expected_document"]]
    blob = " ".join(texts.values())
    for stem in ("зарплат", "оклад", "парков", "компенсац", "отпуск"):
        assert stem not in blob, stem


async def test_criterion1_documents_listed(api: Api, seeded: dict[str, str]) -> None:
    lst = (await api.client.get("/kb/documents")).json()
    assert {d["title"]: d["id"] for d in lst} == seeded
    assert all(d["status"] == "ready" and d["status_error"] is None for d in lst)
    for doc_id in seeded.values():
        assert (await api.client.get(f"/kb/documents/{doc_id}")).json()["snippets_count"] >= 5


async def test_criterion2_answerable(
    api: Api, seeded: dict[str, str], kb_questions: list[dict[str, Any]]
) -> None:
    for q in (q for q in kb_questions if not q["expected_needs_review"]):
        body = (await api.client.post("/kb/ask", json={"question": q["question"]})).json()
        assert body["needs_review"] is False, (q["question"], body)
        assert body["sources"], q["question"]
        assert body["sources"][0]["document_id"] == seeded[q["expected_document"]], q["question"]
        assert q["expected_quote"].lower() in " ".join(s["quote"] for s in body["sources"]).lower()
        assert body["answer"] != INSUFFICIENT_DATA


async def test_criterion3_out_of_base(
    api: Api, seeded: dict[str, str], kb_questions: list[dict[str, Any]]
) -> None:
    neg = [q for q in kb_questions if q["expected_needs_review"]]
    for q in neg:
        body = (await api.client.post("/kb/ask", json={"question": q["question"]})).json()
        assert body == {"answer": INSUFFICIENT_DATA, "sources": [], "needs_review": True}, q["question"]
    runs = (await api.client.get("/kb/qa_runs?needs_review=true")).json()
    assert sorted((r["question"], r["error"]) for r in runs) == sorted(
        (q["question"], q["expected_reason"]) for q in neg
    )


async def test_criterion4_audit_every_command(
    api: Api, seeded: dict[str, str], kb_documents: list[dict[str, Any]], kb_questions: list[dict[str, Any]]
) -> None:
    for q in kb_questions:
        await api.client.post("/kb/ask", json={"question": q["question"]})
    ctx = next(d["text"] for d in kb_documents if d["title"] == "Правила работы команды")
    r = await api.client.post(
        "/ai/answer_with_sources",
        json={"question": "Во сколько проходит ежедневный стендап?", "context": ctx},
    )
    assert r.status_code == 200 and r.json()["sources"]
    audit = api.audit
    actions = [a["action"] for a in audit]
    assert actions.count("POST /kb/documents") == len(kb_documents)
    assert actions.count("POST /kb/ask") == len(kb_questions)
    assert actions.count("POST /ai/answer_with_sources") == 1
    asks = {a["input"]["question"]: a for a in audit if a["action"] == "POST /kb/ask"}  # type: ignore[index]
    for q in kb_questions:
        row = asks[q["question"]]
        if q["expected_needs_review"]:
            assert row["status"] == "needs_review" and row["error"] == q["expected_reason"]
        else:
            assert row["status"] == "ok" and row["error"] is None
