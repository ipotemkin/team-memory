import math
from typing import Any

import pytest

from app.domain.errors import NotFound
from app.domain.models import AiAnswer, AuditRecord
from app.domain.ports import AuditSink, ChatLLM, Embedder, FileStorage, TaskQueue


async def test_file_storage_roundtrip(storage: FileStorage) -> None:
    obj = await storage.put("a/b.txt", b"hello", "text/plain")
    assert obj.key == "a/b.txt" and obj.size_bytes == 5 and len(obj.sha256) == 64
    assert await storage.get("a/b.txt") == b"hello"
    assert isinstance(await storage.presign("a/b.txt", 60), str)
    await storage.delete("a/b.txt")
    with pytest.raises(NotFound):
        await storage.get("a/b.txt")
    await storage.delete("a/b.txt")  # idempotent


async def test_task_queue_enqueue_returns_id(queue: TaskQueue) -> None:
    seen: list[dict[str, Any]] = []
    register = getattr(queue, "register", None)
    if register is not None:
        register("t", lambda **kw: seen.append(kw))
    a = await queue.enqueue("t", x=1)
    b = await queue.enqueue("t", x=2)
    assert a and b and a != b
    if register is not None:
        assert seen == [{"x": 1}, {"x": 2}]


async def test_embedder_shape_and_determinism(embedder: Embedder) -> None:
    v1, v2 = await embedder.embed(["стендап в 11", "стендап в 11"])
    assert len(v1) == embedder.dim and v1 == v2
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, rel_tol=1e-6)
    assert embedder.model_name


async def test_chat_returns_validated_answer(chat: ChatLLM) -> None:
    ans = await chat.answer_with_sources(
        "Во сколько стендап?", "[S1] (doc 1) Ежедневный стендап проходит в 11:00."
    )
    assert isinstance(ans, AiAnswer)
    assert chat.model_name


async def test_audit_sink_accepts_records(audit_sink: AuditSink) -> None:
    await audit_sink.record(AuditRecord(action="x", status="ok", duration_ms=0))


async def test_audit_sink_is_readable_when_supported(audit_sink: AuditSink) -> None:
    """Sinks that expose count() (ClickHouse) must store what they accept."""
    count = getattr(audit_sink, "count", None)
    if count is None:
        pytest.skip("sink has no count()")
    await audit_sink.record(AuditRecord(action="contract.x", status="ok", duration_ms=3, input={"q": "й"}))
    await audit_sink.record(AuditRecord(action="contract.y", status="error", error="e", duration_ms=0))
    assert await count("contract.x") == 1
    assert await count() == 2
