"""Uniform error body {"error": code, "detail": ...} and exception handlers."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import Conflict, LLMError, LLMInvalidJSON, NotFound


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, detail: Any = None) -> None:
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.detail = detail


def error_response(status: int, error: str, detail: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


def field_errors(errors: Any) -> list[dict[str, str]]:
    out = []
    for e in errors:
        loc = [str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path")]
        out.append({"field": ".".join(loc) or "body", "msg": str(e.get("msg", ""))})
    return out


def _validation(errors: Any) -> JSONResponse:
    errs = list(errors)
    if any(e.get("type") == "json_invalid" for e in errs):
        return error_response(400, "bad_request", "Invalid JSON body")
    return error_response(422, "validation_error", field_errors(errs))


def install(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _rve(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _validation(exc.errors())

    @app.exception_handler(ValidationError)
    async def _ve(_: Request, exc: ValidationError) -> JSONResponse:
        return error_response(422, "validation_error", field_errors(exc.errors()))

    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.error, exc.detail)

    @app.exception_handler(NotFound)
    async def _nf(_: Request, exc: NotFound) -> JSONResponse:
        return error_response(404, "not_found", str(exc))

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
        return error_response(409, "conflict", str(exc))

    @app.exception_handler(LLMInvalidJSON)
    async def _inv(_: Request, exc: LLMInvalidJSON) -> JSONResponse:
        return error_response(502, "llm_invalid_json", str(exc) or "schema validation failed")

    @app.exception_handler(LLMError)
    async def _llm(_: Request, exc: LLMError) -> JSONResponse:
        return error_response(502, "llm_error", str(exc) or "upstream error")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            404: "not_found",
            405: "method_not_allowed",
            413: "file_too_large",
            415: "unsupported_media_type",
        }.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, exc.detail)

    @app.exception_handler(Exception)
    async def _any(_: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "internal_error", type(exc).__name__)
