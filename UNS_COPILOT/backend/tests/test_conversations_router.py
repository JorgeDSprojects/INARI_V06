import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set; requires a live Postgres (docker compose up -d copilot_postgres)"
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_create_list_and_get_conversation(client: TestClient):
    user_id = client.get("/users/").json()[0]["id"]

    created = client.post("/conversations/", json={"user_id": user_id, "title": "pytest chat"}).json()
    assert created["user_id"] == user_id
    assert created["title"] == "pytest chat"

    listed = client.get(f"/conversations/?user_id={user_id}").json()
    assert any(c["id"] == created["id"] for c in listed)

    detail = client.get(f"/conversations/{created['id']}").json()
    assert detail["messages"] == []
