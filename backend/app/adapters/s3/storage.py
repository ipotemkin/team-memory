"""S3/MinIO FileStorage via boto3 (blocking calls run in a worker thread)."""

import asyncio
import hashlib
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.domain.errors import NotFound
from app.domain.models import StoredObject

_MISSING = {"NoSuchKey", "404", "NotFound"}


class S3FileStorage:
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
                signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 3}
            ),
        }
        self._client = boto3.client("s3", endpoint_url=endpoint_url, **kw)
        self._presigner = boto3.client("s3", endpoint_url=public_endpoint_url or endpoint_url, **kw)

    async def ensure_bucket(self) -> None:
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
        try:
            obj = await asyncio.to_thread(self._client.get_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                raise NotFound(f"object {key} not found") from exc
            raise
        body: bytes = await asyncio.to_thread(obj["Body"].read)
        return body

    async def presign(self, key: str, ttl_s: int = 900) -> str:
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
