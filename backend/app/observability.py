"""Наблюдаемость: структурные JSON-логи, middleware X-Request-ID и опциональные Prometheus-метрики /metrics.

request_id хранится в contextvar, поэтому попадает во все логи запроса, в том числе из сервисов.
"""

import contextvars
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
REQUEST_ID_HEADER = "x-request-id"
# Стандартные атрибуты LogRecord — всё остальное пришло из ``extra`` и выводится в JSON.
_STD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Форматтер логов в одну JSON-строку: ts, level, logger, msg, request_id и поля из ``extra``."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            out["request_id"] = rid
        for k, v in vars(record).items():
            if k not in _STD and not k.startswith("_"):
                out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class RequestIdFilter(logging.Filter):
    """Для текстового формата: добавляет ``request_id`` в запись лога."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def setup_logging(fmt: str = "json", level: str = "INFO") -> None:
    """Настраивает корневой логгер (повторный вызов не дублирует обработчик).

    Логи uvicorn перенаправляются в корневой логгер, чтобы формат был единым.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_kb", False):
            root.removeHandler(h)
    h = logging.StreamHandler()
    h._kb = True  # type: ignore[attr-defined]
    if fmt == "json":
        h.setFormatter(JsonFormatter())
    else:
        h.addFilter(RequestIdFilter())
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    root.addHandler(h)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


class _Metrics:
    """Счётчик запросов и гистограмма задержек по методу/шаблону маршрута/статусу (свой registry)."""

    def __init__(self) -> None:
        from prometheus_client import CollectorRegistry, Counter, Histogram

        self.registry = CollectorRegistry()
        self.requests = Counter(
            "kb_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=self.registry
        )
        self.latency = Histogram(
            "kb_http_request_duration_seconds", "HTTP latency", ["method", "route"], registry=self.registry
        )

    def render(self) -> tuple[bytes, str]:
        """Текстовый формат Prometheus и его Content-Type."""
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return generate_latest(self.registry), CONTENT_TYPE_LATEST


def make_metrics() -> _Metrics | None:
    """Метрики, если установлен prometheus-client, иначе None."""
    try:
        return _Metrics()
    except ImportError:  # prometheus-client — необязательная зависимость
        return None


class RequestContextMiddleware:
    """Чистый ASGI: X-Request-ID (входящий или сгенерированный), строка access-лога, опциональные метрики."""

    def __init__(self, app: ASGIApp, metrics: _Metrics | None = None) -> None:
        self.app = app
        self.metrics = metrics
        self.log = logging.getLogger("app.access")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        # Входящий id обрезается до 100 символов (защита логов); state["request_id"] читает аудит.
        rid = headers.get(REQUEST_ID_HEADER, "")[:100] or uuid.uuid4().hex
        token = request_id_var.set(rid)
        scope.setdefault("state", {})["request_id"] = rid
        status = {"code": 500}
        t0 = time.perf_counter()

        async def snd(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
                msg["headers"] = [*msg.get("headers", []), (b"x-request-id", rid.encode())]
            await send(msg)

        try:
            await self.app(scope, receive, snd)
        finally:
            dt = time.perf_counter() - t0
            # Метки по шаблону маршрута, а не пути: иначе кардинальность метрик растёт с каждым id.
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            if self.metrics is not None and scope["path"] != "/metrics":
                self.metrics.requests.labels(scope["method"], route, str(status["code"])).inc()
                self.metrics.latency.labels(scope["method"], route).observe(dt)
            self.log.info(
                "%s %s %s",
                scope["method"],
                scope["path"],
                status["code"],
                extra={"status": status["code"], "duration_ms": int(dt * 1000), "route": route},
            )
            request_id_var.reset(token)
