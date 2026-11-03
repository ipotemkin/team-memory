"""IndexingService: chunk -> snippets -> embed -> upsert embeddings -> status ready / failed.

Runs in the background (Celery task ``index_document``) or inline (tests, QUEUE_BACKEND=inline).
Embeddings are computed outside the DB transaction; the document becomes ``ready`` only after all
of its snippets are embedded with the active model, so search never sees half-indexed documents.
"""

import logging
from collections.abc import Callable

from app.domain.chunking import chunk_paragraphs
from app.domain.errors import NotFound
from app.domain.models import DocumentStatus, Snippet
from app.domain.ports import Embedder, UnitOfWork

log = logging.getLogger("app.indexing")
INDEX_TASK = "index_document"


class IndexingService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], embedder: Embedder | None) -> None:
        self._uow = uow_factory
        self._embedder = embedder

    async def index(self, document_id: int, final_attempt: bool = True) -> DocumentStatus:
        """Index one document. On error: re-raise for a retry, or mark ``failed`` on the last attempt."""
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
