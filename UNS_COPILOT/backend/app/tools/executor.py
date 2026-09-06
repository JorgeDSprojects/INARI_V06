from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.catalog import get_catalog
from app.tools.events import list_events
from app.tools.params import GetCatalogParams, GetLatestValueParams, ListEventsParams, QueryReadingsParams
from app.tools.readings import get_latest_value, query_readings

logger = logging.getLogger(__name__)

_PARAM_MODELS = {
    "get_catalog": GetCatalogParams,
    "get_latest_value": GetLatestValueParams,
    "query_readings": QueryReadingsParams,
    "list_events": ListEventsParams,
}


async def execute_tool(
    session: AsyncSession, name: str, arguments: dict, *, row_limit: int, max_raw_range_hours: int
) -> dict:
    """Never raises -- see Global Constraints. Any failure (unknown tool,
    invalid arguments, a DB error surfacing from the tool itself) comes
    back as {"error": "..."} so the agentic loop can hand it to the model
    as a tool_result instead of crashing the turn."""
    param_model = _PARAM_MODELS.get(name)
    if param_model is None:
        return {"error": f"Unknown tool: {name!r}"}

    try:
        params = param_model(**arguments)
    except (ValidationError, TypeError) as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}

    try:
        if name == "get_catalog":
            result = await get_catalog(session, params)
        elif name == "get_latest_value":
            result = await get_latest_value(session, params)
        elif name == "query_readings":
            result = await query_readings(session, params, row_limit=row_limit, max_raw_range_hours=max_raw_range_hours)
        elif name == "list_events":
            result = await list_events(session, params, row_limit=row_limit)
        else:  # pragma: no cover - unreachable, guarded by _PARAM_MODELS above
            return {"error": f"Unknown tool: {name!r}"}
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all, see docstring
        logger.warning("Tool %s failed: %s", name, exc)
        return {"error": str(exc)}

    return {"result": result}
