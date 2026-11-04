"""/ai/answer_with_sources — строгий JSON AiAnswer по готовому контексту; 502 при ошибках LLM."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api import deps
from app.api.schemas import ERRORS, AiAnswerOut, ErrorOut
from app.domain.errors import LLMError, LLMInvalidJSON
from app.services.ai_answer import AiAnswerRequest, AiAnswerService

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post(
    "/answer_with_sources",
    response_model=AiAnswerOut,
    responses={**ERRORS, 502: {"model": ErrorOut, "description": "llm_error / llm_invalid_json"}},
)
async def answer_with_sources(
    body: AiAnswerRequest, request: Request, svc: Annotated[AiAnswerService, Depends(deps.ai)]
) -> AiAnswerOut:
    try:
        ans = await svc.answer(body.question, body.context)
    except (LLMError, LLMInvalidJSON):
        deps.mark_audited(request)  # сервис уже записал аудит status=error перед пробросом
        raise
    deps.mark_audited(request)
    return AiAnswerOut.model_validate(ans.model_dump())
