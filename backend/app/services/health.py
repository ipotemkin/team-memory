"""Health service: aggregates probes into an overall status."""

import asyncio
from collections.abc import Sequence

from app.domain.health import ComponentStatus, HealthProbe
from app.domain.ports import Embedder


class HealthService:
    def __init__(self, probes: Sequence[HealthProbe], timeout: float = 3.0) -> None:
        self._probes = list(probes)
        self._timeout = timeout

    async def _run(self, probe: HealthProbe) -> ComponentStatus:
        try:
            return await asyncio.wait_for(probe.check(), self._timeout)
        except Exception as exc:  # noqa: BLE001
            return ComponentStatus(probe.name, False, f"{type(exc).__name__}: {exc}")

    async def check(self) -> dict[str, object]:
        results = await asyncio.gather(*(self._run(p) for p in self._probes))
        overall = "ok" if all(r.ok for r in results) else "degraded"
        return {
            "status": overall,
            "components": {r.name: {"ok": r.ok, "detail": r.detail} for r in results},
        }


class EmbedderProbe:
    """Embeds a short text with the (active) embedder and checks the vector size."""

    def __init__(self, embedder: Embedder, name: str = "embedder") -> None:
        self.name = name
        self._e = embedder

    async def check(self) -> ComponentStatus:
        vec = (await self._e.embed(["ping"]))[0]
        ok = len(vec) == self._e.dim
        return ComponentStatus(self.name, ok, f"{self._e.model_name} dim={len(vec)}")


class StaticProbe:
    def __init__(self, name: str, detail: str, ok: bool = True) -> None:
        self.name = name
        self._st = ComponentStatus(name, ok, detail)

    async def check(self) -> ComponentStatus:
        return self._st
