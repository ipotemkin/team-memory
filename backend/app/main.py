"""Точка входа FastAPI: composition root → роутеры, обработчики ошибок, аудит, CORS.

``app`` — модуль-уровневое приложение для uvicorn; тесты вызывают ``create_app`` со своим контейнером.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, ai, ask, documents, errors, qa_runs
from app.api.audit import AuditMiddleware
from app.api.health import router as health_router
from app.composition import Container, build_container, build_health_service
from app.config import Settings, get_settings
from app.observability import RequestContextMiddleware, make_metrics, setup_logging
from app.services.health import HealthService


def create_app(
    settings: Settings | None = None,
    container: Container | None = None,
    health_service: HealthService | None = None,
    admin_health_service: HealthService | None = None,
) -> FastAPI:
    """Создаёт FastAPI-приложение.

    Args:
        settings: настройки (по умолчанию из контейнера или окружения).
        container: готовый контейнер (тесты); тогда логирование, startup и закрытие — на вызывающем.
        health_service, admin_health_service: подмена health-сервисов (тесты).

    Returns:
        Настроенное приложение.
    """
    s = settings or (container.settings if container else get_settings())
    c = container or build_container(s)
    owns = container is None
    if owns:
        setup_logging(s.LOG_FORMAT, s.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if owns:
            await c.run_startup()  # регистрация активной модели эмбеддингов + HNSW-индекс и т.п.
        yield
        if owns:
            await c.aclose()

    app = FastAPI(
        title="Team Memory API",
        version="0.8.0",
        description="База знаний команды: документы, вопросы с источниками, очередь ручной проверки.",
        lifespan=lifespan,
    )
    app.state.container = c
    app.state.health_service = health_service or build_health_service(s)
    app.state.admin_health_service = admin_health_service or c.admin_health_service()
    errors.install(app)
    for r in (health_router, documents.router, ask.router, ai.router, qa_runs.router, admin.router):
        app.include_router(r)
    metrics = make_metrics() if s.METRICS_ENABLED else None
    if metrics is not None:

        @app.get("/metrics", include_in_schema=False)
        async def _metrics() -> Response:
            body, ctype = metrics.render()
            return Response(body, media_type=ctype)

    # Последний добавленный middleware — внешний: CORS → RequestContext (request_id) → Audit,
    # поэтому аудит видит request_id, а CORS-заголовки есть и у ответов с ошибкой.
    app.add_middleware(AuditMiddleware, audit=c.audit_service(), max_body_kb=s.AUDIT_MAX_BODY_KB)
    app.add_middleware(RequestContextMiddleware, metrics=metrics)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    return app


app = create_app()
