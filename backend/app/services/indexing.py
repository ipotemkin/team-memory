"""IndexingService — индексация документа: нарезка → фрагменты → эмбеддинги → статус ready/failed.

Работает в фоне (Celery-задача ``index_document`` из ``adapters/celery_queue/worker.py``) или сразу
(в тестах и при QUEUE_BACKEND=inline). Эмбеддинги считаются вне транзакции БД (сетевой вызов может
быть долгим); документ становится ``ready`` только после того, как все его фрагменты получили
эмбеддинг активной модели, поэтому поиск никогда не видит наполовину проиндексированный документ
(Retriever ищет только по документам в статусе ready).
"""

import logging
from collections.abc import Callable

from app.domain.chunking import chunk_paragraphs
from app.domain.errors import NotFound
from app.domain.models import DocumentStatus, Snippet
from app.domain.ports import Embedder, UnitOfWork

log = logging.getLogger("app.indexing")
# Имя Celery-задачи индексации (используется DocumentService при постановке в очередь).
INDEX_TASK = "index_document"


class IndexingService:
    """Индексирует документы.

    Args:
        uow_factory: фабрика UnitOfWork.
        embedder: эмбеддер активной модели; None — только полнотекстовый индекс.
    """

    def __init__(self, uow_factory: Callable[[], UnitOfWork], embedder: Embedder | None) -> None:
        self._uow = uow_factory
        self._embedder = embedder

    async def index(self, document_id: int, final_attempt: bool = True) -> DocumentStatus:
        """Индексирует один документ.

        Args:
            document_id: id документа.
            final_attempt: последняя ли это попытка Celery. Если нет — ошибка пробрасывается, и Celery
                повторит задачу с backoff; на последней попытке документ помечается failed с текстом
                ошибки.

        Returns:
            Итоговый статус документа (ready или failed). Удалённый документ — failed без исключения.
        """
        try:
            return await self._index(document_id)
        except NotFound:
            log.warning("index_document: document %s not found, skipping", document_id)
            return DocumentStatus.FAILED
        except Exception as e:
            if not final_attempt:
                raise
            log.exception("index_document %s failed", document_id)
            async with self._uow() as uow:
                await uow.documents.set_status(
                    document_id, DocumentStatus.FAILED, f"{type(e).__name__}: {e}"[:500]
                )
                await uow.commit()
            return DocumentStatus.FAILED

    async def _index(self, document_id: int) -> DocumentStatus:
        """Собственно индексация в два шага.

        1. Транзакция: статус indexing, замена фрагментов документа новыми (повторная индексация
          идемпотентна).
        2. Вне транзакции — эмбеддинги; затем вторая транзакция: векторы + статус ready.
        """
        async with self._uow() as uow:
            doc = await uow.documents.get(document_id)
            if doc is None:
                raise NotFound(str(document_id))
            await uow.documents.set_status(document_id, DocumentStatus.INDEXING)
            chunks = chunk_paragraphs(doc.text or "")
            saved = await uow.snippets.replace_for_document(
                document_id,
                [
                    Snippet(
                        document_id=document_id,
                        ord=c.ord,
                        text=c.text,
                        char_start=c.char_start,
                        char_end=c.char_end,
                        text_sha256=c.text_sha256,
                    )
                    for c in chunks
                ],
            )
            await uow.commit()

        if self._embedder is not None and saved:
            vectors = await self._embedder.embed([s.text for s in saved])
            items = [(s.id or 0, s.text_sha256, v) for s, v in zip(saved, vectors, strict=True)]
        else:
            items = []
        async with self._uow() as uow:
            if items and self._embedder is not None:
                await uow.snippets.upsert_embeddings(self._embedder.model_name, items)
            await uow.documents.set_status(document_id, DocumentStatus.READY)
            await uow.commit()
        return DocumentStatus.READY
