"""Публичные id в API — строки (ТЗ); внутри это BIGINT identity."""

from app.domain.errors import NotFound

_MAX = 2**63 - 1


def parse_id(raw: str, kind: str) -> int:
    # isascii: str.isdigit() принимает и не-ASCII цифры (например, «٣»), которые int() тоже разберёт.
    """Строка → id; всё, что не положительное целое в диапазоне BIGINT, — «не найдено».

    Raises:
        NotFound: некорректный id (404, а не 422: снаружи id непрозрачен).
    """
    if not raw.isascii() or not raw.isdigit() or not 0 < int(raw) <= _MAX:
        raise NotFound(f"{kind} {raw!r} not found")
    return int(raw)
