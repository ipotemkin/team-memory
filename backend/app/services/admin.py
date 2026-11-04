"""AdminService — реестр моделей эмбеддингов и blue/green-переиндексация (эндпоинты ``/admin``).

Поток: register (status=indexing) → reindex (фоновая задача, прогресс в реестре; затем status=ready)
→ activate (финальный догоняющий проход, проверка покрытия, атомарное переключение; прежняя модель
→ retired) → delete (удаление данных неактивной модели).

Пока новая модель считается, поиск продолжает работать на старой активной модели — пользователи
не замечают переиндексацию («blue/green»).
"""

import logging
from collections.abc import Callable

from app.domain.errors import Conflict, NotFound
from app.domain.models import EMBEDDING_DIM_MAX, EMBEDDING_ERROR_MAX, EmbeddingModelInfo
from app.domain.ports import Embedder, EmbeddingModelRegistry, TaskQueue, UnitOfWork

log = logging.getLogger("app.admin")
# Имя Celery-задачи переиндексации модели.
REINDEX_MODEL_TASK = "reindex_embedding_model"


class AdminService:
    """Операции администратора над моделями эмбеддингов.

    Args:
        registry: реестр моделей (порт EmbeddingModelRegistry).
        uow_factory: фабрика UnitOfWork (чтение фрагментов, запись эмбеддингов).
        embedder_for: фабрика эмбеддера по описанию модели.
        queue: очередь для фоновой задачи переиндексации.
        default: модель из настроек (EMBEDDINGS_MODEL) — регистрируется при первом обращении.
        batch_size: размер пачки фрагментов на один вызов эмбеддера (REINDEX_BATCH_SIZE).
        on_switch: колбэк после смены активной модели (сбрасывает кеш ActiveEmbedder).
    """

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
        """Гарантирует, что модель по умолчанию зарегистрирована и есть активная модель."""
        await self._reg.bootstrap(self._default.name, self._default.provider, self._default.dim)

    async def list_models(self) -> list[EmbeddingModelInfo]:
        """Все модели реестра."""
        await self._bootstrap()
        return await self._reg.list()

    async def get(self, name: str) -> EmbeddingModelInfo:
        """Модель по имени.

        Raises:
            NotFound: модели нет.
        """
        info = await self._reg.get(name)
        if info is None:
            raise NotFound(f"embedding model {name!r}")
        return info

    async def register(self, name: str, provider: str, dim: int) -> EmbeddingModelInfo:
        """Регистрирует новую модель (status=indexing).

        Raises:
            ValueError: пустые имя/провайдер или dim вне (0, 4000] (API отдаёт 422).
            Conflict: модель уже зарегистрирована (409).
        """
        name, provider = name.strip(), provider.strip()
        if not name or not provider or not 0 < dim <= EMBEDDING_DIM_MAX:
            raise ValueError("name, provider and 0 < dim <= 4000 are required")
        await self._bootstrap()
        return await self._reg.register(name, provider, dim)

    async def start_reindex(self, name: str) -> str:
        """Ставит фоновую переиндексацию модели и возвращает id задачи.

        Повторный запуск для retired/ready/failed возвращает модель в indexing и стирает прошлую ошибку;
        активная модель остаётся активной (догоняющая переиндексация).
        """
        info = await self.get(name)
        if info.status in ("retired", "ready", "failed") or info.error:
            # Перезапуск: обратно в indexing и сброс прошлой ошибки (активная остаётся active).
            await self._reg.set_status(name, "indexing" if info.status != "active" else "active")
        done, total = await self._reg.coverage(name)
        await self._reg.set_progress(name, done, total)
        return await self._queue.enqueue(REINDEX_MODEL_TASK, name=name)

    async def reindex(self, name: str, max_batches: int = 1_000_000) -> int:
        """Считает эмбеддинги для всех фрагментов без актуального вектора модели ``name``.

        Работает пачками по ``batch_size``, после каждой пачки обновляет прогресс в реестре. В конце
        создаёт векторный индекс и, если всё покрыто, переводит модель из indexing в ready.

        Returns:
            Сколько фрагментов получили эмбеддинг.

        Raises:
            Conflict: эмбеддер вернул векторы не той размерности.
        """
        info = await self.get(name)
        embedder = self._embedder_for(info)
        n = 0
        for _ in range(max_batches):
            async with self._uow() as uow:
                missing = await uow.snippets.missing_embeddings(name, self._batch)
            if not missing:
                break
            vecs = await embedder.embed([s.text for s in missing])
            # Защита от неверно указанного dim: такие векторы не влезут в колонку vector(dim).
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
        """Точка входа фоновой задачи: как reindex(), но сбой сохраняется в реестре.

        Модель получает status=failed (активная остаётся active) и текст ошибки (обрезан до
        EMBEDDING_ERROR_MAX); исключение затем пробрасывается дальше, чтобы задача считалась упавшей.
        """
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
            except Exception:  # noqa: BLE001 - не маскируем исходную ошибку
                log.exception("could not persist reindex failure for %s", name)
            raise

    async def activate(self, name: str) -> EmbeddingModelInfo:
        """Делает модель активной (blue/green-переключение).

        Сначала догоняющий reindex (документы, добавленные после окончания переиндексации), затем
        проверка, что покрыты все фрагменты, и атомарное переключение в реестре.

        Raises:
            Conflict: модель не в статусе ready или покрыты не все фрагменты.
        """
        info = await self.get(name)
        if info.status == "active":
            return info
        if info.status != "ready":
            raise Conflict(f"model {name!r} is {info.status!r}; run reindex until it is 'ready'")
        await self.reindex(name)  # догоняем документы, проиндексированные после reindex
        done, total = await self._reg.coverage(name)
        if done < total:
            raise Conflict(f"model {name!r} covers {done}/{total} snippets; reindex again")
        await self._reg.activate(name)
        if self._on_switch is not None:
            self._on_switch()
        return await self.get(name)

    async def delete(self, name: str) -> int:
        """Удаляет неактивную модель вместе с её эмбеддингами и индексом; возвращает число удалённых
        векторов.

        Raises:
            Conflict: попытка удалить активную модель.
        """
        info = await self.get(name)
        if info.status == "active":
            raise Conflict("the active model cannot be deleted; activate another one first")
        return await self._reg.delete(name)
