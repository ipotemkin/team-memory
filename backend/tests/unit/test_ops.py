"""Stage 8 ops: settings for Ollama/ClickHouse, JSON logging, ClickHouse sink with a fake client."""

import json
import logging
import uuid
from typing import Any

from app.adapters.clickhouse import ClickHouseAuditSink
from app.adapters.openai_compat import OpenAICompatChatLLM, OpenAICompatEmbedder
from app.composition import build_container
from app.config import Settings
from app.domain.models import AuditRecord
from app.observability import JsonFormatter, request_id_var


def mem(**kw: Any) -> Settings:
    base: dict[str, Any] = {"DB_BACKEND": "memory", "STORAGE_BACKEND": "memory", "QUEUE_BACKEND": "inline"}
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[call-arg]


def test_defaults() -> None:
    s = mem()
    assert s.AUDIT_BACKEND == "postgres" and s.ADMIN_TOKEN == "" and s.METRICS_ENABLED is False
    assert s.llm_base_url == s.embed_base_url == s.OPENAI_BASE_URL


async def test_ollama_profile_wiring() -> None:
    c = build_container(
        mem(
            LLM_PROVIDER="openai_compat",
            EMBEDDINGS_PROVIDER="openai_compat",
            LLM_BASE_URL="http://localhost:11434/v1",
            EMBED_BASE_URL="http://localhost:11434/v1",
            LLM_API_KEY="ollama",
            EMBED_API_KEY="ollama",
            CHAT_MODEL="qwen2.5:7b",
            EMBEDDINGS_MODEL="bge-m3",
            EMBEDDINGS_DIM=1024,
        )
    )
    assert isinstance(c.chat, OpenAICompatChatLLM) and c.chat.model_name == "qwen2.5:7b"
    assert isinstance(c.embedder, OpenAICompatEmbedder) and c.embedder.dim == 1024
    assert c.chat._c is c.embedder._c  # same server+key -> one client
    assert str(c.embedder._c._http.base_url) == "http://localhost:11434/v1/"
    await c.aclose()


async def test_separate_llm_and_embed_servers() -> None:
    c = build_container(
        mem(
            LLM_PROVIDER="openai_compat",
            EMBEDDINGS_PROVIDER="openai_compat",
            OPENAI_API_KEY="k",
            EMBED_BASE_URL="http://localhost:11434/v1",
        )
    )
    assert c.chat._c is not c.embedder._c  # type: ignore[attr-defined]
    await c.aclose()


def test_audit_backend_clickhouse_adds_sink() -> None:
    c = build_container(mem(AUDIT_BACKEND="both"))
    assert any(isinstance(x, ClickHouseAuditSink) for x in c.extra_audit_sinks)
    assert any(p.name == "clickhouse" for p in c.health_probes)
    c2 = build_container(mem())
    assert not any(isinstance(x, ClickHouseAuditSink) for x in c2.extra_audit_sinks)


def test_postgres_uow_without_db_audit() -> None:
    from app.adapters.postgres.uow import NullAuditSink

    c = build_container(mem(DB_BACKEND="postgres", AUDIT_BACKEND="clickhouse"))
    uow: Any = c.uow_factory()
    assert uow._audit_enabled is False
    assert NullAuditSink is not None


class FakeCH:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []

    def command(self, sql: str) -> None:
        self.commands.append(sql)

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))

    def close(self) -> None:
        pass


async def test_clickhouse_sink_with_fake_client() -> None:
    fake = FakeCH()
    sink = ClickHouseAuditSink("h", database="kb", table="audit_runs", client=fake)
    await sink.ensure_schema()
    assert "CREATE TABLE IF NOT EXISTS kb.audit_runs" in fake.commands[1]
    rec = AuditRecord(action="ask", status="ok", duration_ms=5, input={"question": "стендап?"})
    await sink.record(rec)
    table, rows, cols = fake.inserts[0]
    assert table == "kb.audit_runs" and cols[0] == "created_at"
    row = dict(zip(cols, rows[0], strict=True))
    assert row["request_id"] == rec.request_id and json.loads(row["input"]) == {"question": "стендап?"}
    assert row["output"] == ""


def test_clickhouse_rejects_bad_identifiers() -> None:
    import pytest

    with pytest.raises(ValueError):
        ClickHouseAuditSink("h", table="x; DROP")


def test_json_formatter_includes_request_id_and_extra() -> None:
    rid = uuid.uuid4().hex
    tok = request_id_var.set(rid)
    try:
        rec = logging.LogRecord("app.x", logging.INFO, __file__, 1, "hello %s", ("мир",), None)
        rec.duration_ms = 7
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(tok)
    assert out["msg"] == "hello мир" and out["request_id"] == rid and out["duration_ms"] == 7
    assert out["level"] == "INFO" and out["logger"] == "app.x"
