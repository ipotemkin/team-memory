"""PDF через pypdf: текстовый слой каждой страницы; страницы разделяются пустой строкой.

PDF без текстового слоя (сканы, картинки) отклоняется: OCR не поддерживается.
"""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.adapters.extractors.base import FileFormat, tidy
from app.domain.errors import ExtractionFailed

logging.getLogger("pypdf").setLevel(logging.ERROR)  # pypdf шумит на «кривых», но читаемых PDF


class PdfExtractor(FileFormat):
    """Адаптер PDF. Сигнатура ``%PDF-``."""

    format = "pdf"
    mime_type = "application/pdf"
    extensions = frozenset({".pdf"})
    content_types = frozenset({mime_type, "application/x-pdf"})
    magic = (b"%PDF-",)

    def extract(self, data: bytes) -> str:
        """Текст всех страниц.

        Пустая строка между страницами делает границу страницы границей фрагмента.

        Raises:
            ExtractionFailed: файл повреждён, защищён паролем или без текстового слоя.
        """
        try:
            reader = PdfReader(io.BytesIO(data))
            # Пробуем пустой пароль: многие «зашифрованные» PDF открываются без него.
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
