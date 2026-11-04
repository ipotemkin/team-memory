"""FileStorage на S3/MinIO через boto3.

boto3 синхронный, поэтому каждый вызов выполняется в отдельном потоке (``asyncio.to_thread``)
и не блокирует event loop.
"""

import asyncio
import hashlib
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.domain.errors import NotFound
from app.domain.models import StoredObject

# Коды ошибок S3, означающие «объекта нет».
_MISSING = {"NoSuchKey", "404", "NotFound"}


class S3FileStorage:
    """Порт FileStorage для S3/MinIO.

    Args:
        endpoint_url: адрес S3 изнутри docker-сети (например, ``http://minio:9000``).
        access_key, secret_key: учётные данные.
        bucket: бакет для оригиналов.
        region: регион (для MinIO любой).
        public_endpoint_url: адрес, доступный из браузера, — им подписываются ссылки на скачивание
            (S3_PUBLIC_ENDPOINT_URL), т.к. подпись включает host.
    """

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        public_endpoint_url: str | None = None,
    ) -> None:
        self.bucket = bucket
        kw: dict[str, Any] = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region,
            "config": Config(
                # path-style адресация (http://host/bucket/key) — нужна MinIO; 3 попытки на запрос.
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3},
            ),
        }
        self._client = boto3.client("s3", endpoint_url=endpoint_url, **kw)
        self._presigner = boto3.client("s3", endpoint_url=public_endpoint_url or endpoint_url, **kw)

    async def ensure_bucket(self) -> None:
        """Создаёт бакет, если его нет (при старте приложения)."""
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)
        except ClientError:
            await asyncio.to_thread(self._client.create_bucket, Bucket=self.bucket)

    async def put(self, key: str, data: bytes, mime: str) -> StoredObject:
        await asyncio.to_thread(
            self._client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=mime
        )
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
            obj = await asyncio.to_thread(self._client.get_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                raise NotFound(f"object {key} not found") from exc
            raise
        body: bytes = await asyncio.to_thread(obj["Body"].read)
        return body

    async def presign(self, key: str, ttl_s: int = 900) -> str:
        """Presigned-ссылка на скачивание на ``ttl_s`` секунд.

        Сначала проверяем, что объект существует, — иначе пользователь получил бы ссылку на 404.

        Raises:
            NotFound: объекта нет.
        """
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            raise NotFound(f"object {key} not found") from exc
        url: str = await asyncio.to_thread(
            self._presigner.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl_s,
        )
        return url

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)
