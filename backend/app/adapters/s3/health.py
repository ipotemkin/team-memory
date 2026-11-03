"""MinIO / S3 reachability probe."""

import asyncio

import boto3
from botocore.config import Config

from app.domain.health import ComponentStatus


class S3Probe:
    name = "minio"

    def __init__(self, endpoint: str, key: str, secret: str, bucket: str, region: str) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name=region,
            config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}),
        )

    async def check(self) -> ComponentStatus:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        return ComponentStatus(self.name, True, f"bucket={self._bucket}")
