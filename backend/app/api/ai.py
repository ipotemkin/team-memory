"""/ai/answer_with_sources — strict AiAnswer JSON; 502 on LLM errors."""

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
        deps.mark_audited(request)  # the service audited status=error before re-raising
        raise
    deps.mark_audited(request)
    return AiAnswerOut.model_validate(ans.model_dump())
