"""Поиск в Postgres: полнотекстовый (FTS, словарь 'russian') и гибридный FTS + pgvector со слиянием RRF.

- FTS score = доля лексем вопроса, присутствующих во фрагменте (0..1).
- Вектор = косинусное сходство ``1 - (embedding <=> q)`` только по *активной* модели эмбеддингов,
  через частичный HNSW-индекс (``embedding::vector(dim)``, ``WHERE embedding_model = model``).
- Слияние = Reciprocal Rank Fusion: сумма ``1/(rrf_k + rank)`` по обоим каналам задаёт *порядок*.
- Score = нормированный ``max(fts_score, cosine)`` в 0..1, поэтому один порог MIN_SCORE применим к обоим
  каналам (строки только из FTS сохраняют долю лексем, только из векторов — косинус).

Ищем только по документам в статусе 'ready'.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.models import ScoredSnippet

# ql — уникальные лексемы вопроса, qn — их количество, qt — tsquery «лексема1 | лексема2 | ...».
# score — доля лексем вопроса во фрагменте; rank (ts_rank_cd) — только для упорядочивания при равенстве.
FTS_SQL = text(
    """
WITH ql AS (SELECT DISTINCT lexeme FROM unnest(to_tsvector('russian', :q))),
     qn AS (SELECT count(*)::float AS n FROM ql),
     qt AS (SELECT string_agg(quote_literal(lexeme), ' | ')::tsquery AS q FROM ql)
SELECT s.id, s.document_id, s.snippet_text,
       (SELECT count(*) FROM ql WHERE ql.lexeme = ANY (tsvector_to_array(s.tsv)))::float
         / NULLIF((SELECT n FROM qn), 0) AS score,
       ts_rank_cd(s.tsv, qt.q) AS rank
FROM app.snippets s
JOIN app.documents d ON d.id = s.document_id AND d.status = 'ready'
CROSS JOIN qt
WHERE qt.q IS NOT NULL AND s.tsv @@ qt.q
ORDER BY score DESC, rank DESC, s.id
LIMIT :k
"""
)

# Размерность модели, только если она активна (векторный поиск по неактивной модели не делаем).
ACTIVE_DIM_SQL = text("SELECT dim FROM app.embedding_models WHERE name = :m AND status = 'active'")


def vector_sql(dim: int) -> str:
    """SQL векторного канала для модели размерности ``dim``.

    Размерность подставляется литералом (это проверенный int из реестра, не ввод пользователя), чтобы
    выражение совпало с выражением частичного HNSW-индекса — иначе планировщик индекс не использует.
    """
    return f"""
SELECT s.id, s.document_id, s.snippet_text,
       1 - (e.embedding::vector({dim}) <=> CAST(:qv AS vector({dim}))) AS score
FROM app.snippet_embeddings e
JOIN app.snippets s ON s.id = e.snippet_id AND s.text_sha256 = e.text_sha256
JOIN app.documents d ON d.id = s.document_id AND d.status = 'ready'
WHERE e.embedding_model = :m
ORDER BY e.embedding::vector({dim}) <=> CAST(:qv AS vector({dim}))
LIMIT :k
"""


def _clip(x: float | None) -> float:
    """Обрезает значение в [0, 1] (None → 0)."""
    return min(1.0, max(0.0, float(x or 0.0)))


def rrf_merge(
    fts: list[ScoredSnippet], vec: list[ScoredSnippet], k: int, rrf_k: int = 60
) -> list[ScoredSnippet]:
    """Сливает результаты FTS и векторного поиска методом Reciprocal Rank Fusion.

    RRF учитывает только ранги, поэтому несравнимые шкалы (доля лексем и косинус) не мешают слиянию.
    Фрагмент, найденный обоими каналами, получает сумму вкладов и поднимается выше. В результате
    остаётся лучший из двух score фрагмента.

    Args:
        fts: результаты полнотекстового поиска (по убыванию релевантности).
        vec: результаты векторного поиска.
        k: сколько вернуть.
        rrf_k: константа RRF (обычно 60): чем больше, тем слабее разница между соседними рангами.
    """
    fused: dict[int, float] = {}
    best: dict[int, ScoredSnippet] = {}
    for lst in (fts, vec):
        for rank, s in enumerate(lst, 1):
            fused[s.snippet_id] = fused.get(s.snippet_id, 0.0) + 1.0 / (rrf_k + rank)
            if s.snippet_id not in best or s.score > best[s.snippet_id].score:
                best[s.snippet_id] = s
    order = sorted(fused, key=lambda sid: (-fused[sid], -best[sid].score, sid))
    return [best[sid] for sid in order[:k]]


class PostgresFtsRetriever:
    """Порт Retriever: только полнотекстовый поиск."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def _fts(self, query: str, k: int) -> list[ScoredSnippet]:
        """До ``k`` фрагментов по FTS-запросу (лексемы вопроса через OR)."""
        async with self._engine.connect() as conn:
            res = await conn.execute(FTS_SQL, {"q": query, "k": k})
            return [
                ScoredSnippet(
                    snippet_id=m["id"],
                    document_id=m["document_id"],
                    text=m["snippet_text"],
                    score=_clip(m["score"]),
                )
                for m in res.mappings()
            ]

    async def search(
        self, query: str, qvec: list[float] | None, model: str | None, k: int
    ) -> list[ScoredSnippet]:
        if k <= 0 or not query.strip():
            return []
        return await self._fts(query, k)


class PostgresHybridRetriever(PostgresFtsRetriever):
    """FTS, если вектора вопроса нет (эмбеддер упал → fts_only), иначе FTS + вектор + RRF.

    ``candidates`` — во сколько раз больше кандидатов берём из каждого канала, чем нужно вернуть.
    """

    def __init__(self, engine: AsyncEngine, rrf_k: int = 60, candidates: int = 4) -> None:
        super().__init__(engine)
        self._rrf_k = rrf_k
        self._cand = candidates

    async def _vector(self, qvec: list[float], model: str, n: int) -> list[ScoredSnippet]:
        """Векторный канал: ближайшие фрагменты по косинусному расстоянию.

        Пустой результат, если модель не активна или размерность вектора вопроса не совпадает.
        """
        async with self._engine.connect() as conn:
            dim = (await conn.execute(ACTIVE_DIM_SQL, {"m": model})).scalar_one_or_none()
            if dim is None or int(dim) != len(qvec):
                return []  # модель не активна/другая размерность: без векторов
            qv = "[" + ",".join(f"{x:.7g}" for x in qvec) + "]"
            # ef_search: ширина поиска в HNSW (больше — точнее, но медленнее); действует до конца
            # транзакции.
            await conn.execute(text("SET LOCAL hnsw.ef_search = 100"))
            res = await conn.execute(text(vector_sql(int(dim))), {"qv": qv, "m": model, "k": n})
            return [
                ScoredSnippet(
                    snippet_id=m["id"],
                    document_id=m["document_id"],
                    text=m["snippet_text"],
                    score=_clip(m["score"]),
                )
                for m in res.mappings()
            ]

    async def search(
        self, query: str, qvec: list[float] | None, model: str | None, k: int
    ) -> list[ScoredSnippet]:
        if k <= 0:
            return []
        n = k * self._cand
        fts = await self._fts(query, n) if query.strip() else []
        if qvec is None or model is None:
            return fts[:k]
        vec = await self._vector(qvec, model, n)
        return rrf_merge(fts, vec, k, self._rrf_k)
