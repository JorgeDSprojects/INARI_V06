from unittest.mock import AsyncMock, patch

import pytest

from app.tools.executor import execute_tool


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_not_raise():
    result = await execute_tool(session=None, name="delete_everything", arguments={}, row_limit=10, max_raw_range_hours=24)
    assert "error" in result


@pytest.mark.asyncio
async def test_invalid_arguments_return_error_not_raise():
    result = await execute_tool(
        session=None, name="get_latest_value", arguments={"topic": "line3"},  # missing signal_key
        row_limit=10, max_raw_range_hours=24,
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_valid_call_dispatches_to_the_right_tool_function():
    with patch("app.tools.executor.get_latest_value", new=AsyncMock(return_value={"value_numeric": 42})) as mocked:
        result = await execute_tool(
            session=None, name="get_latest_value", arguments={"topic": "line3", "signal_key": "temp"},
            row_limit=10, max_raw_range_hours=24,
        )
    assert result == {"result": {"value_numeric": 42}}
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_tool_raising_becomes_an_error_result():
    with patch("app.tools.executor.get_latest_value", new=AsyncMock(side_effect=RuntimeError("db is down"))):
        result = await execute_tool(
            session=None, name="get_latest_value", arguments={"topic": "line3", "signal_key": "temp"},
            row_limit=10, max_raw_range_hours=24,
        )
    assert "error" in result
    assert "db is down" in result["error"]
