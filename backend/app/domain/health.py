"""Health-check domain model and port."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    ok: bool
    detail: str = ""


class HealthProbe(Protocol):
    """Port: checks reachability of one infrastructure component."""

    name: str

    async def check(self) -> ComponentStatus: ...
