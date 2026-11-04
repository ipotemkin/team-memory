"""AiAnswerService — сценарий ``POST /ai/answer_with_sources``.

В отличие от /kb/ask, поиска нет: вопрос и контекст присылает клиент, сервис только вызывает
ChatLLM и пишет аудит. Ошибки LLM пробрасываются наверх (API превращает их в HTTP-ошибку).
"""

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from app.domain.errors import LLMError, LLMInvalidJSON
from app.domain.models import AiAnswer, AuditRecord, AuditStatus, Context, Question, ReviewReason
from app.domain.ports import AuditSink, ChatLLM, UnitOfWork
from app.services.audit import AuditTimer, fan_out

ACTION = "POST /ai/answer_with_sources"


# Валидация входа (те же ограничения, что у API); лишние поля запрещены.
class AiAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Question
    context: Context


class AiAnswerService:
    """Прямой вызов ChatLLM по контексту клиента с записью аудита."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        chat: ChatLLM,
        extra_audit_sinks: Sequence[AuditSink] = (),
    ) -> None:
        self._uow = uow_factory
        self._chat = chat
        self._extra = list(extra_audit_sinks)

    async def answer(self, question: str, context: str) -> AiAnswer:
        """Возвращает ответ LLM по вопросу и контексту.

        Raises:
            pydantic.ValidationError: вопрос/контекст не прошли ограничения длины.
            LLMError, LLMInvalidJSON: ошибка LLM (после записи аудита со статусом error).
        """
        req = AiAnswerRequest(question=question, context=context)
        timer = AuditTimer(ACTION, {"question": req.question, "context_len": len(req.context)})
        try:
            ans = await self._chat.answer_with_sources(req.question, req.context)
        except (LLMInvalidJSON, LLMError) as exc:
            code = (
                ReviewReason.LLM_INVALID_JSON if isinstance(exc, LLMInvalidJSON) else ReviewReason.LLM_ERROR
            )
            await self._audit(timer.record("error", None, code.value))
            raise
        # В аудите помечаем ответы, которые сама LLM считает ненадёжными.
        status: AuditStatus = "needs_review" if ans.needs_review or ans.confidence == "low" else "ok"
        await self._audit(timer.record(status, ans.model_dump(mode="json")))
        return ans

    async def _audit(self, rec: AuditRecord) -> None:
        """Пишет запись аудита в БД (отдельная транзакция) и в дополнительные приёмники."""
        async with self._uow() as uow:
            await uow.audit.record(rec)
            await uow.commit()
        await fan_out(self._extra, rec)
