"""Нарезка текста документа на фрагменты (snippets) по абзацам — чистая функция домена.

Используется ``services/indexing.py``: каждый фрагмент сохраняется в БД, попадает в полнотекстовый
индекс и получает эмбеддинг. Текст фрагмента — дословный срез исходного текста
(``text[char_start:char_end]``), поэтому цитаты из ответа LLM можно проверить подстрокой.
"""

import hashlib
import re
from dataclasses import dataclass

# Максимальная длина фрагмента: компромисс между точностью поиска и полнотой контекста для LLM.
MAX_CHUNK_CHARS = 1200
# Абзацы разделяются пустой строкой (строка может содержать пробелы/табы).
_PARA_SEP = re.compile(r"\n[ \t]*\n")
# Конец предложения: пробел после . ! ? или многоточия (lookbehind не «съедает» знак).
_SENT_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Chunk:
    """Фрагмент документа: порядковый номер, дословный текст и его позиция в исходном тексте."""

    ord: int
    text: str
    char_start: int
    char_end: int

    @property
    def text_sha256(self) -> str:
        """SHA-256 текста: по нему определяется, устарел ли эмбеддинг фрагмента."""
        return sha256_text(self.text)


def sha256_text(text: str) -> str:
    """Хеш SHA-256 строки в UTF-8 (hex)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _spans(text: str, start: int, end: int, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    """Делит ``text[start:end]`` по разделителю ``pattern``.

    Возвращает непустые отрезки (a, b) с обрезанными по краям пробелами; координаты — в исходном тексте.
    """
    out: list[tuple[int, int]] = []
    pos = start
    for m in pattern.finditer(text, start, end):
        out.append((pos, m.start()))
        pos = m.end()
    out.append((pos, end))
    return [_strip(text, a, b) for a, b in out if text[a:b].strip()]


def _strip(text: str, a: int, b: int) -> tuple[int, int]:
    """Сдвигает границы (a, b) внутрь, пропуская пробельные символы по краям."""
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return a, b


def _split_long(text: str, a: int, b: int, max_chars: int) -> list[tuple[int, int]]:
    """Режет длинный абзац: жадно склеивает предложения, пока влезает в ``max_chars``.

    Предложение длиннее ``max_chars`` режется «жёстко» по ``max_chars`` символов.
    """
    pieces: list[tuple[int, int]] = []
    for sa, sb in _spans(text, a, b, _SENT_END):
        while sb - sa > max_chars:
            pieces.append(_strip(text, sa, sa + max_chars))
            sa, sb = _strip(text, sa + max_chars, sb)
        if sb > sa:
            pieces.append((sa, sb))
    merged: list[tuple[int, int]] = []
    for pa, pb in pieces:
        # Склеиваем с предыдущим куском, если общий отрезок (вместе с пробелами между) влезает в лимит.
        if merged and pb - merged[-1][0] <= max_chars:
            merged[-1] = (merged[-1][0], pb)
        else:
            merged.append((pa, pb))
    return merged


def chunk_paragraphs(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Делит текст на фрагменты по пустым строкам.

    Абзацы длиннее ``max_chars`` дополнительно режутся по предложениям (``_split_long``).

    Args:
        text: полный текст документа.
        max_chars: максимальная длина фрагмента.

    Returns:
        Список ``Chunk`` с ord = 0..n-1 в порядке следования в тексте.
    """
    spans: list[tuple[int, int]] = []
    for a, b in _spans(text, 0, len(text), _PARA_SEP):
        spans.extend([(a, b)] if b - a <= max_chars else _split_long(text, a, b, max_chars))
    return [Chunk(i, text[a:b], a, b) for i, (a, b) in enumerate(spans)]
