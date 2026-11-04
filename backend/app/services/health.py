"""HealthService — сводит результаты проверок компонентов в общий статус (``/admin/health``).

Пробы (порт HealthProbe) выполняются параллельно, каждая с таймаутом; упавшая или зависшая проба
даёт ``ok=False`` и общий статус ``degraded``, но не ошибку запроса.
"""

import asyncio
from collections.abc import Sequence

from app.domain.health import ComponentStatus, HealthProbe
from app.domain.ports import Embedder


class HealthService:
    """Агрегатор проверок здоровья.

    Args:
        probes: пробы компонентов (Postgres, S3, Redis, ClickHouse, LLM, эмбеддер...).
        timeout: таймаут одной пробы в секундах.
    """

    def __init__(self, probes: Sequence[HealthProbe], timeout: float = 3.0) -> None:
        self._probes = list(probes)
        self._timeout = timeout

    async def _run(self, probe: HealthProbe) -> ComponentStatus:
        """Запускает пробу с таймаутом; любое исключение превращается в ``ok=False`` с описанием."""
        try:
            return await asyncio.wait_for(probe.check(), self._timeout)
        except Exception as exc:  # noqa: BLE001
            return ComponentStatus(probe.name, False, f"{type(exc).__name__}: {exc}")

    async def check(self) -> dict[str, object]:
        """Опрашивает все пробы параллельно.

        Returns:
            ``{"status": "ok" | "degraded", "components": {имя: {"ok": bool, "detail": str}}}``.
        """
        results = await asyncio.gather(*(self._run(p) for p in self._probes))
        overall = "ok" if all(r.ok for r in results) else "degraded"
        return {
            "status": overall,
            "components": {r.name: {"ok": r.ok, "detail": r.detail} for r in results},
        }


class EmbedderProbe:
    """Проба эмбеддера: считает вектор для короткого текста активной моделью и сверяет размерность."""

    def __init__(self, embedder: Embedder, name: str = "embedder") -> None:
        self.name = name
        self._e = embedder

    async def check(self) -> ComponentStatus:
        vec = (await self._e.embed(["ping"]))[0]
        ok = len(vec) == self._e.dim
        return ComponentStatus(self.name, ok, f"{self._e.model_name} dim={len(vec)}")


class StaticProbe:
    """Проба с заранее известным результатом (например, «stub» или «отключено» в настройках)."""

    def __init__(self, name: str, detail: str, ok: bool = True) -> None:
        self.name = name
        self._st = ComponentStatus(name, ok, detail)

    async def check(self) -> ComponentStatus:
        return self._st
