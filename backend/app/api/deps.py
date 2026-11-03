"""FastAPI dependencies: the composition container lives in app.state (wired in main.py)."""

from typing import Any

from fastapi import Request

from app.config import Settings
from app.services.ai_answer import AiAnswerService
from app.services.ask import AskService
from app.services.documents import DocumentService
from app.services.qa_runs import QaRunService


def _c(request: Request) -> Any:
    return request.app.state.container


def settings(request: Request) -> Settings:
    s: Settings = _c(request).settings
    return s


def documents(request: Request) -> DocumentService:
    svc: DocumentService = _c(request).document_service()
    return svc


def ask(request: Request) -> AskService:
    svc: AskService = _c(request).ask_service()
    return svc


def ai(request: Request) -> AiAnswerService:
    svc: AiAnswerService = _c(request).ai_answer_service()
    return svc


def qa_runs(request: Request) -> QaRunService:
    svc: QaRunService = _c(request).qa_run_service()
    return svc


def mark_audited(request: Request) -> None:
    """The service wrote its own audit row; the middleware must not add another."""
    request.state.audited = True
