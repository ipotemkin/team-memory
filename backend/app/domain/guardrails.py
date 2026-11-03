"""Guardrails: quote verification and needs_review decision (pure functions)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.models import INSUFFICIENT_DATA, AiAnswer, ReviewReason, ScoredSnippet, Source

MIN_QUOTE_LEN = 8
_QUOTES = str.maketrans({c: '"' for c in "«»„“”‟″"} | {c: "'" for c in "‘’‚‛′`"})
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―"})
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, ё→е, unify quotes/dashes, collapse whitespace, strip edge punctuation."""
    t = text.lower().replace("ё", "е").translate(_QUOTES).translate(_DASHES)
    t = _WS.sub(" ", t).strip()
    return t.strip(" .,;:!?\"'()[]…")


def _dedupe(sources: list[Source]) -> list[Source]:
    seen: set[tuple[int, str]] = set()
    out: list[Source] = []
    for s in sources:
        key = (s.document_id, normalize(s.quote))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def verify_quotes(answer: AiAnswer, snippets: Sequence[ScoredSnippet]) -> tuple[list[Source], list[str]]:
    """Each quote must be a normalized literal substring of a retrieved snippet → its document_id."""
    normed = [(s, normalize(s.text)) for s in snippets]
    verified: list[Source] = []
    bad: list[str] = []
    for src in answer.sources:
        q = normalize(src.quote)
        hit = next((s for s, n in normed if len(q) >= MIN_QUOTE_LEN and q in n), None)
        if hit is not None:
            verified.append(Source(document_id=hit.document_id, quote=src.quote.strip()))
        else:
            bad.append(src.quote)
    return _dedupe(verified), bad


@dataclass(frozen=True)
class Decision:
    answer: str
    sources: list[Source]
    needs_review: bool
    reason: ReviewReason | None

    def __post_init__(self) -> None:
        check_invariants(self.needs_review, self.sources, self.reason)


def check_invariants(needs_review: bool, sources: Sequence[Source], reason: ReviewReason | None) -> None:
    if not needs_review and not sources:
        raise AssertionError("needs_review=false requires non-empty sources")
    if needs_review and reason is None:
        raise AssertionError("needs_review=true requires a reason")
    if not needs_review and reason is not None:
        raise AssertionError("reason set but needs_review=false")


def decide_review(
    answer: AiAnswer | None,
    verified: Sequence[Source],
    unverified: Sequence[str],
    llm_error: ReviewReason | None = None,
) -> Decision:
    reason: ReviewReason | None = llm_error
    if reason is None and answer is None:
        reason = ReviewReason.NO_SOURCES
    if reason is None and answer is not None and (answer.confidence == "low" or answer.needs_review):
        reason = ReviewReason.LOW_CONFIDENCE
    if reason is None and unverified:
        reason = ReviewReason.UNVERIFIED_QUOTE
    if reason is None and not verified:
        reason = ReviewReason.NO_SOURCES
    if reason is None:
        assert answer is not None
        return Decision(answer.answer, list(verified), False, None)
    # unverified_quote: keep confirmed quotes for the reviewer
    keep = list(verified) if reason is ReviewReason.UNVERIFIED_QUOTE else []
    return Decision(INSUFFICIENT_DATA, keep, True, reason)
