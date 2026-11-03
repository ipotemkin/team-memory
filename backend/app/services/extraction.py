"""TextExtractionService: pick the TextExtractor for an upload and get its text.

Checks, in order: format allowed (UPLOAD_FORMATS) by extension / declared MIME -> magic bytes ->
extract. Unsupported or mismatching files -> UnsupportedFile (415); unreadable / no text ->
ExtractionFailed (422). Extraction runs in a worker thread (CPU-bound parsing).
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.domain.errors import ExtractionFailed, UnsupportedFile
from app.domain.ports import TextExtractor

log = logging.getLogger("app.extraction")


@dataclass(frozen=True)
class ExtractedText:
    text: str
    mime_type: str
    format: str


def extension_of(filename: str) -> str:
    return PurePosixPath(filename or "").suffix.lower()


class TextExtractionService:
    def __init__(self, extractors: Sequence[TextExtractor]) -> None:
        self._extractors = list(extractors)

    @property
    def formats(self) -> list[str]:
        return [e.format for e in self._extractors]

    def resolve(self, filename: str, content_type: str | None) -> TextExtractor:
        ext = extension_of(filename)
        ct = (content_type or "").split(";")[0].strip().lower()
        for e in self._extractors:
            if e.supports(ext, ct):
                return e
        raise UnsupportedFile(f"supported formats: {', '.join(self.formats)} (got {ext or ct or 'unknown'})")

    async def extract(self, filename: str, content_type: str | None, data: bytes) -> ExtractedText:
        ex = self.resolve(filename, content_type)
        if not ex.matches(data):
            raise UnsupportedFile(f"file content does not look like {ex.format.upper()}")
        try:
            text = await asyncio.to_thread(ex.extract, data)
        except (UnsupportedFile, ExtractionFailed):
            raise
        except Exception as e:  # noqa: BLE001 - parser bug / exotic file: a clear 422 instead of a 500
            log.warning("extraction of %s failed: %r", filename, e)
            raise ExtractionFailed(f"cannot read {ex.format.upper()}: {type(e).__name__}") from None
        return ExtractedText(text=text, mime_type=ex.mime_type, format=ex.format)
