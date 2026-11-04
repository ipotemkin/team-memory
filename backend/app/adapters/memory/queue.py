"""Синхронная очередь задач (QUEUE_BACKEND=inline): зарегистрированный обработчик выполняется сразу.

Реализует порт TaskQueue без Celery/Redis — удобно в тестах: после enqueue документ уже проиндексирован.
"""

import inspect
import uuid
from collections.abc import Callable
from typing import Any


class InlineTaskQueue:
    """Очередь, выполняющая задачу прямо в вызове ``enqueue``.

    ``history`` хранит все поставленные задачи (id, имя, аргументы) — для проверок в тестах.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self.history: list[tuple[str, str, dict[str, Any]]] = []

    def register(self, task: str, handler: Callable[..., Any]) -> None:
        """Регистрирует обработчик задачи (обычная или async-функция)."""
        self._handlers[task] = handler

    async def enqueue(self, task: str, **kwargs: Any) -> str:
        """Выполняет обработчик (если он есть) и возвращает сгенерированный id задачи."""
        task_id = str(uuid.uuid4())
        self.history.append((task_id, task, kwargs))
        handler = self._handlers.get(task)
        if handler is not None:
            res = handler(**kwargs)
            if inspect.isawaitable(res):
                await res
        return task_id
