"""In-memory embedding-model registry (reference implementation of EmbeddingModelRegistry)."""

from app.adapters.memory.store import MemoryStore
from app.domain.errors import Conflict, NotFound
from app.domain.models import EmbeddingModelInfo, utcnow


class MemoryEmbeddingRegistry:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def _m(self) -> dict[str, EmbeddingModelInfo]:
        return self._store.state.embedding_models

    def _upd(self, name: str, **kw: object) -> None:
        info = self._m.get(name)
        if info is None:
            raise NotFound(f"embedding model {name!r}")
        self._m[name] = info.model_copy(update=kw)

    async def list(self) -> list[EmbeddingModelInfo]:
        return sorted(self._m.values(), key=lambda i: (i.created_at, i.name))

    async def get(self, name: str) -> EmbeddingModelInfo | None:
        return self._m.get(name)

    async def active(self) -> EmbeddingModelInfo | None:
        return next((i for i in self._m.values() if i.status == "active"), None)

    async def register(self, name: str, provider: str, dim: int) -> EmbeddingModelInfo:
        if name in self._m:
            raise Conflict(f"embedding model {name!r} already registered")
        info = EmbeddingModelInfo(name=name, provider=provider, dim=dim)
        self._m[name] = info
        return info

    async def bootstrap(self, name: str, provider: str, dim: int) -> str:
        if name not in self._m:
            self._m[name] = EmbeddingModelInfo(name=name, provider=provider, dim=dim)
        act = await self.active()
        if act is None:
            self._upd(name, status="active", activated_at=utcnow())
            return name
        return act.name

    async def set_status(self, name: str, status: str, error: str | None = None) -> None:
        self._upd(name, status=status, error=error)

    async def set_progress(self, name: str, done: int, total: int) -> None:
        self._upd(name, progress_done=done, progress_total=total)

    async def coverage(self, name: str) -> tuple[int, int]:
        st = self._store.state
        done = sum(
            1
            for sid, sn in st.snippets.items()
            if (e := st.embeddings.get((sid, name))) is not None and e[0] == sn.text_sha256
        )
        return done, len(st.snippets)

    async def activate(self, name: str) -> None:
        if name not in self._m:
            raise NotFound(f"embedding model {name!r}")
        now = utcnow()
        models = dict(self._m)  # build the new state, then swap (atomic for readers)
        for n, i in models.items():
            if i.status == "active" and n != name:
                models[n] = i.model_copy(update={"status": "retired", "retired_at": now})
        models[name] = models[name].model_copy(update={"status": "active", "activated_at": now})
        self._store.state.embedding_models = models

    async def delete(self, name: str) -> int:
        info = self._m.get(name)
        if info is None:
            raise NotFound(f"embedding model {name!r}")
        if info.status == "active":
            raise Conflict("cannot delete the active model")
        st = self._store.state
        keys = [k for k in st.embeddings if k[1] == name]
        for k in keys:
            del st.embeddings[k]
        del self._m[name]
        return len(keys)

    async def ensure_index(self, name: str) -> None:
        return None
