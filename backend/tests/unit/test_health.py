"""Health-сервис: live, ok и degraded при недоступных зависимостях."""

from fastapi.testclient import TestClient

from app.domain.health import ComponentStatus
from app.main import create_app
from app.services.health import HealthService


class OkProbe:
    def __init__(self, name: str) -> None:
        self.name = name

    async def check(self) -> ComponentStatus:
        return ComponentStatus(self.name, True)


class DownProbe(OkProbe):
    async def check(self) -> ComponentStatus:
        raise ConnectionError("unreachable")


def test_live() -> None:
    client = TestClient(create_app(health_service=HealthService([])))
    r = client.get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_health_ok() -> None:
    svc = HealthService([OkProbe("postgres"), OkProbe("minio"), OkProbe("redis")])
    body = TestClient(create_app(health_service=svc)).get("/health").json()
    assert body["status"] == "ok"


def test_health_degraded_when_deps_down() -> None:
    svc = HealthService([DownProbe("postgres"), OkProbe("minio"), DownProbe("redis")])
    body = TestClient(create_app(health_service=svc)).get("/health").json()
    assert body["status"] == "degraded"
    assert body["components"]["postgres"]["ok"] is False
    assert "unreachable" in body["components"]["postgres"]["detail"]
    assert body["components"]["minio"]["ok"] is True
