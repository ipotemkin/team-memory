import pytest

from app.domain.guardrails import Decision, decide_review, normalize, verify_quotes
from app.domain.models import INSUFFICIENT_DATA, AiAnswer, ReviewReason, ScoredSnippet, Source

SNIPS = [
    ScoredSnippet(snippet_id=1, document_id=10, text="Ежедневный  стендап проходит в 11:00.", score=0.9),
    ScoredSnippet(snippet_id=2, document_id=20, text="Ёлка «зелёная» — всегда.", score=0.5),
]


def ans(*quotes: str, confidence: str = "high", needs_review: bool = False) -> AiAnswer:
    return AiAnswer.model_validate(
        {
            "answer": "ok",
            "sources": [{"quote": q} for q in quotes],
            "confidence": confidence,
            "needs_review": needs_review,
        }
    )


def test_normalize() -> None:
    assert normalize("  Ёлка «Зелёная»\n— ВСЕГДА. ") == 'елка "зеленая" - всегда'


def test_verify_maps_to_document_and_normalizes() -> None:
    verified, bad = verify_quotes(
        ans("стендап ПРОХОДИТ в 11:00", 'елка "зеленая"', "выдумка про обед"), SNIPS
    )
    assert verified == [
        Source(document_id=10, quote="стендап ПРОХОДИТ в 11:00"),
        Source(document_id=20, quote='елка "зеленая"'),
    ]
    assert bad == ["выдумка про обед"]


def test_short_quote_rejected_and_dupes_removed() -> None:
    verified, bad = verify_quotes(ans("11:00", "стендап проходит", "Стендап  проходит"), SNIPS)
    assert bad == ["11:00"]
    assert len(verified) == 1


def test_decide_ok() -> None:
    d = decide_review(ans("x"), [Source(document_id=1, quote="q")], [])
    assert d == Decision("ok", [Source(document_id=1, quote="q")], False, None)


@pytest.mark.parametrize(
    ("answer", "verified", "unverified", "err", "reason", "kept"),
    [
        (None, [], [], None, ReviewReason.NO_SOURCES, 0),
        (ans(), [], [], None, ReviewReason.NO_SOURCES, 0),
        (ans(confidence="low"), [Source(document_id=1, quote="q")], [], None, ReviewReason.LOW_CONFIDENCE, 0),
        (
            ans(needs_review=True),
            [Source(document_id=1, quote="q")],
            [],
            None,
            ReviewReason.LOW_CONFIDENCE,
            0,
        ),
        (ans(), [Source(document_id=1, quote="q")], ["bad"], None, ReviewReason.UNVERIFIED_QUOTE, 1),
        (ans(), [], ["bad"], None, ReviewReason.UNVERIFIED_QUOTE, 0),
        (None, [], [], ReviewReason.LLM_ERROR, ReviewReason.LLM_ERROR, 0),
        (None, [], [], ReviewReason.LLM_INVALID_JSON, ReviewReason.LLM_INVALID_JSON, 0),
    ],
)
def test_decide_review_reasons(
    answer: AiAnswer | None,
    verified: list[Source],
    unverified: list[str],
    err: ReviewReason | None,
    reason: ReviewReason,
    kept: int,
) -> None:
    d = decide_review(answer, verified, unverified, err)
    assert d.needs_review is True
    assert d.reason is reason
    assert d.answer == INSUFFICIENT_DATA
    assert len(d.sources) == kept


def test_medium_confidence_with_verified_is_auto() -> None:
    d = decide_review(ans(confidence="medium"), [Source(document_id=1, quote="q")], [])
    assert d.needs_review is False


def test_invariants_enforced() -> None:
    with pytest.raises(AssertionError):
        Decision("a", [], False, None)
    with pytest.raises(AssertionError):
        Decision("a", [], True, None)


def test_insufficient_text_is_russian() -> None:
    assert INSUFFICIENT_DATA.startswith("Данных недостаточно")
