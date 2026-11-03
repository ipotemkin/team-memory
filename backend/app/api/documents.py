"""/kb/documents: JSON {title,text} per spec, or multipart file upload (extension)."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api import deps
from app.api.errors import ApiError
from app.api.ids import parse_id
from app.api.schemas import ERRORS, DocumentCreated, DocumentDetail, DocumentIn, DocumentListItem, ErrorOut
from app.config import Settings
from app.domain.models import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX, Page
from app.services.documents import DocumentService

router = APIRouter(prefix="/kb/documents", tags=["documents"])

TEXT_TYPES = {
    "text/plain": "text/plain",
    "text/markdown": "text/markdown",
    "text/x-markdown": "text/markdown",
}
EXT_TYPES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown"}

_BODY_DOC: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": DocumentIn.model_json_schema()},
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "title": {"type": "string", "maxLength": 200},
                        "file": {"type": "string", "format": "binary", "description": ".txt / .md, UTF-8"},
                    },
                }
            },
        },
    }
}


def _mime(filename: str, content_type: str | None) -> str | None:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in TEXT_TYPES:
        return TEXT_TYPES[ct]
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in EXT_TYPES and ct in ("", "application/octet-stream", "text/plain"):
        return EXT_TYPES[ext]
    return None


@router.post(
    "",
    response_model=DocumentCreated,
    responses={**ERRORS, 413: {"model": ErrorOut}, 415: {"model": ErrorOut}},
    openapi_extra=_BODY_DOC,
    summary="Добавить документ (JSON по ТЗ или multipart-файл)",
)
async def create_document(
    request: Request,
    svc: Annotated[DocumentService, Depends(deps.documents)],
    s: Annotated[Settings, Depends(deps.settings)],
) -> DocumentCreated:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype == "application/json":
        try:
            payload = json.loads(await request.body() or b"null")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "bad_request", "Invalid JSON body") from None
        if not isinstance(payload, dict):
            raise ApiError(400, "bad_request", "JSON object expected")
        body = DocumentIn.model_validate(payload)  # ValidationError -> 422
        doc = await svc.add(body.title, body.text)
    elif ctype == "multipart/form-data":
        doc = await _upload(request, svc, s)
    else:
        raise ApiError(415, "unsupported_media_type", "use application/json or multipart/form-data")
    deps.mark_audited(request)
    return DocumentCreated(document_id=str(doc.id), document_status=doc.status)


async def _upload(request: Request, svc: DocumentService, s: Settings) -> Any:
    limit = s.MAX_UPLOAD_MB * 1024 * 1024
    declared = int(request.headers.get("content-length") or 0)
    if declared > limit + 64 * 1024:
        raise ApiError(413, "file_too_large", f"max {s.MAX_UPLOAD_MB} MB")
    form = await request.form(max_files=1, max_fields=5)
    file = form.get("file")
    if file is None or isinstance(file, str):
        raise ApiError(422, "validation_error", [{"field": "file", "msg": "Field required"}])
    data = await file.read()
    if len(data) > limit:
        raise ApiError(413, "file_too_large", f"max {s.MAX_UPLOAD_MB} MB")
    filename = file.filename or "file"
    mime = _mime(filename, file.content_type)
    if mime is None:
        raise ApiError(415, "unsupported_media_type", "supported: text/plain (.txt), text/markdown (.md)")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(415, "unsupported_media_type", "text must be UTF-8") from None
    raw_title = form.get("title")
    title = raw_title if isinstance(raw_title, str) and raw_title.strip() else filename.rsplit(".", 1)[0]
    DocumentIn(title=title, text=text)  # same limits as JSON (ValidationError -> 422)
    return await svc.add_file(title, filename, data, mime, text)


@router.get("", response_model=list[DocumentListItem], responses={422: {"model": ErrorOut}})
async def list_documents(
    svc: Annotated[DocumentService, Depends(deps.documents)],
    limit: Annotated[int, Query(ge=1, le=PAGE_LIMIT_MAX)] = PAGE_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentListItem]:
    docs = await svc.list(Page(limit=limit, offset=offset))
    return [
        DocumentListItem(
            id=str(d.id), title=d.title, created_at=d.created_at, status=d.status, status_error=d.status_error
        )
        for d in docs
    ]


@router.get("/{document_id}", response_model=DocumentDetail, responses={404: {"model": ErrorOut}})
async def get_document(
    document_id: str,
    svc: Annotated[DocumentService, Depends(deps.documents)],
    s: Annotated[Settings, Depends(deps.settings)],
    lines: Annotated[int | None, Query(ge=1, le=100_000, description="вернуть только первые N строк")] = None,
) -> DocumentDetail:
    doc = await svc.get(parse_id(document_id, "document"))
    all_lines = (doc.text or "").splitlines()
    text = doc.text if lines is None else "\n".join(all_lines[:lines])
    return DocumentDetail(
        id=str(doc.id),
        status_error=doc.status_error,
        title=doc.title,
        created_at=doc.created_at,
        status=doc.status,
        text=text,
        lines_total=len(all_lines),
        lines_returned=len(all_lines) if lines is None else min(lines, len(all_lines)),
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        sha256=doc.sha256,
        download_url=await svc.download_url(doc, s.DOWNLOAD_URL_TTL_S),
        snippets_count=len(await svc.snippets(doc.id)),
    )
