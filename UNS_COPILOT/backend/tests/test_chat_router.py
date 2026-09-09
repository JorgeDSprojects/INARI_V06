import os
import re

import pytest
from fastapi.testclient import TestClient

from app.llm.base import ChatResponse
from app.llm.fake import FakeLLMProvider
from app.main import app
from app.routers.chat import get_llm_provider

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set; requires a live Postgres (docker compose up -d copilot_postgres)"
)


@pytest.fixture
def client():
    fake_llm = FakeLLMProvider([ChatResponse(content="It's 22.0 degrees.", tool_calls=[], finish_reason="stop")])
    app.dependency_overrides[get_llm_provider] = lambda: fake_llm
    with TestClient(app) as c:
        c.fake_llm = fake_llm
        yield c
    app.dependency_overrides.clear()


def test_send_message_returns_assistant_reply(client: TestClient):
    user_id = client.get("/users/").json()[0]["id"]
    conversation = client.post("/conversations/", json={"user_id": user_id}).json()

    response = client.post(f"/conversations/{conversation['id']}/messages", json={"text": "what is the temperature?"})

    assert response.status_code == 200
    assert response.json() == {"conversation_id": conversation["id"], "reply": "It's 22.0 degrees."}

    detail = client.get(f"/conversations/{conversation['id']}").json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]


def test_first_message_sets_title_and_bumps_updated_at(client: TestClient):
    """Spec Section 2: the title defaults to the first 50 chars of the first
    user message, and conversations sort by recency of activity."""
    user_id = client.get("/users/").json()[0]["id"]
    conversation = client.post("/conversations/", json={"user_id": user_id}).json()
    assert conversation["title"] is None
    before = conversation["updated_at"]

    text = "a" * 40 + "b" * 40  # 80 chars, so the 50-char cut is observable
    client.post(f"/conversations/{conversation['id']}/messages", json={"text": text})

    detail = client.get(f"/conversations/{conversation['id']}").json()
    assert detail["title"] == text[:50]
    assert len(detail["title"]) == 50
    assert detail["updated_at"] != before


def test_run_turn_prepends_a_system_message_with_the_current_time(client: TestClient):
    """The model has no other way to resolve "today"/"last hour" into the
    explicit from_time/to_time the tools require."""
    user_id = client.get("/users/").json()[0]["id"]
    conversation = client.post("/conversations/", json={"user_id": user_id}).json()

    client.post(f"/conversations/{conversation['id']}/messages", json={"text": "what happened today?"})

    sent_messages = client.fake_llm.calls[0]
    assert sent_messages[0]["role"] == "system"
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", sent_messages[0]["content"])
    assert "signal_key" in sent_messages[0]["content"]

    # The system message is synthesised per turn, never persisted.
    detail = client.get(f"/conversations/{conversation['id']}").json()
    assert all(m["role"] != "system" for m in detail["messages"])
