"""End-to-end chat test: a real UNS_SILVER row, through the real
`execute_tool`, through the real `run_turn`, through the real HTTP endpoint.

Every other test in this suite mocks the layer below the one under test, so
nothing else catches a failure that only appears when a genuine asyncpg row
(with `datetime` / `Decimal` values) has to be serialised into a `tool`
message. Only the LLM itself is faked here -- that is the one component we
cannot run deterministically.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.base import ChatResponse, ToolCall
from app.llm.fake import FakeLLMProvider
from app.main import app
from app.routers.chat import get_llm_provider

DATABASE_URL = os.environ.get("DATABASE_URL")
SILVER_DATABASE_URL = os.environ.get("SILVER_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not SILVER_DATABASE_URL,
    reason="DATABASE_URL and SILVER_DATABASE_URL must both be set; requires live copilot + silver Postgres",
)

TOPIC = "pytest_e2e_enterprise.pytest_e2e_site.pytest_e2e_line"
SIGNAL_KEY = "temp"
VALUE = 23.75
NOW = datetime.now(timezone.utc).replace(microsecond=0)


async def _seed() -> None:
    engine = create_async_engine(SILVER_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        await s.execute(
            text(
                "INSERT INTO signal_catalog "
                "(topic, signal_key, signal_type, unit, range_min, range_max, effective_since, effective_until) "
                "VALUES (:topic, :key, 'raw', 'C', 0, 100, :since, NULL)"
            ),
            {"topic": TOPIC, "key": SIGNAL_KEY, "since": NOW - timedelta(days=1)},
        )
        await s.execute(
            text(
                "INSERT INTO silver_readings (time, topic, signal_key, signal_type, value_numeric) "
                "VALUES (:t, :topic, :key, 'raw', :value)"
            ),
            {"t": NOW - timedelta(minutes=5), "topic": TOPIC, "key": SIGNAL_KEY, "value": VALUE},
        )
        await s.execute(
            text(
                "INSERT INTO silver_latest_value (topic, signal_key, signal_type, time, value_numeric) "
                "VALUES (:topic, :key, 'raw', :t, :value) "
                "ON CONFLICT (topic, signal_key) DO UPDATE "
                "SET time = EXCLUDED.time, value_numeric = EXCLUDED.value_numeric"
            ),
            {"topic": TOPIC, "key": SIGNAL_KEY, "t": NOW - timedelta(minutes=5), "value": VALUE},
        )
        await s.commit()
    await engine.dispose()


async def _cleanup() -> None:
    engine = create_async_engine(SILVER_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        await s.execute(text("DELETE FROM silver_latest_value WHERE topic = :topic"), {"topic": TOPIC})
        await s.execute(text("DELETE FROM silver_readings WHERE topic = :topic"), {"topic": TOPIC})
        await s.execute(text("DELETE FROM signal_catalog WHERE topic = :topic"), {"topic": TOPIC})
        await s.commit()
    await engine.dispose()


@pytest.fixture
def seeded_silver_row():
    """Seed one distinctive-topic signal into the LIVE shared UNS_SILVER and
    remove it again afterwards -- same topic-scoped pattern as
    tests/test_tools_integration.py. Synchronous so it composes with the
    sync TestClient without sharing an event loop with it."""
    asyncio.run(_seed())
    try:
        yield
    finally:
        asyncio.run(_cleanup())


@pytest.fixture
def client():
    """TestClient whose LLM is scripted to make a REAL get_latest_value call
    against the seeded row -- `execute_tool` is deliberately NOT mocked."""
    fake_llm = FakeLLMProvider([
        ChatResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_e2e_1",
                    name="get_latest_value",
                    arguments={"topic": TOPIC, "signal_key": SIGNAL_KEY},
                )
            ],
            finish_reason="tool_calls",
        ),
        ChatResponse(content=f"The latest value is {VALUE} C.", tool_calls=[], finish_reason="stop"),
    ])
    app.dependency_overrides[get_llm_provider] = lambda: fake_llm
    with TestClient(app) as c:
        c.fake_llm = fake_llm
        yield c
    app.dependency_overrides.clear()


def test_real_tool_result_is_persisted_as_valid_json(seeded_silver_row, client: TestClient):
    user_id = client.get("/users/").json()[0]["id"]
    conversation = client.post("/conversations/", json={"user_id": user_id}).json()

    response = client.post(
        f"/conversations/{conversation['id']}/messages",
        json={"text": f"what is the latest {SIGNAL_KEY} on {TOPIC}?"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["reply"] == f"The latest value is {VALUE} C."

    detail = client.get(f"/conversations/{conversation['id']}").json()
    tool_messages = [m for m in detail["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1

    # The whole point: this content must be valid JSON built from a real
    # asyncpg row (TIMESTAMPTZ + NUMERIC), not a TypeError from json.dumps.
    payload = json.loads(tool_messages[0]["content"])
    row = payload["result"]
    assert row["topic"] == TOPIC
    assert row["signal_key"] == SIGNAL_KEY
    assert row["value_numeric"] == VALUE
    assert isinstance(row["value_numeric"], float)
    assert isinstance(row["time"], str)  # normalised at the tool boundary
