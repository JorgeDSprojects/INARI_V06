from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.executor import execute_tool
from app.tools.params import GetLatestValueParams


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
async def test_non_dict_arguments_return_error_not_raise():
    """Test that non-dict arguments (list, string, int, bool) don't raise TypeError."""
    for non_dict_args in [["topic", "signal"], "not a dict", 42, True]:
        result = await execute_tool(
            session=None, name="get_latest_value", arguments=non_dict_args,
            row_limit=10, max_raw_range_hours=24,
        )
        assert "error" in result, f"Failed for arguments={non_dict_args!r}"


@pytest.mark.asyncio
async def test_valid_call_dispatches_to_the_right_tool_function():
    with patch("app.tools.executor.get_latest_value", new=AsyncMock(return_value={"value_numeric": 42})) as mocked:
        result = await execute_tool(
            session=None, name="get_latest_value", arguments={"topic": "line3", "signal_key": "temp"},
            row_limit=10, max_raw_range_hours=24,
        )
    assert result == {"result": {"value_numeric": 42}}
    mocked.assert_awaited_once()
    # Verify the params object was passed correctly
    call_args = mocked.call_args
    assert call_args is not None
    params = call_args[0][1]  # Second positional arg (after session)
    assert isinstance(params, GetLatestValueParams)
    assert params.topic == "line3"
    assert params.signal_key == "temp"


@pytest.mark.asyncio
async def test_a_tool_raising_becomes_an_error_result():
    with patch("app.tools.executor.get_latest_value", new=AsyncMock(side_effect=RuntimeError("db is down"))):
        result = await execute_tool(
            session=None, name="get_latest_value", arguments={"topic": "line3", "signal_key": "temp"},
            row_limit=10, max_raw_range_hours=24,
        )
    assert "error" in result
    assert "db is down" in result["error"]


@pytest.mark.asyncio
async def test_a_failing_tool_rolls_the_session_back():
    """A DB error leaves the shared session's transaction aborted; without a
    rollback every later tool call in the same turn fails too."""
    session = MagicMock()
    session.rollback = AsyncMock()
    with patch("app.tools.executor.get_latest_value", new=AsyncMock(side_effect=RuntimeError("db is down"))):
        result = await execute_tool(
            session=session, name="get_latest_value", arguments={"topic": "line3", "signal_key": "temp"},
            row_limit=10, max_raw_range_hours=24,
        )
    assert "error" in result
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failing_rollback_does_not_mask_the_tool_error():
    session = MagicMock()
    session.rollback = AsyncMock(side_effect=RuntimeError("connection already closed"))
    with patch("app.tools.executor.get_latest_value", new=AsyncMock(side_effect=RuntimeError("db is down"))):
        result = await execute_tool(
            session=session, name="get_latest_value", arguments={"topic": "line3", "signal_key": "temp"},
            row_limit=10, max_raw_range_hours=24,
        )
    assert "db is down" in result["error"]


@pytest.mark.asyncio
async def test_get_catalog_dispatch_passes_row_limit():
    with patch("app.tools.executor.get_catalog", new=AsyncMock(return_value=[])) as mocked:
        await execute_tool(
            session=None, name="get_catalog", arguments={}, row_limit=7, max_raw_range_hours=24,
        )
    assert mocked.call_args.kwargs.get("row_limit") == 7


@pytest.mark.asyncio
async def test_get_catalog_dispatch_to_correct_tool():
    """Verify get_catalog is routed correctly through execute_tool."""
    with patch("app.tools.executor.get_catalog", new=AsyncMock(return_value=[])) as mocked:
        result = await execute_tool(
            session=None, name="get_catalog", arguments={"topic_filter": "plant1"},
            row_limit=10, max_raw_range_hours=24,
        )
    assert result == {"result": []}
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_readings_dispatch_with_row_limit_and_max_raw_range_hours():
    """Verify query_readings receives row_limit and max_raw_range_hours correctly."""
    with patch("app.tools.executor.query_readings", new=AsyncMock(return_value=[])) as mocked:
        result = await execute_tool(
            session=None,
            name="query_readings",
            arguments={
                "topic": "plant1.line1",
                "signal_key": "temp",
                "from_time": "2026-01-01T00:00:00Z",
                "to_time": "2026-01-02T00:00:00Z",
            },
            row_limit=100,
            max_raw_range_hours=48,
        )
    assert result == {"result": []}
    mocked.assert_awaited_once()
    # Verify row_limit and max_raw_range_hours were passed correctly
    call_args = mocked.call_args
    assert call_args.kwargs.get("row_limit") == 100
    assert call_args.kwargs.get("max_raw_range_hours") == 48


@pytest.mark.asyncio
async def test_list_events_dispatch_with_row_limit():
    """Verify list_events receives row_limit correctly."""
    with patch("app.tools.executor.list_events", new=AsyncMock(return_value=[])) as mocked:
        result = await execute_tool(
            session=None,
            name="list_events",
            arguments={
                "from_time": "2026-01-01T00:00:00Z",
                "to_time": "2026-01-02T00:00:00Z",
            },
            row_limit=50,
            max_raw_range_hours=24,
        )
    assert result == {"result": []}
    mocked.assert_awaited_once()
    # Verify row_limit was passed correctly
    call_args = mocked.call_args
    assert call_args.kwargs.get("row_limit") == 50
