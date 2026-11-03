"""Create the ClickHouse audit table:  python -m app.adapters.clickhouse.migrate"""

import asyncio

from app.adapters.clickhouse.sink import ClickHouseAuditSink
from app.config import get_settings


def main() -> None:
    s = get_settings()
    sink = ClickHouseAuditSink(
        s.CLICKHOUSE_HOST,
        s.CLICKHOUSE_PORT,
        s.CLICKHOUSE_USER,
        s.CLICKHOUSE_PASSWORD,
        s.CLICKHOUSE_DATABASE,
        s.CLICKHOUSE_TABLE,
        s.CLICKHOUSE_SECURE,
    )
    asyncio.run(sink.ensure_schema())
    print(f"ok: {sink.fqtn}")
    sink.close()


if __name__ == "__main__":
    main()
