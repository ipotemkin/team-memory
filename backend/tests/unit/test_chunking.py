"""Нарезка текста на фрагменты: по пустым строкам, смещения, длинные абзацы и предложения."""

from app.domain.chunking import chunk_paragraphs, sha256_text


def test_splits_by_blank_lines_and_keeps_offsets() -> None:
    text = "Первый абзац.\n\n  Второй абзац.  \n \n\nТретий."
    chunks = chunk_paragraphs(text)
    assert [c.text for c in chunks] == ["Первый абзац.", "Второй абзац.", "Третий."]
    for c in chunks:
        assert text[c.char_start : c.char_end] == c.text
        assert c.text_sha256 == sha256_text(c.text)
    assert [c.ord for c in chunks] == [0, 1, 2]


def test_empty_text_gives_no_chunks() -> None:
    assert chunk_paragraphs("  \n\n \n") == []


def test_long_paragraph_split_by_sentences_verbatim() -> None:
    sent = "Это довольно длинное предложение номер {}. "
    text = "".join(sent.format(i) for i in range(60)).strip()
    chunks = chunk_paragraphs(text, max_chars=200)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    assert all(text[c.char_start : c.char_end] == c.text for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)


def test_giant_sentence_hard_split() -> None:
    text = "а" * 500
    chunks = chunk_paragraphs(text, max_chars=200)
    assert [len(c.text) for c in chunks] == [200, 200, 100]
