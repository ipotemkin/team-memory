"""ActiveEmbedder: an Embedder that follows the registry's active model (switches after activation)."""

import time
from collections.abc import Callable

from app.domain.models import EmbeddingModelInfo
from app.domain.ports import Embedder, EmbeddingModelRegistry

EmbedderFactory = Callable[[EmbeddingModelInfo], Embedder]


class ActiveEmbedder:
    def __init__(
        self,
        default: Embedder,
        registry: EmbeddingModelRegistry,
        factory: EmbedderFactory,
        ttl_s: float = 5.0,
    ) -> None:
        self._default = default
        self._registry = registry
        self._factory = factory
        self._ttl = ttl_s
        self._current: Embedder = default
        self._checked = 0.0
        self._cache: dict[str, Embedder] = {default.model_name: default}
        self.model_name: str = default.model_name
        self.dim: int = default.dim

    def invalidate(self) -> None:
        self._checked = 0.0

    def for_model(self, info: EmbeddingModelInfo) -> Embedder:
        e = self._cache.get(info.name)
        if e is None or e.dim != info.dim:
            e = self._factory(info)
            self._cache[info.name] = e
        return e

    async def resolve(self) -> Embedder:
        now = time.monotonic()
        if now - self._checked >= self._ttl:
            self._checked = now
            try:
                info = await self._registry.active()
            except Exception:  # noqa: BLE001 - registry unavailable: keep the last known model
                info = None
            if info is not None:
                self._current = self.for_model(info)
                self.model_name, self.dim = self._current.model_name, self._current.dim
        return self._current

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await (await self.resolve()).embed(texts)
