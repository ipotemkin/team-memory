"""Embedder поверх ``/embeddings`` (пачками; ретраи и таймауты — в клиенте)."""

from app.adapters.openai_compat.client import OpenAICompatClient
from app.domain.errors import LLMError


class OpenAICompatEmbedder:
    """Порт Embedder для OpenAI-совместимого сервера.

    Args:
        client: HTTP-клиент.
        model: имя модели эмбеддингов (EMBEDDINGS_MODEL).
        dim: ожидаемая размерность вектора (EMBEDDINGS_DIM).
        batch_size: сколько текстов отправлять в одном запросе.
    """

    def __init__(self, client: OpenAICompatClient, model: str, dim: int, batch_size: int = 64) -> None:
        self._c = client
        self.model_name = model
        self.dim = dim
        self._batch = max(1, batch_size)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Эмбеддинги для текстов (порядок сохраняется).

        Raises:
            LLMError: ошибка API, неожиданный формат ответа или размерность не совпала с ``dim``.
        """
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            # Переводы строк заменяем пробелами, пустую строку — пробелом (API не принимает пустой input).
            chunk = [t.replace("\n", " ") or " " for t in texts[i : i + self._batch]]
            payload: dict[str, object] = {"model": self.model_name, "input": chunk}
            # Только модели text-embedding-3 умеют укорачивать вектор до заданной размерности.
            if self.model_name.startswith("text-embedding-3"):
                payload["dimensions"] = self.dim
            data = await self._c.post("embeddings", payload)
            try:
                # Сортируем по index: порядок в ответе не гарантирован.
                items = sorted(data["data"], key=lambda d: d["index"])
                vecs = [[float(x) for x in d["embedding"]] for d in items]
            except (KeyError, TypeError, ValueError) as e:
                raise LLMError(f"bad embeddings response: {e}") from e
            if len(vecs) != len(chunk) or any(len(v) != self.dim for v in vecs):
                raise LLMError(f"embeddings shape mismatch: expected {len(chunk)}x{self.dim}")
            out.extend(vecs)
        return out
