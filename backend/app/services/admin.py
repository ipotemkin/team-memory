"""AdminService: embedding-model registry operations and blue/green reindex.

Flow: register (status=indexing) -> reindex (background task, progress in the registry; status=ready)
-> activate (final catch-up pass, coverage check, atomic switch; previous model -> retired)
-> delete inactive model data.
"""

import logging
from collections.abc import Callable

from app.domain.errors import Conflict, NotFound
from app.domain.models import EMBEDDING_DIM_MAX, EMBEDDING_ERROR_MAX, EmbeddingModelInfo
from app.domain.ports import Embedder, EmbeddingModelRegistry, TaskQueue, UnitOfWork

log = logging.getLogger("app.admin")
REINDEX_MODEL_TASK = "reindex_embedding_model"


class AdminService:
    def __init__(
        self,
        registry: EmbeddingModelRegistry,
        uow_factory: Callable[[], UnitOfWork],
        embedder_for: Callable[[EmbeddingModelInfo], Embedder],
        queue: TaskQueue,
        default: EmbeddingModelInfo,
        batch_size: int = 64,
        on_switch: Callable[[], None] | None = None,
    ) -> None:
        self._reg = registry
        self._uow = uow_factory
        self._embedder_for = embedder_for
        self._queue = queue
        self._default = default
        self._batch = max(1, batch_size)
        self._on_switch = on_switch

    async def _bootstrap(self) -> None:
        await self._reg.bootstrap(self._default.name, self._default.provider, self._default.dim)

    async def list_models(self) -> list[EmbeddingModelInfo]:
        await self._bootstrap()
        return await self._reg.list()

    async def get(self, name: str) -> EmbeddingModelInfo:
        info = await self._reg.get(name)
        if info is None:
            raise NotFound(f"embedding model {name!r}")
        return info

    async def register(self, name: str, provider: str, dim: int) -> EmbeddingModelInfo:
        name, provider = name.strip(), provider.strip()
        if not name or not provider or not 0 < dim <= EMBEDDING_DIM_MAX:
            raise ValueError("name, provider and 0 < dim <= 4000 are required")
        await self._bootstrap()
        return await self._reg.register(name, provider, dim)

    async def start_reindex(self, name: str) -> str:
        info = await self.get(name)
        if info.status in ("retired", "ready", "failed") or info.error:
            # restart: back to indexing and clear a previous failure (active stays active)
            await self._reg.set_status(name, "indexing" if info.status != "active" else "active")
        done, total = await self._reg.coverage(name)
        await self._reg.set_progress(name, done, total)
        return await self._queue.enqueue(REINDEX_MODEL_TASK, name=name)

    async def reindex(self, name: str, max_batches: int = 1_000_000) -> int:
        """Embed every snippet missing an up-to-date embedding of ``name``. Returns embedded count."""
        info = await self.get(name)
        embedder = self._embedder_for(info)
        n = 0
        for _ in range(max_batches):
            async with self._uow() as uow:
                missing = await uow.snippets.missing_embeddings(name, self._batch)
            if not missing:
                break
            vecs = await embedder.embed([s.text for s in missing])
            if any(len(v) != info.dim for v in vecs):
                raise Conflict(f"embedder returned vectors of wrong dimension for {name!r} (dim={info.dim})")
            items = [(s.id or 0, s.text_sha256, v) for s, v in zip(missing, vecs, strict=True)]
            async with self._uow() as uow:
                await uow.snippets.upsert_embeddings(name, items)
                await uow.commit()
            n += len(items)
            done, total = await self._reg.coverage(name)
            await self._reg.set_progress(name, done, total)
        done, total = await self._reg.coverage(name)
        await self._reg.set_progress(name, done, total)
        await self._reg.ensure_index(name)
        current = await self._reg.get(name)
        if current is not None and current.status == "indexing" and done >= total:
            await self._reg.set_status(name, "ready")
        log.info("reindex %s: embedded %s, coverage %s/%s", name, n, done, total)
        return n

    async def run_reindex_task(self, name: str) -> int:
        """Background-task entry point: like reindex(), but a failure is persisted in the registry
        (status='failed' unless the model is active, error message truncated) and re-raised."""
        try:
            return await self.reindex(name)
        except Exception as e:
            msg = (f"{type(e).__name__}: {e}")[:EMBEDDING_ERROR_MAX]
            log.exception("reindex %s failed", name)
            try:
                current = await self._reg.get(name)
                if current is not None:
                    status = "active" if current.status == "active" else "failed"
                    await self._reg.set_status(name, status, msg)
            except Exception:  # noqa: BLE001 - never mask the original error
                log.exception("could not persist reindex failure for %s", name)
            raise

    async def activate(self, name: str) -> EmbeddingModelInfo:
        info = await self.get(name)
        if info.status == "active":
            return info
        if info.status != "ready":
            raise Conflict(f"model {name!r} is {info.status!r}; run reindex until it is 'ready'")
        await self.reindex(name)  # catch up documents indexed after the reindex finished
        done, total = await self._reg.coverage(name)
        if done < total:
            raise Conflict(f"model {name!r} covers {done}/{total} snippets; reindex again")
        await self._reg.activate(name)
        if self._on_switch is not None:
            self._on_switch()
        return await self.get(name)

    async def delete(self, name: str) -> int:
        info = await self.get(name)
        if info.status == "active":
            raise Conflict("the active model cannot be deleted; activate another one first")
        return await self._reg.delete(name)
