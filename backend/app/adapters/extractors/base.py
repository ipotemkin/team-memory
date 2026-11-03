"""Shared format matching for TextExtractor adapters (extension + declared MIME + magic bytes)."""

import re
from typing import ClassVar

# Browsers/curl often send these for any binary/unknown file: rely on extension + magic bytes then.
GENERIC_CONTENT_TYPES = frozenset({"", "application/octet-stream"})
_TRAILING_WS = re.compile(r"[ \t\u00a0]+\n")
_MANY_BLANKS = re.compile(r"\n{3,}")


class FileFormat:
    format: ClassVar[str]
    mime_type: ClassVar[str]
    extensions: ClassVar[frozenset[str]]
    content_types: ClassVar[frozenset[str]]  # accepted declared types (besides generic ones)
    magic: ClassVar[tuple[bytes, ...]] = ()  # empty -> no magic check

    def supports(self, extension: str, content_type: str) -> bool:
        if extension:
            return extension in self.extensions and (
                content_type in self.content_types or content_type in GENERIC_CONTENT_TYPES
            )
        return content_type in self.content_types

    def matches(self, data: bytes) -> bool:
        return not self.magic or any(data.startswith(m) for m in self.magic)

    def extract(self, data: bytes) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


def tidy(text: str) -> str:
    """Normalize line ends, drop trailing spaces, at most one blank line between paragraphs."""
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    t = _TRAILING_WS.sub("\n", t + "\n")
    return _MANY_BLANKS.sub("\n\n", t).strip()
