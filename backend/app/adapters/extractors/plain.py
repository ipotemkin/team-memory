"""Plain-text formats (.txt, .md): UTF-8 (BOM allowed), stored as-is."""

from app.adapters.extractors.base import FileFormat
from app.domain.errors import UnsupportedFile


class PlainTextExtractor(FileFormat):
    format = "txt"
    mime_type = "text/plain"
    extensions = frozenset({".txt"})
    content_types = frozenset({"text/plain"})

    def extract(self, data: bytes) -> str:
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UnsupportedFile("text must be UTF-8") from None


class MarkdownExtractor(PlainTextExtractor):
    format = "md"
    mime_type = "text/markdown"
    extensions = frozenset({".md", ".markdown"})
    content_types = frozenset({"text/markdown", "text/x-markdown", "text/plain"})
