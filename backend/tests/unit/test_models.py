import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.models import AiAnswer, NewDocument, Page, Question
from app.services.ai_answer import AiAnswerRequest


def test_title_limits() -> None:
    assert NewDocument(title="  T  ", text=" x ").title == "T"
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(ValidationError):
            NewDocument(title=bad, text="x")


def test_text_limits() -> None:
    NewDocument(title="t", text="x" * 100_000)
    with pytest.raises(ValidationError):
        NewDocument(title="t", text="x" * 100_001)
    with pytest.raises(ValidationError):
        NewDocument(title="t", text="  ")


def test_question_limits() -> None:
    ta = TypeAdapter(Question)
    ta.validate_python("abc")
    for bad in ("ab", "x" * 2001):
        with pytest.raises(ValidationError):
            ta.validate_python(bad)


def test_context_limits() -> None:
    AiAnswerRequest(question="вопрос", context="x" * 20_000)
    with pytest.raises(ValidationError):
        AiAnswerRequest(question="вопрос", context="x" * 20_001)


def test_page_limits() -> None:
    with pytest.raises(ValidationError):
        Page(limit=201)
    with pytest.raises(ValidationError):
        Page(offset=-1)


def test_ai_answer_is_strict() -> None:
    ok = {"answer": "a", "sources": [{"quote": "q"}], "confidence": "high", "needs_review": False}
    AiAnswer.model_validate(ok)
    for bad in (
        {**ok, "extra": 1},
        {**ok, "confidence": "sure"},
        {**ok, "needs_review": "false"},
        {**ok, "sources": [{"quote": "q", "document_id": 1}]},
    ):
        with pytest.raises(ValidationError):
            AiAnswer.model_validate(bad)
