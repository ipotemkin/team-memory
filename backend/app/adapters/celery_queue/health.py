"""Проба доступности Redis (``PING``) для ``/admin/health``."""

import redis.asyncio as aioredis

from app.domain.health import ComponentStatus


class RedisProbe:
    """Порт HealthProbe для Redis: новое соединение на каждую проверку, таймаут подключения 2 с."""

    name = "redis"

    def __init__(self, url: str) -> None:
        self._url = url

    async def check(self) -> ComponentStatus:
        client = aioredis.from_url(self._url, socket_connect_timeout=2)
        try:
            await client.ping()
        finally:
            await client.aclose()
        return ComponentStatus(self.name, True)
