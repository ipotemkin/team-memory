"""Paragraph chunking (pure function). Snippet text is verbatim from the source."""

import hashlib
import re
from dataclasses import dataclass

MAX_CHUNK_CHARS = 1200
_PARA_SEP = re.compile(r"\n[ \t]*\n")
_SENT_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Chunk:
    ord: int
    text: str
    char_start: int
    char_end: int

    @property
    def text_sha256(self) -> str:
        return sha256_text(self.text)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _spans(text: str, start: int, end: int, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    pos = start
    for m in pattern.finditer(text, start, end):
        out.append((pos, m.start()))
        pos = m.end()
    out.append((pos, end))
    return [_strip(text, a, b) for a, b in out if text[a:b].strip()]


def _strip(text: str, a: int, b: int) -> tuple[int, int]:
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return a, b


def _split_long(text: str, a: int, b: int, max_chars: int) -> list[tuple[int, int]]:
    """Greedily merge sentences of a long paragraph up to max_chars; hard-split giant sentences."""
    pieces: list[tuple[int, int]] = []
    for sa, sb in _spans(text, a, b, _SENT_END):
        while sb - sa > max_chars:
            pieces.append(_strip(text, sa, sa + max_chars))
            sa, sb = _strip(text, sa + max_chars, sb)
        if sb > sa:
            pieces.append((sa, sb))
    merged: list[tuple[int, int]] = []
    for pa, pb in pieces:
        if merged and pb - merged[-1][0] <= max_chars:
            merged[-1] = (merged[-1][0], pb)
        else:
            merged.append((pa, pb))
    return merged


def chunk_paragraphs(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Split by blank lines; paragraphs longer than max_chars are split by sentences."""
    spans: list[tuple[int, int]] = []
    for a, b in _spans(text, 0, len(text), _PARA_SEP):
        spans.extend([(a, b)] if b - a <= max_chars else _split_long(text, a, b, max_chars))
    return [Chunk(i, text[a:b], a, b) for i, (a, b) in enumerate(spans)]
