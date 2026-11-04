"""Реестр моделей эмбеддингов в Postgres (``app.embedding_models`` + ``app.snippet_embeddings``).

Реализует порт EmbeddingModelRegistry для blue/green-переиндексации: регистрация, прогресс,
покрытие, атомарная активация и удаление данных модели вместе с её HNSW-индексом.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.postgres.admin import create_hnsw_index
from app.domain.errors import Conflict, NotFound
from app.domain.models import EmbeddingModelInfo

_COLS = (
    "name, provider, dim, status::text AS status, created_at, activated_at, retired_at, "
    "progress_done, progress_total, error"
)


def _info(row: Any) -> EmbeddingModelInfo:
    """Строка реестра → ``EmbeddingModelInfo``."""
    return EmbeddingModelInfo(**dict(row))


def index_name(model: str) -> str:
    """Имя HNSW-индекса модели; совпадает с тем, что генерирует SQL-функция ``app.hnsw_index_ddl`` (md5)."""
    import hashlib

    return "ix_snip_emb_hnsw_" + hashlib.md5(model.encode()).hexdigest()[:12]  # noqa: S324 - как md5() в SQL


class PostgresEmbeddingRegistry:
    """Порт EmbeddingModelRegistry поверх Postgres."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._e = engine

    async def list(self) -> list[EmbeddingModelInfo]:
        async with self._e.connect() as c:
            res = await c.execute(text(f"SELECT {_COLS} FROM app.embedding_models ORDER BY created_at, name"))
            return [_info(r) for r in res.mappings()]

    async def get(self, name: str) -> EmbeddingModelInfo | None:
        async with self._e.connect() as c:
            res = await c.execute(
                text(f"SELECT {_COLS} FROM app.embedding_models WHERE name = :n"), {"n": name}
            )
            row = res.mappings().first()
            return _info(row) if row else None

    async def active(self) -> EmbeddingModelInfo | None:
        async with self._e.connect() as c:
            res = await c.execute(text(f"SELECT {_COLS} FROM app.embedding_models WHERE status = 'active'"))
            row = res.mappings().first()
            return _info(row) if row else None

    async def register(self, name: str, provider: str, dim: int) -> EmbeddingModelInfo:
        """Регистрирует модель (status=indexing).

        Raises:
            Conflict: имя занято (нарушение PK).
        """
        try:
            async with self._e.begin() as c:
                await c.execute(
                    text("INSERT INTO app.embedding_models (name, provider, dim) VALUES (:n, :p, :d)"),
                    {"n": name, "p": provider, "d": dim},
                )
        except IntegrityError as e:
            raise Conflict(f"embedding model {name!r} already registered") from e
        info = await self.get(name)
        assert info is not None
        return info

    async def bootstrap(self, name: str, provider: str, dim: int) -> str:
        async with self._e.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO app.embedding_models (name, provider, dim) VALUES (:n, :p, :d) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                {"n": name, "p": provider, "d": dim},
            )
            act = (
                await c.execute(text("SELECT name FROM app.embedding_models WHERE status = 'active'"))
            ).scalar_one_or_none()
            if act is None:
                await c.execute(
                    text("UPDATE app.embedding_models SET status='active', activated_at=now() WHERE name=:n"),
                    {"n": name},
                )
                act = name
        return str(act)

    async def _update(self, sql: str, params: dict[str, Any]) -> None:
        """Выполняет UPDATE одной модели.

        Raises:
            NotFound: не обновлено ни одной строки.
        """
        async with self._e.begin() as c:
            res = await c.execute(text(sql), params)
            if not getattr(res, "rowcount", 1):
                raise NotFound(f"embedding model {params['n']!r}")

    async def set_status(self, name: str, status: str, error: str | None = None) -> None:
        await self._update(
            "UPDATE app.embedding_models SET status = CAST(:s AS app.embedding_model_status), error = :e "
            "WHERE name = :n",
            {"n": name, "s": status, "e": error},
        )

    async def set_progress(self, name: str, done: int, total: int) -> None:
        await self._update(
            "UPDATE app.embedding_models SET progress_done = :d, progress_total = :t WHERE name = :n",
            {"n": name, "d": done, "t": total},
        )

    async def coverage(self, name: str) -> tuple[int, int]:
        """(фрагменты с эмбеддингом этой модели для текущей версии текста, всего фрагментов)."""
        async with self._e.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT count(e.snippet_id) AS done, count(*) AS total FROM app.snippets s "
                        "LEFT JOIN app.snippet_embeddings e ON e.snippet_id = s.id "
                        "AND e.embedding_model = :n AND e.text_sha256 = s.text_sha256"
                    ),
                    {"n": name},
                )
            ).one()
        return int(row[0]), int(row[1])

    async def activate(self, name: str) -> None:
        """Атомарно делает модель активной, а прежнюю активную — retired.

        Advisory-lock сериализует параллельные активации, а одна транзакция гарантирует, что читатели
        видят
        ровно одну активную модель (есть и уникальный частичный индекс по status='active').
        """
        async with self._e.begin() as c:  # одна транзакция: видна старая или новая модель
            await c.execute(text("SELECT pg_advisory_xact_lock(hashtext('app.embedding_models.activate'))"))
            exists = (
                await c.execute(
                    text("SELECT 1 FROM app.embedding_models WHERE name = :n FOR UPDATE"), {"n": name}
                )
            ).scalar_one_or_none()
            if exists is None:
                raise NotFound(f"embedding model {name!r}")
            await c.execute(
                text(
                    "UPDATE app.embedding_models SET status = 'retired', retired_at = now() "
                    "WHERE status = 'active' AND name <> :n"
                ),
                {"n": name},
            )
            await c.execute(
                text(
                    "UPDATE app.embedding_models SET status = 'active', activated_at = now() WHERE name = :n"
                ),
                {"n": name},
            )

    async def delete(self, name: str) -> int:
        """Удаляет эмбеддинги и строку реестра (в транзакции), затем HNSW-индекс (CONCURRENTLY).

        Raises:
            NotFound: модели нет.
            Conflict: модель активна.
        """
        async with self._e.begin() as c:
            st = (
                await c.execute(
                    text("SELECT status::text FROM app.embedding_models WHERE name = :n FOR UPDATE"),
                    {"n": name},
                )
            ).scalar_one_or_none()
            if st is None:
                raise NotFound(f"embedding model {name!r}")
            if st == "active":
                raise Conflict("cannot delete the active model")
            res = await c.execute(
                text("DELETE FROM app.snippet_embeddings WHERE embedding_model = :n"), {"n": name}
            )
            n = int(getattr(res, "rowcount", 0) or 0)
            await c.execute(text("DELETE FROM app.embedding_models WHERE name = :n"), {"n": name})
        async with self._e.connect() as c:
            ac = await c.execution_options(isolation_level="AUTOCOMMIT")
            await ac.execute(text(f'DROP INDEX CONCURRENTLY IF EXISTS app."{index_name(name)}"'))
        return n

    async def ensure_index(self, name: str) -> None:
        """Создаёт HNSW-индекс модели, если его нет."""
        await create_hnsw_index(self._e, name)
