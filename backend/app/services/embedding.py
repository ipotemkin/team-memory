"""ActiveEmbedder — эмбеддер, который «следует» за активной моделью реестра.

Реализует порт Embedder поверх реестра: раз в ``ttl_s`` секунд спрашивает у реестра активную модель
и переключается на неё. Поэтому после ``/admin/.../activate`` API и воркер начинают использовать
новую модель без перезапуска.
"""

import time
from collections.abc import Callable

from app.domain.models import EmbeddingModelInfo
from app.domain.ports import Embedder, EmbeddingModelRegistry

# Фабрика: описание модели из реестра → готовый эмбеддер.
EmbedderFactory = Callable[[EmbeddingModelInfo], Embedder]


class ActiveEmbedder:
    """Эмбеддер активной модели с кешем эмбеддеров по именам моделей.

    Args:
        default: эмбеддер модели из настроек (используется, пока реестр не ответил).
        registry: реестр моделей.
        factory: создаёт эмбеддер по описанию модели.
        ttl_s: как часто перепроверять активную модель.
    """

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
        """Сбрасывает таймер, чтобы следующий вызов сразу перечитал активную модель."""
        self._checked = 0.0

    def for_model(self, info: EmbeddingModelInfo) -> Embedder:
        """Эмбеддер для модели ``info`` (из кеша; пересоздаётся, если сменилась размерность)."""
        e = self._cache.get(info.name)
        if e is None or e.dim != info.dim:
            e = self._factory(info)
            self._cache[info.name] = e
        return e

    async def resolve(self) -> Embedder:
        """Текущий эмбеддер активной модели.

        Если реестр недоступен, остаёмся на последней известной модели. ``model_name``/``dim``
        обновляются,
        чтобы вызывающий код (AskService) записал в историю фактически использованную модель.
        """
        now = time.monotonic()
        if now - self._checked >= self._ttl:
            self._checked = now
            try:
                info = await self._registry.active()
            except Exception:  # noqa: BLE001 - реестр недоступен: остаёмся на прежней модели
                info = None
            if info is not None:
                self._current = self.for_model(info)
                self.model_name, self.dim = self._current.model_name, self._current.dim
        return self._current

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Считает эмбеддинги активной моделью."""
        return await (await self.resolve()).embed(texts)
