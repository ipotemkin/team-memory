"""Audit helper: measures duration_ms and builds AuditRecord."""

import time
from collections.abc import Callable
from typing import Any

from app.domain.models import AuditRecord, AuditStatus
from app.domain.ports import AuditSink, UnitOfWork


class AuditTimer:
    def __init__(self, action: str, input: dict[str, Any] | None = None) -> None:
        self.action = action
        self.input = input
        self._t0 = time.perf_counter()

    @property
    def duration_ms(self) -> int:
        return max(0, int((time.perf_counter() - self._t0) * 1000))

    def record(
        self, status: AuditStatus, output: dict[str, Any] | None = None, error: str | None = None
    ) -> AuditRecord:
        return AuditRecord(
            action=self.action,
            input=self.input,
            output=output,
            status=status,
            error=error,
            duration_ms=self.duration_ms,
        )


async def fan_out(sinks: list[AuditSink], rec: AuditRecord) -> None:
    """Non-transactional extra sinks (log/kafka): failures never break the request."""
    for sink in sinks:
        try:
            await sink.record(rec)
        except Exception:  # noqa: BLE001, S110
            pass


class AuditService:
    """Records an audit row in its own UoW (used by the API for requests no service audited)."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork], extra: list[AuditSink] | None = None) -> None:
        self._uow = uow_factory
        self._extra = list(extra or [])

    async def record(self, rec: AuditRecord) -> None:
        async with self._uow() as uow:
            await uow.audit.record(rec)
            await uow.commit()
        await fan_out(self._extra, rec)
