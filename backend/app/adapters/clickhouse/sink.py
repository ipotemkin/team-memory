"""ClickHouse AuditSink.

clickhouse-connect is synchronous; calls run in a worker thread (asyncio.to_thread).
The client is created lazily so the API starts even if ClickHouse is down (fan_out swallows errors).
"""

import asyncio
import json
import re
import threading
from typing import Any

from app.domain.health import ComponentStatus
from app.domain.models import AuditRecord

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DDL = """
CREATE TABLE IF NOT EXISTS {db}.{table}
(
    created_at   DateTime64(3, 'UTC'),
    request_id   UUID,
    action       LowCardinality(String),
    status       LowCardinality(String),
    error        Nullable(String),
    duration_ms  UInt32,
    input        String,
    output       String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (action, created_at)
TTL toDateTime(created_at) + INTERVAL 365 DAY
""".strip()

COLUMNS = ["created_at", "request_id", "action", "status", "error", "duration_ms", "input", "output"]


def _ident(v: str) -> str:
    if not _IDENT.match(v):
        raise ValueError(f"bad ClickHouse identifier: {v!r}")
    return v


class ClickHouseAuditSink:
    def __init__(
        self,
        host: str,
        port: int = 8123,
        username: str = "default",
        password: str = "",
        database: str = "kb",
        table: str = "audit_runs",
        secure: bool = False,
        client: Any = None,
    ) -> None:
        self._cfg: dict[str, Any] = dict(
            host=host, port=port, username=username, password=password, secure=secure
        )
        self.database = _ident(database)
        self.table = _ident(table)
        self._client = client
        self._lock = threading.Lock()

    def _get(self) -> Any:
        with self._lock:
            if self._client is None:
                import clickhouse_connect

                self._client = clickhouse_connect.get_client(**self._cfg, connect_timeout=3)
            return self._client

    @property
    def fqtn(self) -> str:
        return f"{self.database}.{self.table}"

    def _ensure_schema(self) -> None:
        c = self._get()
        c.command(f"CREATE DATABASE IF NOT EXISTS {self.database}")
        c.command(DDL.format(db=self.database, table=self.table))

    async def ensure_schema(self) -> None:
        await asyncio.to_thread(self._ensure_schema)

    @staticmethod
    def row(rec: AuditRecord) -> list[Any]:
        return [
            rec.created_at,
            rec.request_id,
            rec.action,
            rec.status,
            rec.error,
            rec.duration_ms,
            json.dumps(rec.input, ensure_ascii=False, default=str) if rec.input is not None else "",
            json.dumps(rec.output, ensure_ascii=False, default=str) if rec.output is not None else "",
        ]

    def _insert(self, rows: list[list[Any]]) -> None:
        self._get().insert(self.fqtn, rows, column_names=COLUMNS)

    async def record(self, rec: AuditRecord) -> None:
        await asyncio.to_thread(self._insert, [self.row(rec)])

    def _count(self, action: str | None) -> int:
        q = f"SELECT count() FROM {self.fqtn}"
        params: dict[str, Any] = {}
        if action is not None:
            q += " WHERE action = {a:String}"
            params["a"] = action
        return int(self._get().query(q, parameters=params).result_rows[0][0])

    async def count(self, action: str | None = None) -> int:
        return await asyncio.to_thread(self._count, action)

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)


class ClickHouseProbe:
    name = "clickhouse"

    def __init__(self, sink: ClickHouseAuditSink) -> None:
        self._sink = sink

    async def check(self) -> ComponentStatus:
        def _ping() -> bool:
            return bool(self._sink._get().ping())

        ok = await asyncio.to_thread(_ping)
        return ComponentStatus(self.name, ok, "ok" if ok else "ping failed")
