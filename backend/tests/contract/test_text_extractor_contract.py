"""Contract of the TextExtractor port: every registered adapter must satisfy it."""

import pytest

from app.adapters.extractors import ALL_EXTRACTORS
from app.domain.ports import TextExtractor
from tests.support_files import make_docx, sample_pdf

SAMPLES: dict[str, tuple[str, bytes, str]] = {  # format -> (filename, bytes, phrase in text)
    "txt": ("a.txt", "Стендап в 11:00.\n\nРелиз по четвергам.".encode(), "Релиз по четвергам."),
    "md": ("a.md", "# Заголовок\n\nТекст **жирный**.".encode(), "Текст **жирный**."),
    "docx": ("a.docx", make_docx(), "Ёлка в офисе ставится 20 декабря."),
    "pdf": ("a.pdf", sample_pdf(), "алерт эскалируется тимлиду."),
}


@pytest.fixture(params=[cls.format for cls in ALL_EXTRACTORS])
def case(request: pytest.FixtureRequest) -> tuple[TextExtractor, str, bytes, str]:
    cls = next(c for c in ALL_EXTRACTORS if c.format == request.param)
    filename, data, phrase = SAMPLES[request.param]
    return cls(), filename, data, phrase


def test_every_format_has_a_sample() -> None:
    assert {c.format for c in ALL_EXTRACTORS} == set(SAMPLES)


def test_supports_own_extension_and_mime(case: tuple[TextExtractor, str, bytes, str]) -> None:
    ex, filename, _, _ = case
    ext = "." + filename.rsplit(".", 1)[1]
    assert ex.supports(ext, ex.mime_type)
    assert ex.supports(ext, "application/octet-stream")
    assert not ex.supports(".exe", ex.mime_type)


def test_extract_is_deterministic_text(case: tuple[TextExtractor, str, bytes, str]) -> None:
    ex, _, data, phrase = case
    assert ex.matches(data)
    text = ex.extract(data)
    assert isinstance(text, str) and phrase in text and text == ex.extract(data)
    assert "\x00" not in text and "\r" not in text


def test_binary_formats_check_magic(case: tuple[TextExtractor, str, bytes, str]) -> None:
    ex, _, _, _ = case
    if ex.format in ("txt", "md"):
        assert ex.matches(b"anything")
    else:
        assert not ex.matches(b"plain text, no magic")
