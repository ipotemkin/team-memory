"""Public ids are strings in the API (spec); internally they are BIGINT identities."""

from app.domain.errors import NotFound

_MAX = 2**63 - 1


def parse_id(raw: str, kind: str) -> int:
    if not raw.isascii() or not raw.isdigit() or not 0 < int(raw) <= _MAX:
        raise NotFound(f"{kind} {raw!r} not found")
    return int(raw)
