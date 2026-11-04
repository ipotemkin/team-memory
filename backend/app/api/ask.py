"""/kb/ask — RAG-ответ по базе знаний. Всегда 200: проблемы (нет фрагментов, ошибка/отказ LLM, цитата
не найдена) превращаются в needs_review=true с причиной в qa_runs и аудите.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api import deps
from app.api.schemas import ERRORS, AskIn, AskOut, sources_out
from app.services.ask import AskService

router = APIRouter(prefix="/kb", tags=["ask"])


@router.post("/ask", response_model=AskOut, responses=ERRORS, summary="Вопрос к базе знаний")
async def ask(body: AskIn, request: Request, svc: Annotated[AskService, Depends(deps.ask)]) -> AskOut:
    res = await svc.ask(body.question)
    deps.mark_audited(request)
    return AskOut(answer=res.answer, sources=sources_out(res.sources), needs_review=res.needs_review)
