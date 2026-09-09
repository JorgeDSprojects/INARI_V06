from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.normalize import row_to_json_safe
from app.tools.params import GetCatalogParams


async def get_catalog(session: AsyncSession, params: GetCatalogParams, row_limit: int) -> list[dict]:
    query = (
        "SELECT topic, signal_key, signal_type, unit, description, range_min, range_max, thresholds "
        "FROM signal_catalog WHERE effective_until IS NULL"
    )
    bind: dict = {"row_limit": row_limit}
    if params.topic_filter:
        query += " AND topic LIKE :topic_prefix"
        bind["topic_prefix"] = f"{params.topic_filter}%"
    if params.signal_type:
        query += " AND signal_type = :signal_type"
        bind["signal_type"] = params.signal_type
    # Bounded like every other tool: an unfiltered catalog on a large plant
    # would otherwise blow past the model's context window.
    query += " ORDER BY topic, signal_key LIMIT :row_limit"

    result = await session.execute(text(query), bind)
    # range_min/range_max are NUMERIC -> Decimal, which json.dumps rejects.
    return [row_to_json_safe(row) for row in result.mappings()]
