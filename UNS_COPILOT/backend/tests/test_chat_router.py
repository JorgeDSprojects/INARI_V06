import os

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
