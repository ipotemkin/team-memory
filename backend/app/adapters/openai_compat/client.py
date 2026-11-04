"""Минимальный async-клиент для OpenAI-совместимых API (OpenAI, ProxyAPI, vLLM, Ollama...).

httpx напрямую вместо SDK openai: одна лёгкая зависимость, явные таймауты и ретраи, удобное
тестирование через ``httpx.MockTransport``.
"""

import asyncio
import logging
import random
from typing import Any

import httpx

from app.domain.errors import LLMError

log = logging.getLogger("app.openai_compat")
# Коды, при которых повтор имеет смысл: таймаут, конфликт, rate limit, ошибки сервера/прокси.
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


class ApiStatusError(LLMError):
    """HTTP-ошибка сервера: код и тело ответа (тело нужно, чтобы распознать неподдержку json_schema)."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class OpenAICompatClient:
    """HTTP-клиент с Bearer-авторизацией, таймаутами и ретраями.

    Args:
        base_url: адрес API, например ``https://api.proxyapi.ru/openai/v1``.
        api_key: ключ (пустой ключ → POST сразу падает с LLMError).
        timeout_s: общий таймаут запроса (на соединение — не больше 10 с).
        max_retries: число повторов после первой попытки.
        backoff_s: базовая задержка экспоненциального backoff.
        transport: подменный транспорт httpx для тестов.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: float = 30.0,
        max_retries: int = 3,
        backoff_s: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self._max_retries = max(0, max_retries)
        self._backoff = backoff_s
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
            transport=transport,
        )

    async def aclose(self) -> None:
        """Закрывает HTTP-соединения."""
        await self._http.aclose()

    async def get(self, path: str) -> dict[str, Any]:
        """Один GET без ретраев (для health-проверок).

        Raises:
            ApiStatusError: ответ с кодом ≥ 400.
        """
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        r = await self._http.get(path.lstrip("/"), headers=headers)
        if r.status_code >= 400:
            raise ApiStatusError(r.status_code, r.text)
        data: dict[str, Any] = r.json()
        return data

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST с ретраями на 408/409/429/5xx и сетевые ошибки (экспоненциальный backoff + jitter).

        Raises:
            LLMError: нет ключа, сетевая ошибка/таймаут после всех попыток или ответ не JSON.
            ApiStatusError: неретраибельный код (например, 400/401) или ретраи исчерпаны.
        """
        if not self._key:
            raise LLMError("OPENAI_API_KEY is not set")
        headers = {"Authorization": f"Bearer {self._key}"}
        attempt = 0
        while True:
            try:
                r = await self._http.post(path.lstrip("/"), json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                err: LLMError = LLMError(f"{type(e).__name__}: {e}")
            else:
                if r.status_code < 400:
                    try:
                        data: dict[str, Any] = r.json()
                    except ValueError as e:
                        raise LLMError("upstream returned non-JSON body") from e
                    return data
                err = ApiStatusError(r.status_code, r.text)
                if r.status_code not in RETRY_STATUS:
                    raise err
            if attempt >= self._max_retries:
                raise err
            # Задержка 0.5, 1, 2... с × (1..1.25) — jitter разводит одновременные повторы разных запросов.
            delay = self._backoff * (2**attempt) * (1 + random.random() * 0.25)  # noqa: S311
            log.warning("openai_compat %s failed (%s), retry %d in %.1fs", path, err, attempt + 1, delay)
            await asyncio.sleep(delay)
            attempt += 1
