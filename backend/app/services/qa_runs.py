"""QaRunService — чтение истории вопросов и ответов (очередь на ручную проверку, ``/kb/qa_runs``)."""

from collections.abc import Callable

from app.domain.errors import NotFound
from app.domain.models import Page, QaRun
from app.domain.ports import UnitOfWork


class QaRunService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow = uow_factory

    async def list(self, page: Page, needs_review: bool | None = None) -> list[QaRun]:
        """Страница запусков, новые первыми; ``needs_review`` — фильтр (None — все)."""
        async with self._uow() as uow:
            return await uow.qa_runs.list(page, needs_review)

    async def get(self, run_id: int) -> QaRun:
        """Запуск по id.

        Raises:
            NotFound: запуска нет.
        """
        async with self._uow() as uow:
            run = await uow.qa_runs.get(run_id)
        if run is None:
            raise NotFound(f"qa_run {run_id} not found")
        return run
