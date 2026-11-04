"""In-memory FileStorage: оригиналы файлов в словаре (STORAGE_BACKEND=memory)."""

import hashlib

from app.domain.errors import NotFound
from app.domain.models import StoredObject


class MemoryFileStorage:
    """Порт FileStorage; presign возвращает псевдо-URL ``memory://bucket/key?ttl=...``."""

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
        """Содержимое объекта.

        Raises:
            NotFound: объекта нет.
        """
        try:
            return self._objects[key][0]
        except KeyError:
            raise NotFound(f"object {key} not found") from None

    async def presign(self, key: str, ttl_s: int = 900) -> str:
        """Псевдо-ссылка на объект.

        Raises:
            NotFound: объекта нет.
        """
        if key not in self._objects:
            raise NotFound(f"object {key} not found")
        return f"memory://{self.bucket}/{key}?ttl={ttl_s}"

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)
