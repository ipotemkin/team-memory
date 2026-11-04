"""Адаптер объектного хранилища S3/MinIO (boto3) — порт FileStorage для оригиналов файлов."""

from app.adapters.s3.storage import S3FileStorage

__all__ = ["S3FileStorage"]
