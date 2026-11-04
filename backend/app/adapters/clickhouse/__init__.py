"""Приёмник аудита в ClickHouse (clickhouse-connect, HTTP-интерфейс). AUDIT_BACKEND=clickhouse|both."""

from app.adapters.clickhouse.sink import DDL, ClickHouseAuditSink, ClickHouseProbe

__all__ = ["DDL", "ClickHouseAuditSink", "ClickHouseProbe"]
