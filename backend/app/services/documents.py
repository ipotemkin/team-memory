"""DocumentService: add (text or file), list, get documents; chunk them into snippets."""

from __future__ import annotations

import builtins
import hashlib
import uuid
from collections.abc import Callable
from pathlib import PurePosixPath

from app.domain.chunking import sha256_text
from app.domain.errors import NotFound
from app.domain.models import Document, DocumentStatus, NewDocument, Page, Snippet
from app.domain.ports import FileStorage, TaskQueue, UnitOfWork
from app.services.audit import AuditTimer
from app.services.extraction import TextExtractionService
from app.services.indexing import INDEX_TASK

ACTION = "POST /kb/documents"


class DocumentService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        storage: FileStorage | None = None,
        queue: TaskQueue | None = None,
        extraction: TextExtractionService | None = None,
    ) -> None:
        self._uow = uow_factory
        self._storage = storage
        self._queue = queue
        self._extraction = extraction

    async def add(self, title: str, text: str) -> Document:
        new = NewDocument(title=title, text=text)  # validates limits
        return await self._add(new, {"title": new.title, "text_len": len(new.text)})

    async def add_file(self, title: str, filename: str, data: bytes, content_type: str | None) -> Document:
        """Extract text (txt/md/docx/pdf, see TextExtractionService), store the original in FileStorage,
        then index the extracted text like any other document (snippets are verbatim from it)."""
        if self._extraction is None:
            raise RuntimeError("TextExtractionService is not configured")
        extracted = await self._extraction.extract(filename, content_type, data)
        mime = extracted.mime_type
        new = NewDocument(title=title, text=extracted.text)  # validate before touching storage
        if self._storage is None:
            raise RuntimeError("FileStorage is not configured")
        safe = PurePosixPath(filename or "file").name or "file"
        key = f"documents/{uuid.uuid4().hex}/{safe}"
        obj = await self._storage.put(key, data, mime)
        new = new.model_copy(
            update={
                "storage_bucket": obj.bucket,
                "object_key": obj.key,
                "sha256": hashlib.sha256(data).hexdigest(),
                "mime_type": mime,
                "size_bytes": len(data),
            }
        )
        try:
            return await self._add(
                new,
                {"title": new.title, "filename": safe, "size_bytes": len(data), "format": extracted.format},
            )
        except Exception:
            await self._storage.delete(key)
            raise

    async def _add(self, new: NewDocument, audit_input: dict[str, object]) -> Document:
        """Persist the document as ``indexing`` and enqueue background indexing; return at once."""
        new = new.model_copy(update={"content_sha256": sha256_text(new.text)})
        timer = AuditTimer(ACTION, audit_input)
        async with self._uow() as uow:
            doc = await uow.documents.add(new)
            await uow.documents.set_status(doc.id, DocumentStatus.INDEXING)
            await uow.audit.record(
                timer.record(
                    "ok", {"status": "ok", "document_id": str(doc.id), "document_status": "indexing"}
                )
            )
            await uow.commit()
        if self._queue is not None:
            try:
                await self._queue.enqueue(INDEX_TASK, document_id=doc.id)
            except Exception as e:  # noqa: BLE001 - broker down: keep the document, but make the failure visible
                async with self._uow() as uow:
                    await uow.documents.set_status(
                        doc.id, DocumentStatus.FAILED, f"enqueue failed: {e}"[:500]
                    )
                    await uow.commit()
        return await self.get(doc.id)

    async def list(self, page: Page | None = None) -> builtins.list[Document]:
        async with self._uow() as uow:
            return await uow.documents.list(page or Page())

    async def get(self, document_id: int) -> Document:
        async with self._uow() as uow:
            doc = await uow.documents.get(document_id)
        if doc is None:
            raise NotFound(f"document {document_id} not found")
        return doc

    async def snippets(self, document_id: int) -> builtins.list[Snippet]:
        await self.get(document_id)
        async with self._uow() as uow:
            return await uow.snippets.list_for_document(document_id)

    async def download_url(self, doc: Document, ttl_s: int = 900) -> str | None:
        if not doc.object_key or self._storage is None:
            return None
        try:
            return await self._storage.presign(doc.object_key, ttl_s)
        except NotFound:
            return None
