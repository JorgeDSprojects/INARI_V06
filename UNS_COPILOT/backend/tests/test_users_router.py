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


def test_users_are_seeded_and_listed(client: TestClient):
    response = client.get("/users/")
    assert response.status_code == 200
    names = [u["display_name"] for u in response.json()]
    assert "Operario Juan" in names
    assert "Ingeniera Maria" in names
    assert "Administrador" in names
