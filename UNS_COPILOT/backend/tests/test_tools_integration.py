import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.tools.catalog import get_catalog
from app.tools.events import list_events
from app.tools.params import GetCatalogParams, GetLatestValueParams, ListEventsParams, QueryReadingsParams
from app.tools.readings import get_latest_value, query_readings

SILVER_DATABASE_URL = os.environ.get("SILVER_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not SILVER_DATABASE_URL, reason="SILVER_DATABASE_URL not set; requires a live UNS_SILVER Postgres"
)

TOPIC = "pytest_enterprise.pytest_site.pytest_line"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SILVER_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        await s.execute(
            text(
                "INSERT INTO signal_catalog (topic, signal_key, signal_type, unit, effective_since, effective_until) "
                "VALUES (:topic, 'temp', 'raw', 'C', :since, NULL)"
            ),
            {"topic": TOPIC, "since": NOW - timedelta(days=1)},
        )
        await s.execute(
            text(
                "INSERT INTO silver_readings (time, topic, signal_key, signal_type, value_numeric) "
                "VALUES (:t1, :topic, 'temp', 'raw', 21.5), (:t2, :topic, 'temp', 'raw', 22.0)"
            ),
            {"t1": NOW - timedelta(minutes=10), "t2": NOW - timedelta(minutes=5), "topic": TOPIC},
        )
        await s.execute(
            text(
                "INSERT INTO silver_latest_value (topic, signal_key, signal_type, time, value_numeric) "
                "VALUES (:topic, 'temp', 'raw', :t, 22.0) "
                "ON CONFLICT (topic, signal_key) DO UPDATE SET time = EXCLUDED.time, value_numeric = EXCLUDED.value_numeric"
            ),
            {"topic": TOPIC, "t": NOW - timedelta(minutes=5)},
        )
        await s.execute(
            text(
                "INSERT INTO silver_events (time, topic, event_key, payload, signal_type) "
                "VALUES (:t, :topic, 'alarms', :payload, 'unknown')"
            ),
            {"t": NOW - timedelta(minutes=3), "topic": TOPIC, "payload": '{"severity": "high"}'},
        )
        await s.commit()

        yield s

        await s.execute(text("DELETE FROM silver_events WHERE topic = :topic"), {"topic": TOPIC})
        await s.execute(text("DELETE FROM silver_latest_value WHERE topic = :topic"), {"topic": TOPIC})
        await s.execute(text("DELETE FROM silver_readings WHERE topic = :topic"), {"topic": TOPIC})
        await s.execute(text("DELETE FROM signal_catalog WHERE topic = :topic"), {"topic": TOPIC})
        await s.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_catalog_returns_active_entry(session: AsyncSession):
    rows = await get_catalog(session, GetCatalogParams(topic_filter="pytest_enterprise"))
    assert any(r["topic"] == TOPIC and r["signal_key"] == "temp" for r in rows)


@pytest.mark.asyncio
async def test_get_latest_value_returns_most_recent_point(session: AsyncSession):
    value = await get_latest_value(session, GetLatestValueParams(topic=TOPIC, signal_key="temp"))
    assert value is not None
    assert value["value_numeric"] == 22.0


@pytest.mark.asyncio
async def test_query_readings_returns_points_in_range(session: AsyncSession):
    params = QueryReadingsParams(
        topic=TOPIC, signal_key="temp", from_time=NOW - timedelta(hours=1), to_time=NOW, agg="raw",
    )
    rows = await query_readings(session, params, row_limit=1000, max_raw_range_hours=24)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_query_readings_rejects_too_wide_raw_span(session: AsyncSession):
    params = QueryReadingsParams(
        topic=TOPIC, signal_key="temp", from_time=NOW - timedelta(days=30), to_time=NOW, agg="raw",
    )
    with pytest.raises(ValueError, match="raw"):
        await query_readings(session, params, row_limit=1000, max_raw_range_hours=24)


@pytest.mark.asyncio
async def test_list_events_returns_matching_event(session: AsyncSession):
    params = ListEventsParams(topic_filter=TOPIC, from_time=NOW - timedelta(hours=1), to_time=NOW)
    rows = await list_events(session, params, row_limit=1000)
    assert len(rows) == 1
    assert rows[0]["payload"]["severity"] == "high"
