"""Вспомогательные классы аудита: замер длительности, сборка AuditRecord, рассылка по приёмникам.

Основной (транзакционный) аудит пишется через ``uow.audit`` вместе с бизнес-данными; дополнительные
приёмники (например, ClickHouse при AUDIT_BACKEND=both) получают ту же запись через ``fan_out``.
"""

import time
from collections.abc import Callable
from typing import Any

from app.domain.models import AuditRecord, AuditStatus
from app.domain.ports import AuditSink, UnitOfWork


class AuditTimer:
    """Засекает время с момента создания и собирает ``AuditRecord`` для действия ``action``.

    Args:
        action: имя действия, например ``"POST /kb/ask"``.
        input: входные данные для записи аудита (без больших текстов).
    """

    def __init__(self, action: str, input: dict[str, Any] | None = None) -> None:
        self.action = action
        self.input = input
        self._t0 = time.perf_counter()

    @property
    def duration_ms(self) -> int:
        """Миллисекунды с момента создания таймера."""
        return max(0, int((time.perf_counter() - self._t0) * 1000))

    def record(
        self, status: AuditStatus, output: dict[str, Any] | None = None, error: str | None = None
    ) -> AuditRecord:
        """Создаёт запись аудита со статусом, выходом и ошибкой; длительность — на момент вызова."""
        return AuditRecord(
            action=self.action,
            input=self.input,
            output=output,
            status=status,
            error=error,
            duration_ms=self.duration_ms,
        )


async def fan_out(sinks: list[AuditSink], rec: AuditRecord) -> None:
    """Пишет запись в нетранзакционные дополнительные приёмники.

    Ошибка приёмника (например, недоступен ClickHouse) никогда не ломает запрос — она игнорируется.
    """
    for sink in sinks:
        try:
            await sink.record(rec)
        except Exception:  # noqa: BLE001, S110
            pass


class AuditService:
    """Пишет строку аудита в отдельной транзакции.

    Используется middleware API для запросов, которые не записал ни один сервис (ошибки валидации,
    404 и т.п.).
    """

    def __init__(self, uow_factory: Callable[[], UnitOfWork], extra: list[AuditSink] | None = None) -> None:
        self._uow = uow_factory
        self._extra = list(extra or [])

    async def record(self, rec: AuditRecord) -> None:
        """Записывает аудит в БД и в дополнительные приёмники."""
        async with self._uow() as uow:
            await uow.audit.record(rec)
            await uow.commit()
        await fan_out(self._extra, rec)
