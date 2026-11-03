"""TaskQueue backed by Celery (send_task by name; workers register the handlers)."""

import asyncio
from typing import Any

from celery import Celery


class CeleryTaskQueue:
    def __init__(self, app: Celery) -> None:
        self._app = app

    async def enqueue(self, task: str, **kwargs: Any) -> str:
        res = await asyncio.to_thread(self._app.send_task, task, kwargs=kwargs)
        return str(res.id)
