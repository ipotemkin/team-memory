"""Audit middleware (pure ASGI).

Services audit their own successful / needs_review / LLM-error commands inside the UoW and the
handler sets ``request.state.audited``. Everything else that a command (POST/PUT/PATCH/DELETE)
ends with — validation errors (400/413/415/422), 404, unexpected 500 — is recorded here, so every
command leaves exactly one audit row.
"""

import json
import logging
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domain.models import AuditRecord, AuditStatus
from app.services.audit import AuditService

log = logging.getLogger("app.audit")
COMMANDS = {"POST", "PUT", "PATCH", "DELETE"}
_MAX_OUT = 4096


def _decode(raw: bytes, ctype: str, max_bytes: int) -> dict[str, Any]:
    if ctype.startswith("multipart/"):
        return {"content_type": "multipart/form-data", "size_bytes": len(raw)}
    clipped = raw[:max_bytes]
    try:
        val = json.loads(clipped)
        return val if isinstance(val, dict) else {"body": val}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw": clipped.decode("utf-8", "replace"), "truncated": len(raw) > max_bytes}


def _rid(scope: Scope) -> dict[str, Any]:
    """Reuse the X-Request-ID as the audit request_id when it is a UUID."""
    raw = (scope.get("state") or {}).get("request_id")
    try:
        return {"request_id": uuid.UUID(str(raw))} if raw else {}
    except ValueError:
        return {}


class AuditMiddleware:
    def __init__(self, app: ASGIApp, audit: AuditService, max_body_kb: int = 10) -> None:
        self.app = app
        self.audit = audit
        self.max_bytes = max_body_kb * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in COMMANDS:
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()
        body = bytearray()
        status = {"code": 500}
        out = bytearray()

        async def recv() -> Message:
            msg = await receive()
            if msg["type"] == "http.request" and len(body) <= self.max_bytes:
                body.extend(msg.get("body", b""))
            return msg

        async def snd(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
            elif msg["type"] == "http.response.body" and len(out) < _MAX_OUT:
                out.extend(msg.get("body", b""))
            await send(msg)

        try:
            await self.app(scope, recv, snd)
        finally:
            code = status["code"]
            state = scope.get("state") or {}
            if code >= 400 and not state.get("audited"):
                await self._record(scope, bytes(body), code, bytes(out), t0)

    async def _record(self, scope: Scope, body: bytes, code: int, out: bytes, t0: float) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        route = scope.get("route")
        path = getattr(route, "path", None) or scope["path"]
        try:
            output = json.loads(out) if out else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            output = {"raw": out[:512].decode("utf-8", "replace")}
        err = output.get("error") if isinstance(output, dict) else None
        st: AuditStatus = "client_error" if code < 500 else "error"
        rec = AuditRecord(
            action=f"{scope['method']} {path}"[:100],
            input=_decode(body, headers.get("content-type", ""), self.max_bytes),
            output={"http_status": code, **(output if isinstance(output, dict) else {})},
            status=st,
            error=str(err or f"http_{code}"),
            duration_ms=max(0, int((time.perf_counter() - t0) * 1000)),
            **_rid(scope),
        )
        try:
            await self.audit.record(rec)
        except Exception:  # audit must never break the response path
            log.exception("audit write failed for %s", rec.action)
