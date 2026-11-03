"""Health probe for an OpenAI-compatible endpoint (OpenAI/ProxyAPI/Ollama): GET /models."""

from app.adapters.openai_compat.client import OpenAICompatClient
from app.domain.health import ComponentStatus


class OpenAICompatProbe:
    def __init__(self, name: str, client: OpenAICompatClient, model: str) -> None:
        self.name = name
        self._c = client
        self._model = model

    async def check(self) -> ComponentStatus:
        data = await self._c.get("models")
        ids = {str(m.get("id")) for m in data.get("data", []) if isinstance(m, dict)}
        if ids and self._model not in ids and f"{self._model}:latest" not in ids:
            return ComponentStatus(self.name, False, f"model {self._model!r} not served")
        return ComponentStatus(self.name, True, self._model)
