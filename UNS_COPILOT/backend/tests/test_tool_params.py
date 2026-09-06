from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.tools.params import GetLatestValueParams, QueryReadingsParams, check_raw_range


def test_get_latest_value_requires_topic_and_signal_key():
    with pytest.raises(ValidationError):
        GetLatestValueParams(topic="line3")  # missing signal_key


def test_query_readings_rejects_unknown_agg():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        QueryReadingsParams(
            topic="line3", signal_key="temp", from_time=now - timedelta(hours=1), to_time=now, agg="5m",
        )


def test_check_raw_range_allows_short_raw_span():
    now = datetime.now(timezone.utc)
    params = QueryReadingsParams(
        topic="line3", signal_key="temp", from_time=now - timedelta(hours=1), to_time=now, agg="raw",
    )
    check_raw_range(params, max_raw_range_hours=24)  # must not raise


def test_check_raw_range_rejects_long_raw_span():
    now = datetime.now(timezone.utc)
    params = QueryReadingsParams(
        topic="line3", signal_key="temp", from_time=now - timedelta(days=30), to_time=now, agg="raw",
    )
    with pytest.raises(ValueError, match="raw"):
        check_raw_range(params, max_raw_range_hours=24)


def test_check_raw_range_ignores_aggregated_queries():
    now = datetime.now(timezone.utc)
    params = QueryReadingsParams(
        topic="line3", signal_key="temp", from_time=now - timedelta(days=30), to_time=now, agg="1h",
    )
    check_raw_range(params, max_raw_range_hours=24)  # must not raise -- aggregated, not raw
