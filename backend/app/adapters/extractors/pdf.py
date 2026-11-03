"""PDF via pypdf: text layer of each page; pages are separated by a blank line.

PDFs without a text layer (scans, images) are rejected: OCR is out of scope.
"""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.adapters.extractors.base import FileFormat, tidy
from app.domain.errors import ExtractionFailed

logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed-but-readable PDFs are noisy


class PdfExtractor(FileFormat):
    format = "pdf"
    mime_type = "application/pdf"
    extensions = frozenset({".pdf"})
    content_types = frozenset({mime_type, "application/x-pdf"})
    magic = (b"%PDF-",)

    def extract(self, data: bytes) -> str:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ExtractionFailed("PDF is password-protected")
            pages = [tidy(page.extract_text() or "") for page in reader.pages]
        except ExtractionFailed:
            raise
        except (PdfReadError, ValueError, KeyError, TypeError, OSError) as e:
            raise ExtractionFailed(f"cannot read PDF: {type(e).__name__}: {e}"[:300]) from None
        text = "\n\n".join(p for p in pages if p)
        if not text:
            raise ExtractionFailed("PDF has no text layer (scanned PDF?); OCR is not supported")
        return text
