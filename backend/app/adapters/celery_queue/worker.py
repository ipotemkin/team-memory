"""Celery-приложение: фоновая индексация и периодическое обслуживание (beat).

Запуск:  celery -A app.adapters.celery_queue.worker:celery_app worker -l info
         celery -A app.adapters.celery_queue.worker:celery_app beat -l info

Асинхронные сервисы работают на ОДНОМ event loop на процесс воркера: async-движок SQLAlchemy и
httpx-клиент привязаны к циклу, в котором созданы, поэтому ``asyncio.run()`` на каждую задачу их бы
сломал. Контейнер зависимостей (composition root) создаётся лениво при первой задаче.
"""

import asyncio
import logging
from collections.abc import Awaitable
from typing import TYPE_CHECKING

from celery import Celery, Task
from celery.schedules import crontab

from app.config import get_settings

if TYPE_CHECKING:
    from app.composition import Container

log = logging.getLogger("app.worker")

_s = get_settings()
celery_app = Celery("team_memory", broker=_s.REDIS_URL, backend=_s.REDIS_URL)
celery_app.conf.update(
    # acks_late + reject_on_worker_lost: задача подтверждается после выполнения, поэтому при падении
    # воркера она вернётся в очередь; prefetch=1 — воркер не набирает задачи впрок.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    beat_schedule={
        "ensure-audit-partitions": {
            "task": "ensure_audit_partitions",
            "schedule": crontab(minute="17", hour="3"),  # ежедневно в 03:17 UTC
        },
    },
    timezone="UTC",
)

_loop: asyncio.AbstractEventLoop | None = None
_container: "Container | None" = None


def run[T](aw: Awaitable[T]) -> T:
    """Выполняет корутину на общем для процесса event loop (создаёт его при первом вызове)."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(_wrap(aw))


async def _wrap[T](aw: Awaitable[T]) -> T:
    """Оборачивает awaitable в корутину (run_until_complete принимает future/корутину)."""
    return await aw


def container() -> "Container":
    """Контейнер зависимостей воркера; создаётся один раз и выполняет стартовые задачи (бутстрап моделей и
    т.п.).
    """
    global _container
    if _container is None:
        from app.composition import build_container  # лениво: composition импортирует этот модуль

        _container = build_container(get_settings())
        run(_container.run_startup())
    return _container


@celery_app.task(name="ping")
def ping() -> str:
    """Проверочная задача: ``pong``."""
    return "pong"


@celery_app.task(name="index_document", bind=True, max_retries=_s.INDEX_MAX_RETRIES)
def index_document(self: Task, document_id: int) -> str:  # type: ignore[type-arg]
    # Последняя попытка: сервис пометит документ failed вместо повторного исключения.
    """Индексирует документ (``IndexingService``) с ретраями.

    При ошибке задача перезапускается через ``INDEX_RETRY_BACKOFF_S * 2^попытка`` секунд; на последней
    попытке сервис сам помечает документ failed и не бросает исключение.
    """
    final = self.request.retries >= (self.max_retries or 0)
    try:
        status = run(container().indexing_service().index(document_id, final_attempt=final))
    except Exception as e:
        countdown = _s.INDEX_RETRY_BACKOFF_S * (2**self.request.retries)
        log.warning(
            "index_document %s: retry %s in %ss (%s)", document_id, self.request.retries + 1, countdown, e
        )
        raise self.retry(exc=e, countdown=countdown) from e
    return str(status)


@celery_app.task(name="ensure_audit_partitions")
def ensure_audit_partitions_task() -> int:
    """Создаёт партиции аудита на будущие месяцы (по расписанию beat)."""
    job = container().maintenance.get("ensure_audit_partitions")
    return int(run(job())) if job else 0


@celery_app.task(name="bootstrap_embedding_model")
def bootstrap_embedding_model_task() -> str:
    """Регистрирует/активирует модель эмбеддингов из настроек и создаёт её индекс."""
    job = container().maintenance.get("bootstrap_embedding_model")
    return str(run(job())) if job else ""


@celery_app.task(name="reindex_all")
def reindex_all_task() -> int:
    """Переиндексирует все документы: ``celery -A app.adapters.celery_queue.worker:celery_app call
    reindex_all``.
    """
    return int(run(container().maintenance["reindex_all"]()))


@celery_app.task(name="reindex_embedding_model", bind=True)
def reindex_embedding_model_task(self: Task, name: str) -> int:  # type: ignore[type-arg]
    """Blue/green-переиндексация одной зарегистрированной модели (прогресс — в app.embedding_models)."""
    if not self.request.is_eager:
        self.update_state(state="PROGRESS", meta={"model": name})
    return int(run(container().admin_service().run_reindex_task(name)))
