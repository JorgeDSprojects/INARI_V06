from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.params import GetCatalogParams


async def get_catalog(session: AsyncSession, params: GetCatalogParams) -> list[dict]:
    query = (
        "SELECT topic, signal_key, signal_type, unit, description, range_min, range_max, thresholds "
        "FROM signal_catalog WHERE effective_until IS NULL"
    )
    bind: dict = {}
    if params.topic_filter:
        query += " AND topic LIKE :topic_prefix"
        bind["topic_prefix"] = f"{params.topic_filter}%"
    if params.signal_type:
        query += " AND signal_type = :signal_type"
        bind["signal_type"] = params.signal_type

    result = await session.execute(text(query), bind)
    return [dict(row) for row in result.mappings()]
