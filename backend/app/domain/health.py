"""Модель состояния компонентов и порт проверки здоровья.

Адаптеры (postgres/s3/redis/clickhouse/openai_compat ``health.py``) реализуют ``HealthProbe``,
``services/health.py`` опрашивает их параллельно для ``/admin/health``.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ComponentStatus:
    """Результат проверки одного компонента: имя, доступен ли и пояснение."""

    name: str
    ok: bool
    detail: str = ""


class HealthProbe(Protocol):
    """Порт: проверяет доступность одного инфраструктурного компонента."""

    name: str

    async def check(self) -> ComponentStatus:
        """Выполняет проверку; не должен бросать исключения — ошибка возвращается как ``ok=False``."""
        ...
