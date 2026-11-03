"""AskService: retrieve -> threshold -> ChatLLM -> validate -> verify -> decide -> persist."""

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import TypeAdapter

from app.domain.errors import LLMError, LLMInvalidJSON
from app.domain.guardrails import Decision, decide_review, verify_quotes
from app.domain.models import AiAnswer, AskResult, QaRun, Question, ReviewReason, ScoredSnippet
from app.domain.ports import AuditSink, ChatLLM, Embedder, Retriever, UnitOfWork
from app.domain.prompts import build_context
from app.services.audit import AuditTimer, fan_out

ACTION = "POST /kb/ask"


class AskService:
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
        if self._embedder is None:
            return None
        try:
            return (await self._embedder.embed([question]))[0]
        except Exception:  # noqa: BLE001 - degrade to FTS-only
            return None

    async def ask(self, question: str) -> AskResult:
        q = _validate_question(question)
        timer = AuditTimer(ACTION, {"question": q})
        qvec = await self._embed(q)
        mode: Literal["hybrid", "fts_only"] = "hybrid" if qvec is not None else "fts_only"
        model = self._embedder.model_name if (self._embedder and qvec is not None) else None
        found = await self._retriever.search(q, qvec, model, self._top_k)
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
            src["document_id"] = str(src["document_id"])  # same shape as the public API
        rec = timer.record(
            "needs_review" if run.needs_review else "ok",
            out,
            run.error.value if run.error else None,
        )
        async with self._uow() as uow:
            await uow.qa_runs.add(run)
            await uow.audit.record(rec)
            await uow.commit()
        await fan_out(self._extra, rec)
        return result

    async def _run_llm(
        self, question: str, snippets: list[ScoredSnippet]
    ) -> tuple[Decision, AiAnswer | None]:
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
    return _QUESTION.validate_python(question)
