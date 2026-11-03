from collections.abc import AsyncIterator, Callable
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI

from app.adapters.memory import MemoryBackend, MemoryFileStorage
from app.adapters.memory import build as build_memory
from app.adapters.memory.queue import InlineTaskQueue
from app.adapters.stubs import StubChatLLM, StubEmbedder
from app.composition import Container
from app.config import Settings
from app.main import create_app
from app.services.health import HealthService


def memory_settings(**kw: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        DB_BACKEND="memory",
        STORAGE_BACKEND="memory",
        QUEUE_BACKEND="inline",
        **kw,  # type: ignore[arg-type]
    )


class Api:
    def __init__(self, backend: MemoryBackend, container: Container) -> None:
        self.backend = backend
        self.container = container
        self.app: FastAPI = create_app(container.settings, container, HealthService([]))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    def set_chat(self, mode: str) -> None:
        self.container = replace(self.container, chat=StubChatLLM(mode))  # type: ignore[arg-type]
        self.app.state.container = self.container

    @property
    def audit(self) -> list[dict[str, object]]:
        return [r.model_dump() for r in self.backend.store.state.audit]


@pytest.fixture
def make_api() -> Callable[..., Api]:
    def _make(**settings: object) -> Api:
        backend = build_memory()
        c = Container(
            uow_factory=backend.uow_factory,
            retriever=backend.retriever,
            storage=MemoryFileStorage(),
            embedder=StubEmbedder(),
            chat=StubChatLLM(),
            queue=InlineTaskQueue(),
            extra_audit_sinks=[],
            settings=memory_settings(**settings),
            registry=backend.registry,
        )
        return Api(backend, c)

    return _make


@pytest.fixture
async def api(make_api: Callable[..., Api]) -> AsyncIterator[Api]:
    a = make_api()
    yield a
    await a.client.aclose()
