"""Celery wiring: task names, retries, beat schedule; tasks executed via the worker's own loop."""

from typing import Any

import pytest

from app.adapters.celery_queue import worker
from app.composition import build_container
from app.config import Settings
from app.domain.models import DocumentStatus


@pytest.fixture
def mem_container(monkeypatch: pytest.MonkeyPatch) -> Any:
    c = build_container(
        Settings(_env_file=None, DB_BACKEND="memory", STORAGE_BACKEND="memory", QUEUE_BACKEND="memory")  # type: ignore[call-arg]
    )
    monkeypatch.setattr(worker, "_container", c)
    return c


def test_tasks_and_beat_registered() -> None:
    names = set(worker.celery_app.tasks)
    assert {
        "index_document",
        "ensure_audit_partitions",
        "bootstrap_embedding_model",
        "reindex_all",
        "ping",
    } <= names
    beat = worker.celery_app.conf.beat_schedule
    assert beat["ensure-audit-partitions"]["task"] == "ensure_audit_partitions"
    assert worker.index_document.max_retries == 3


def test_index_document_task_runs_indexing(mem_container: Any) -> None:
    from app.adapters.memory.queue import InlineTaskQueue

    assert isinstance(mem_container.queue, InlineTaskQueue)
    mem_container.queue._handlers.clear()  # simulate Celery: enqueue does nothing, worker indexes
    doc = worker.run(mem_container.document_service().add("T", "Ежедневный стендап проходит в 11:00."))
    assert doc.status == DocumentStatus.INDEXING
    assert worker.index_document.apply(kwargs={"document_id": doc.id}).get() == "ready"
    assert worker.run(mem_container.document_service().get(doc.id)).status == DocumentStatus.READY


def test_maintenance_task_noop_on_memory(mem_container: Any) -> None:
    assert worker.ensure_audit_partitions_task.apply().get() == 0


def test_reindex_all_task(mem_container: Any) -> None:
    worker.run(mem_container.document_service().add("A", "Текст один."))
    worker.run(mem_container.document_service().add("B", "Текст два."))
    assert worker.reindex_all_task.apply().get() == 2


def test_reindex_embedding_model_task(mem_container: Any) -> None:
    assert "reindex_embedding_model" in worker.celery_app.tasks
    worker.run(mem_container.document_service().add("A", "Текст один."))
    worker.run(mem_container.admin_service().register("stub:hash-8", "stub", 8))
    assert worker.reindex_embedding_model_task.apply(kwargs={"name": "stub:hash-8"}).get() >= 1
    assert worker.run(mem_container.admin_service().get("stub:hash-8")).status == "ready"
