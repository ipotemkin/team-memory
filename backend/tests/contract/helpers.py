"""Вспомогательные конструкторы документов и фрагментов для контрактных тестов."""

from app.domain.chunking import sha256_text
from app.domain.models import NewDocument, Snippet


def new_doc(title: str = "Док", text: str = "Текст документа.") -> NewDocument:
    return NewDocument(title=title, text=text, content_sha256=sha256_text(text))


def snip(document_id: int, ord: int, text: str) -> Snippet:
    return Snippet(
        document_id=document_id,
        ord=ord,
        text=text,
        char_start=0,
        char_end=len(text),
        text_sha256=sha256_text(text),
    )
