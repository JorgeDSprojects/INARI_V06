from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.params import ListEventsParams


async def list_events(session: AsyncSession, params: ListEventsParams, row_limit: int) -> list[dict]:
    query = (
        "SELECT time, topic, event_key, payload, signal_type FROM silver_events "
        "WHERE time BETWEEN :from_time AND :to_time"
    )
    bind: dict = {"from_time": params.from_time, "to_time": params.to_time, "row_limit": row_limit}
    if params.topic_filter:
        query += " AND topic LIKE :topic_prefix"
        bind["topic_prefix"] = f"{params.topic_filter}%"
    if params.event_key:
        query += " AND event_key = :event_key"
        bind["event_key"] = params.event_key
    query += " ORDER BY time LIMIT :row_limit"

    result = await session.execute(text(query), bind)
    return [dict(row) for row in result.mappings()]
