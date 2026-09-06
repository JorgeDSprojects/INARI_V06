from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.params import GetLatestValueParams, QueryReadingsParams, check_raw_range

_AGG_TABLES = {"raw": "silver_readings", "1m": "silver_readings_1m", "1h": "silver_readings_1h"}
_AGG_TIME_COLUMN = {"raw": "time", "1m": "bucket", "1h": "bucket"}


async def get_latest_value(session: AsyncSession, params: GetLatestValueParams) -> dict | None:
    result = await session.execute(
        text(
            "SELECT topic, signal_key, signal_type, time, value_numeric, value_text "
            "FROM silver_latest_value WHERE topic = :topic AND signal_key = :signal_key"
        ),
        {"topic": params.topic, "signal_key": params.signal_key},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def query_readings(
    session: AsyncSession, params: QueryReadingsParams, row_limit: int, max_raw_range_hours: int
) -> list[dict]:
    check_raw_range(params, max_raw_range_hours)  # raises ValueError if violated -- see spec Section 4

    table = _AGG_TABLES[params.agg]
    time_column = _AGG_TIME_COLUMN[params.agg]
    if params.agg == "raw":
        select_cols = f"{time_column} AS time, value_numeric, value_text"
    else:
        select_cols = f"{time_column} AS time, avg_value, min_value, max_value, sample_count"

    result = await session.execute(
        text(
            f"SELECT {select_cols} FROM {table} "
            f"WHERE topic = :topic AND signal_key = :signal_key AND {time_column} BETWEEN :from_time AND :to_time "
            f"ORDER BY {time_column} LIMIT :row_limit"
        ),
        {
            "topic": params.topic, "signal_key": params.signal_key,
            "from_time": params.from_time, "to_time": params.to_time, "row_limit": row_limit,
        },
    )
    return [dict(row) for row in result.mappings()]
