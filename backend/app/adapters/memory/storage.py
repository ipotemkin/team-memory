"""In-memory FileStorage."""

import hashlib

from app.domain.errors import NotFound
from app.domain.models import StoredObject


class MemoryFileStorage:
    def __init__(self, bucket: str = "memory") -> None:
        self.bucket = bucket
        self._objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, mime: str) -> StoredObject:
        self._objects[key] = (bytes(data), mime)
        return StoredObject(
            bucket=self.bucket,
            key=key,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type=mime,
        )

    async def get(self, key: str) -> bytes:
        try:
            return self._objects[key][0]
        except KeyError:
            raise NotFound(f"object {key} not found") from None

    async def presign(self, key: str, ttl_s: int = 900) -> str:
        if key not in self._objects:
            raise NotFound(f"object {key} not found")
        return f"memory://{self.bucket}/{key}?ttl={ttl_s}"

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)
