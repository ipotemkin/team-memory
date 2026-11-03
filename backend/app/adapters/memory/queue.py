"""Synchronous (inline) TaskQueue: runs registered handlers immediately."""

import inspect
import uuid
from collections.abc import Callable
from typing import Any


class InlineTaskQueue:
    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self.history: list[tuple[str, str, dict[str, Any]]] = []

    def register(self, task: str, handler: Callable[..., Any]) -> None:
        self._handlers[task] = handler

    async def enqueue(self, task: str, **kwargs: Any) -> str:
        task_id = str(uuid.uuid4())
        self.history.append((task_id, task, kwargs))
        handler = self._handlers.get(task)
        if handler is not None:
            res = handler(**kwargs)
            if inspect.isawaitable(res):
                await res
        return task_id
