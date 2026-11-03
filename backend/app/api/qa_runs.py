"""/kb/qa_runs — Q&A history and review queue."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api import deps
from app.api.ids import parse_id
from app.api.schemas import ErrorOut, QaRunOut, sources_out
from app.domain.models import Page, QaRun
from app.services.qa_runs import QaRunService

router = APIRouter(prefix="/kb/qa_runs", tags=["qa_runs"])
QA_LIMIT_MAX = 100


def _out(r: QaRun) -> QaRunOut:
    return QaRunOut(
        id=str(r.id),
        created_at=r.created_at,
        question=r.question,
        answer=r.answer,
        sources=sources_out(r.sources),
        needs_review=r.needs_review,
        error=r.error,
        confidence=r.confidence,
        chat_model=r.chat_model,
        embedding_model=r.embedding_model,
        retrieval_mode=r.retrieval_mode,
    )


@router.get("", response_model=list[QaRunOut], responses={422: {"model": ErrorOut}})
async def list_qa_runs(
    svc: Annotated[QaRunService, Depends(deps.qa_runs)],
    needs_review: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=QA_LIMIT_MAX)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[QaRunOut]:
    return [_out(r) for r in await svc.list(Page(limit=limit, offset=offset), needs_review)]


@router.get("/{run_id}", response_model=QaRunOut, responses={404: {"model": ErrorOut}})
async def get_qa_run(run_id: str, svc: Annotated[QaRunService, Depends(deps.qa_runs)]) -> QaRunOut:
    return _out(await svc.get(parse_id(run_id, "qa_run")))
