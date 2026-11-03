"""Shared integration helpers: env-gated Postgres (migrated once) and S3 fixtures."""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
TEST_S3_ENDPOINT = os.environ.get("TEST_S3_ENDPOINT_URL")

needs_pg = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not set")
TEST_CH_HOST = os.environ.get("TEST_CLICKHOUSE_HOST")
needs_ch = pytest.mark.skipif(not TEST_CH_HOST, reason="TEST_CLICKHOUSE_HOST not set")
needs_s3 = pytest.mark.skipif(not TEST_S3_ENDPOINT, reason="TEST_S3_ENDPOINT_URL not set")

TABLES = (
    "app.audit_runs, app.qa_runs, app.snippet_embeddings, app.embedding_models, app.snippets, app.documents"
)
_migrated = False


def migrate_once() -> None:
    """alembic upgrade head against TEST_DATABASE_URL (sync context, once per session)."""
    global _migrated
    if _migrated:
        return
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(TEST_DB_URL))
    # env.py uses asyncio.run(); run it in a separate thread so it works inside async fixtures
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as ex:
        ex.submit(command.upgrade, cfg, "head").result()
    _migrated = True


async def pg_backend() -> AsyncIterator["object"]:
    from app.adapters.postgres import build

    b = build(str(TEST_DB_URL), pool_size=2)
    async with b.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    try:
        yield b
    finally:
        async with b.engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        await b.dispose()


async def s3_storage() -> AsyncIterator["object"]:
    from app.adapters.s3 import S3FileStorage

    bucket = f"{os.environ.get('TEST_S3_BUCKET', 'kb-test')}-{uuid.uuid4().hex[:8]}"
    st = S3FileStorage(
        str(TEST_S3_ENDPOINT),
        os.environ.get("TEST_S3_ACCESS_KEY", "minioadmin"),
        os.environ.get("TEST_S3_SECRET_KEY", "minioadmin"),
        bucket,
        os.environ.get("TEST_S3_REGION", "us-east-1"),
    )
    await st.ensure_bucket()
    try:
        yield st
    finally:
        c = st._client
        for obj in c.list_objects_v2(Bucket=bucket).get("Contents", []):
            c.delete_object(Bucket=bucket, Key=obj["Key"])
        c.delete_bucket(Bucket=bucket)


async def ch_sink() -> AsyncIterator["object"]:
    """ClickHouse audit sink on a throwaway table (TEST_CLICKHOUSE_HOST/PORT/USER/PASSWORD)."""
    from app.adapters.clickhouse import ClickHouseAuditSink

    sink = ClickHouseAuditSink(
        str(TEST_CH_HOST),
        int(os.environ.get("TEST_CLICKHOUSE_PORT", "8124")),
        os.environ.get("TEST_CLICKHOUSE_USER", "default"),
        os.environ.get("TEST_CLICKHOUSE_PASSWORD", ""),
        database="kb_test",
        table=f"audit_{uuid.uuid4().hex[:8]}",
    )
    try:
        await sink.ensure_schema()
    except Exception as e:  # noqa: BLE001 - CH configured but unreachable -> skip, not fail
        pytest.skip(f"ClickHouse unavailable: {e}")
    try:
        yield sink
    finally:
        sink._get().command(f"DROP TABLE IF EXISTS {sink.fqtn}")
        sink.close()
