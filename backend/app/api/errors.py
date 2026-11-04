"""Единое тело ошибки {"error": код, "detail": ...} и обработчики исключений.

Доменные исключения (``app.domain.errors``) отображаются здесь в HTTP-коды — домен о HTTP не знает.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import Conflict, ExtractionFailed, LLMError, LLMInvalidJSON, NotFound, UnsupportedFile


class ApiError(Exception):
    """Ошибка HTTP-слоя с явным кодом и телом.

    Args:
        status_code: HTTP-статус.
        error: машинный код ошибки (``not_found``, ``file_too_large``...).
        detail: подробности (строка или список ошибок полей).
    """

    def __init__(self, status_code: int, error: str, detail: Any = None) -> None:
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.detail = detail


def error_response(status: int, error: str, detail: Any = None) -> JSONResponse:
    """Ответ в едином формате ошибки."""
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


def field_errors(errors: Any) -> list[dict[str, str]]:
    """Ошибки pydantic → [{field, msg}]; служебные префиксы loc (body/query/path) убираются."""
    out = []
    for e in errors:
        loc = [str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path")]
        out.append({"field": ".".join(loc) or "body", "msg": str(e.get("msg", ""))})
    return out


def _validation(errors: Any) -> JSONResponse:
    """Битый JSON → 400 bad_request, остальные ошибки валидации → 422."""
    errs = list(errors)
    if any(e.get("type") == "json_invalid" for e in errs):
        return error_response(400, "bad_request", "Invalid JSON body")
    return error_response(422, "validation_error", field_errors(errs))


def install(app: FastAPI) -> None:
    """Регистрирует обработчики исключений в приложении.

    Отображение: NotFound→404, UnsupportedFile→415, ExtractionFailed→422, Conflict→409,
    LLMError/LLMInvalidJSON→502, прочее→500 (наружу только имя класса исключения).
    """

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

    @app.exception_handler(UnsupportedFile)
    async def _unsupported(_: Request, exc: UnsupportedFile) -> JSONResponse:
        return error_response(415, "unsupported_media_type", str(exc))

    @app.exception_handler(ExtractionFailed)
    async def _extraction(_: Request, exc: ExtractionFailed) -> JSONResponse:
        return error_response(422, "extraction_failed", str(exc))

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
