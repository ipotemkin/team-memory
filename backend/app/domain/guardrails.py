"""Защитные проверки ответа LLM (чистые функции домена).

Вызываются из ``services/ask.py`` после ответа ChatLLM:

1. ``verify_quotes`` — каждая цитата должна быть дословной (после нормализации) подстрокой одного из
   найденных фрагментов; так мы ловим «придуманные» цитаты и узнаём document_id источника.
2. ``decide_review`` — решает, можно ли отдать ответ пользователю или нужна ручная проверка
   (``needs_review``), и с какой причиной (``ReviewReason``).

Инварианты ответа (проверяет ``check_invariants``):
- ``needs_review=false`` ⇒ есть хотя бы один подтверждённый источник и причины нет;
- ``needs_review=true`` ⇒ причина обязательна.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.models import INSUFFICIENT_DATA, AiAnswer, ReviewReason, ScoredSnippet, Source

# Слишком короткая «цитата» (например, «да») совпала бы где угодно — такие не засчитываем.
MIN_QUOTE_LEN = 8
# Таблицы замены для normalize(): все виды кавычек и тире приводятся к ASCII.
_QUOTES = str.maketrans({c: '"' for c in "«»„“”‟″"} | {c: "'" for c in "‘’‚‛′`"})
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―"})
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Нормализует текст для сравнения цитат.

    Нижний регистр, ё→е, «ёлочки»/лапки → прямые кавычки, все тире → дефис, любые пробельные
    последовательности (включая переносы строк из PDF) → один пробел, обрезка пунктуации по краям.
    Так LLM может немного изменить оформление, но не слова.
    """
    t = text.lower().replace("ё", "е").translate(_QUOTES).translate(_DASHES)
    t = _WS.sub(" ", t).strip()
    return t.strip(" .,;:!?\"'()[]…")


def _dedupe(sources: list[Source]) -> list[Source]:
    """Убирает повторы источников: одинаковый документ + одинаковая нормализованная цитата."""
    seen: set[tuple[int, str]] = set()
    out: list[Source] = []
    for s in sources:
        key = (s.document_id, normalize(s.quote))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def verify_quotes(answer: AiAnswer, snippets: Sequence[ScoredSnippet]) -> tuple[list[Source], list[str]]:
    """Проверяет цитаты ответа LLM по найденным фрагментам.

    Цитата подтверждена, если её нормализованный текст длиной не меньше ``MIN_QUOTE_LEN`` входит
    в нормализованный текст какого-либо фрагмента; источник получает document_id этого фрагмента.

    Args:
        answer: проверенный по схеме ответ LLM.
        snippets: фрагменты, переданные LLM в контексте.

    Returns:
        (подтверждённые источники без дублей, список неподтверждённых цитат).
    """
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
    """Итоговое решение по ответу; инварианты проверяются при создании."""

    answer: str
    sources: list[Source]
    needs_review: bool
    reason: ReviewReason | None

    def __post_init__(self) -> None:
        check_invariants(self.needs_review, self.sources, self.reason)


def check_invariants(needs_review: bool, sources: Sequence[Source], reason: ReviewReason | None) -> None:
    """Проверяет инварианты needs_review/sources/reason.

    Raises:
        AssertionError: если комбинация недопустима (это ошибка программы, а не пользователя).
    """
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
    """Решает, нужна ли ручная проверка ответа, и выбирает причину.

    Порядок приоритета причин: ошибка LLM (``llm_error``) → нет ответа (нет фрагментов) →
    низкая уверенность или флаг LLM ``needs_review`` → неподтверждённая цитата → нет подтверждённых
    источников. Если причины нет — отдаём ответ LLM с подтверждёнными источниками.

    Args:
        answer: ответ LLM или None (LLM не вызывалась / упала).
        verified: подтверждённые источники из ``verify_quotes``.
        unverified: неподтверждённые цитаты.
        llm_error: причина, если вызов LLM завершился ошибкой.

    Returns:
        ``Decision``; при needs_review ответ заменяется на стандартный ``INSUFFICIENT_DATA``.
    """
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
    # При unverified_quote сохраняем подтверждённые цитаты — они помогут проверяющему.
    keep = list(verified) if reason is ReviewReason.UNVERIFIED_QUOTE else []
    return Decision(INSUFFICIENT_DATA, keep, True, reason)
