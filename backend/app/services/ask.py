"""AskService — сценарий ``POST /kb/ask`` (вопрос → ответ с цитатами).

Конвейер: эмбеддинг вопроса → гибридный поиск (порт Retriever) → отсечение по порогу MIN_SCORE →
ChatLLM (строгий JSON) → проверка цитат и решение needs_review (``domain/guardrails``) →
сохранение QaRun и записи аудита в одной транзакции (UnitOfWork) → дополнительные приёмники аудита.
"""

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import TypeAdapter

from app.domain.errors import LLMError, LLMInvalidJSON
from app.domain.guardrails import Decision, decide_review, verify_quotes
from app.domain.models import AiAnswer, AskResult, QaRun, Question, ReviewReason, ScoredSnippet
from app.domain.ports import AuditSink, ChatLLM, Embedder, Retriever, UnitOfWork
from app.domain.prompts import build_context
from app.services.audit import AuditTimer, fan_out

# Имя действия в аудите.
ACTION = "POST /kb/ask"


class AskService:
    """Отвечает на вопросы по базе знаний с проверяемыми цитатами.

    Args:
        uow_factory: фабрика UnitOfWork (транзакция для qa_runs + аудита).
        retriever: гибридный поиск фрагментов.
        embedder: эмбеддер вопроса; None — работаем только полнотекстовым поиском.
        chat: чат-модель.
        top_k: сколько фрагментов брать из поиска.
        min_score: порог релевантности (MIN_SCORE); фрагменты ниже порога не идут в LLM.
        extra_audit_sinks: нетранзакционные приёмники аудита (например, ClickHouse).
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        retriever: Retriever,
        embedder: Embedder | None,
        chat: ChatLLM,
        top_k: int = 5,
        min_score: float = 0.6,
        extra_audit_sinks: Sequence[AuditSink] = (),
    ) -> None:
        self._uow = uow_factory
        self._retriever = retriever
        self._embedder = embedder
        self._chat = chat
        self._top_k = top_k
        self._min_score = min_score
        self._extra = list(extra_audit_sinks)

    async def _embed(self, question: str) -> list[float] | None:
        """Эмбеддинг вопроса или None.

        Ошибка эмбеддера не валит запрос: деградируем до полнотекстового поиска
        (retrieval_mode=fts_only).
        """
        if self._embedder is None:
            return None
        try:
            return (await self._embedder.embed([question]))[0]
        except Exception:  # noqa: BLE001 - деградация до FTS-only
            return None

    async def ask(self, question: str) -> AskResult:
        """Отвечает на вопрос и сохраняет запуск в историю.

        Returns:
            ``AskResult``; при ``needs_review=true`` ответ — стандартный текст ``INSUFFICIENT_DATA``.

        Raises:
            pydantic.ValidationError: вопрос короче/длиннее допустимого (API отдаёт 422).
        """
        q = _validate_question(question)
        timer = AuditTimer(ACTION, {"question": q})
        qvec = await self._embed(q)
        mode: Literal["hybrid", "fts_only"] = "hybrid" if qvec is not None else "fts_only"
        model = self._embedder.model_name if (self._embedder and qvec is not None) else None
        found = await self._retriever.search(q, qvec, model, self._top_k)
        # Порог MIN_SCORE: слабые совпадения отбрасываем — лучше «данных недостаточно», чем
        # ответ по нерелевантному тексту. Нет фрагментов → LLM не вызываем (needs_review, no_sources).
        snippets = [s for s in found if s.score >= self._min_score]

        answer: AiAnswer | None = None
        decision: Decision
        if not snippets:
            decision = decide_review(None, [], [])
        else:
            decision, answer = await self._run_llm(q, snippets)

        run = QaRun(
            question=q,
            answer=decision.answer,
            sources=decision.sources,
            needs_review=decision.needs_review,
            error=decision.reason,
            confidence=answer.confidence if answer else None,
            chat_model=self._chat.model_name if snippets else None,
            embedding_model=model,
            retrieval_mode=mode,
            retrieved_ids=[s.snippet_id for s in snippets],
        )
        result = AskResult(answer=run.answer, sources=run.sources, needs_review=run.needs_review)
        out = result.model_dump(mode="json")
        for src in out["sources"]:
            src["document_id"] = str(src["document_id"])  # как в публичном API: id строкой
        rec = timer.record(
            "needs_review" if run.needs_review else "ok",
            out,
            run.error.value if run.error else None,
        )
        # QaRun и аудит пишутся в одной транзакции: в истории не бывает ответа без записи аудита.
        async with self._uow() as uow:
            await uow.qa_runs.add(run)
            await uow.audit.record(rec)
            await uow.commit()
        await fan_out(self._extra, rec)
        return result

    async def _run_llm(
        self, question: str, snippets: list[ScoredSnippet]
    ) -> tuple[Decision, AiAnswer | None]:
        """Вызывает LLM и применяет guardrails.

        Ошибки LLM не пробрасываются, а превращаются в needs_review с причиной
        llm_error/llm_invalid_json.
        """
        try:
            answer = await self._chat.answer_with_sources(question, build_context(snippets))
        except LLMInvalidJSON:
            return decide_review(None, [], [], ReviewReason.LLM_INVALID_JSON), None
        except LLMError:
            return decide_review(None, [], [], ReviewReason.LLM_ERROR), None
        verified, bad = verify_quotes(answer, snippets)
        return decide_review(answer, verified, bad), answer


_QUESTION: TypeAdapter[str] = TypeAdapter(Question)


def _validate_question(question: str) -> str:
    """Проверяет вопрос по ограничениям доменного типа ``Question`` (обрезка пробелов, длина)."""
    return _QUESTION.validate_python(question)
