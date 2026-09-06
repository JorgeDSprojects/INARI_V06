# UNS Copilot Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `UNS_COPILOT` — a FastAPI backend that lets a user chat in natural language about `UNS_SILVER` data, where an LLM answers by calling a closed set of typed, read-only tools (never free-form SQL).

**Architecture:** New sibling service (`UNS_COPILOT/`) with its own Postgres (users/conversations/messages) and a FastAPI backend. A hand-written agentic loop calls a provider-agnostic `LLMProvider` interface (first adapter: Ollama, via the `openai` SDK against Ollama's OpenAI-compatible endpoint) with four tools that query `uns_silver_postgres` read-only. Observability hooks into Langfuse but fails open (chat works even if Langfuse is unreachable or not yet deployed).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async, asyncpg), Postgres 16, `openai` SDK (against Ollama), `langfuse` SDK (optional), pytest + pytest-asyncio, Docker Compose.

**Spec:** `UNS_COPILOT/docs/superpowers/specs/2026-09-06-uns-copilot-design.md` — read it alongside this plan; this plan implements it section by section and does not repeat its rationale.

## Global Constraints

- Code, identifiers, comments, and commit messages are in English. Tool names and descriptions exposed to the LLM (`app/tools/schemas.py`) are also in English. Chat with the project owner stays in Spanish — unrelated to this plan's code.
- All access from `uns_copilot_backend` to `uns_silver_postgres` is read-only (`SELECT` only) — never `INSERT`/`UPDATE`/`DELETE`. Not DB-role-enforced (matches `UNS_SILVER`'s own read-only relationship to `UNS_HISTORIAN` in this repo); enforced by what the code issues.
- Every tool call is Pydantic-validated before touching the database. A tool failure (bad params, DB error, unknown tool name) is returned as `{"error": "..."}` — it never raises out of `execute_tool`.
- `query_readings` and `list_events` enforce `QUERY_ROW_LIMIT` (row cap) and `RAW_QUERY_MAX_RANGE_HOURS` (max span for `agg="raw"`) from spec Section 4.
- Docker Compose service keys are prefixed `copilot_` (`copilot_postgres`, `copilot_backend`), never bare `postgres`/`backend` — the root `docker-compose.yml`'s `include:` merges services by key across all included files, and a bare key collides with `UNS_MANAGER`'s own `postgres`/`backend` keys (see `UNS_DASHBOARD/docker-compose.yml`'s comment on the same issue).
- Langfuse tracing must never block or break a chat response — every call into it is wrapped and fails open. `infra_net` (the network to reach `langfuse-web` from `INFRAESTRUCTURA/`, per spec Section 1) is **not** wired into this plan's `docker-compose.yml` — `INFRAESTRUCTURA/` doesn't exist yet. Ships as code with `LANGFUSE_ENABLED=false` by default; wiring the network is a follow-up once `INFRAESTRUCTURA/` exists.
- `UNS_SILVER` must already be running (`uns_net`) for anything beyond Task 1-8 to work end-to-end — same standalone-with-prerequisite pattern as `UNS_DASHBOARD`/`UNS_SILVER` themselves.

---

### Task 1: Service scaffold, Dockerfile, Compose wiring, health endpoint

**Files:**
- Create: `UNS_COPILOT/backend/requirements.txt`
- Create: `UNS_COPILOT/backend/Dockerfile`
- Create: `UNS_COPILOT/backend/app/__init__.py`
- Create: `UNS_COPILOT/backend/app/main.py`
- Create: `UNS_COPILOT/backend/tests/__init__.py`
- Test: `UNS_COPILOT/backend/tests/test_health.py`
- Create: `UNS_COPILOT/.env.example`
- Create: `UNS_COPILOT/postgres/init.sql`
- Create: `UNS_COPILOT/docker-compose.yml`
- Create: `UNS_COPILOT/scripts/up.sh`
- Create: `UNS_COPILOT/scripts/down.sh`
- Create: `UNS_COPILOT/scripts/restart.sh`
- Create: `UNS_COPILOT/scripts/logs.sh`
- Create: `UNS_COPILOT/scripts/status.sh`
- Modify: `G:\00_data\00_Formacion\INARI_V06\docker-compose.yml`

**Interfaces:**
- Produces: `app.main.app` (a `FastAPI` instance), `GET /health -> {"status": "ok"}`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_health.py
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `UNS_COPILOT/backend/`): `pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'` (nothing exists yet).

- [ ] **Step 3: Write `requirements.txt`, `Dockerfile`, and `app/main.py`**

```txt
# UNS_COPILOT/backend/requirements.txt
fastapi==0.115.5
uvicorn[standard]==0.32.1
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
pydantic==2.10.3
pydantic-settings==2.7.0
openai==1.57.0
langfuse==2.53.9
httpx==0.28.1
pytest==8.3.3
pytest-asyncio==0.24.0
```

```dockerfile
# UNS_COPILOT/backend/Dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```python
# UNS_COPILOT/backend/app/__init__.py
```

```python
# UNS_COPILOT/backend/app/main.py
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="UNS Copilot",
    description="Natural-language chat backend over UNS_SILVER, via controlled tool-calling",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

```python
# UNS_COPILOT/backend/tests/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 5: Write `.env.example`, `postgres/init.sql`, `docker-compose.yml`, and `scripts/*.sh`**

```bash
# UNS_COPILOT/.env.example
POSTGRES_USER=copilot
POSTGRES_PASSWORD=copilotpassword
POSTGRES_DB=uns_copilot
POSTGRES_PORT=5437
DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@uns_copilot_postgres:5432/uns_copilot

# UNS_SILVER's Postgres (must already be running), read-only usage only
SILVER_DATABASE_URL=postgresql+asyncpg://silver:silverpassword@uns_silver_postgres:5432/uns_silver

# LLM provider (Ollama today; OpenAI-compatible wire format)
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:14b

# Agentic loop guardrails
MAX_ITERATIONS=5
MAX_HISTORY_MESSAGES=40
QUERY_ROW_LIMIT=1000
RAW_QUERY_MAX_RANGE_HOURS=24

# Seed users for the "who are you" selector (no passwords) — comma-separated
SEED_USERS=Operario Juan,Ingeniera Maria,Administrador

# Observability (Langfuse) — disabled by default; INFRAESTRUCTURA/ doesn't exist yet
LANGFUSE_ENABLED=false
LANGFUSE_HOST=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

BACKEND_PORT=8002

# Docker network shared with UNS_MANAGER/UNS_HISTORIAN/UNS_SILVER/UNS_DASHBOARD
UNS_MANAGER_NETWORK_NAME=uns_manager_uns_net
```

```sql
-- UNS_COPILOT/postgres/init.sql
-- Tables are created by the backend's SQLAlchemy metadata on startup
-- (see app/database.py: create_tables). This file exists so the
-- docker-compose volume mount point is documented and ready if a raw-SQL
-- migration is ever needed later.
```

```yaml
# UNS_COPILOT/docker-compose.yml
services:
  # Service keys prefixed "copilot_" — see Global Constraints: the root
  # docker-compose.yml's `include:` merges services by key across all
  # included files, and bare "postgres"/"backend" collide with
  # UNS_MANAGER's own keys.
  copilot_postgres:
    image: postgres:16-alpine
    container_name: uns_copilot_postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-copilot}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-copilotpassword}
      POSTGRES_DB: ${POSTGRES_DB:-uns_copilot}
    volumes:
      - copilot_postgres_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    ports:
      - "${POSTGRES_PORT:-5437}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-copilot} -d ${POSTGRES_DB:-uns_copilot}"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - copilot_net

  copilot_backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: uns_copilot_backend
    environment:
      DATABASE_URL: ${DATABASE_URL:-postgresql+asyncpg://${POSTGRES_USER:-copilot}:${POSTGRES_PASSWORD:-copilotpassword}@uns_copilot_postgres:5432/${POSTGRES_DB:-uns_copilot}}
      SILVER_DATABASE_URL: ${SILVER_DATABASE_URL:-postgresql+asyncpg://silver:silverpassword@uns_silver_postgres:5432/uns_silver}
      LLM_BASE_URL: ${LLM_BASE_URL:-http://localhost:11434/v1}
      LLM_API_KEY: ${LLM_API_KEY:-ollama}
      LLM_MODEL: ${LLM_MODEL:-qwen2.5:14b}
      MAX_ITERATIONS: ${MAX_ITERATIONS:-5}
      MAX_HISTORY_MESSAGES: ${MAX_HISTORY_MESSAGES:-40}
      QUERY_ROW_LIMIT: ${QUERY_ROW_LIMIT:-1000}
      RAW_QUERY_MAX_RANGE_HOURS: ${RAW_QUERY_MAX_RANGE_HOURS:-24}
      SEED_USERS: ${SEED_USERS:-Operario Juan,Ingeniera Maria,Administrador}
      LANGFUSE_ENABLED: ${LANGFUSE_ENABLED:-false}
      LANGFUSE_HOST: ${LANGFUSE_HOST:-}
      LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY:-}
      LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY:-}
    ports:
      - "${BACKEND_PORT:-8002}:8000"
    depends_on:
      copilot_postgres:
        condition: service_healthy
    networks:
      - copilot_net
      - uns_manager_net
    restart: unless-stopped

volumes:
  copilot_postgres_data:
    name: uns_copilot_copilot_postgres_data

networks:
  copilot_net:
    driver: bridge
    name: uns_copilot_copilot_net
  uns_manager_net:
    external: true
    name: ${UNS_MANAGER_NETWORK_NAME:-uns_manager_uns_net}
```

```bash
#!/usr/bin/env bash
# UNS_COPILOT/scripts/up.sh
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d --build
echo "UNS Copilot is starting. Use scripts/status.sh to check container health."
```

```bash
#!/usr/bin/env bash
# UNS_COPILOT/scripts/down.sh
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose down
```

```bash
#!/usr/bin/env bash
# UNS_COPILOT/scripts/restart.sh
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose restart "$@"
```

```bash
#!/usr/bin/env bash
# UNS_COPILOT/scripts/logs.sh
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose logs -f "$@"
```

```bash
#!/usr/bin/env bash
# UNS_COPILOT/scripts/status.sh
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose ps
```

Make the scripts executable: `chmod +x UNS_COPILOT/scripts/*.sh`

- [ ] **Step 6: Wire into the root `docker-compose.yml`**

Add a fifth `include:` entry (edit `G:\00_data\00_Formacion\INARI_V06\docker-compose.yml`):

```yaml
include:
  - UNS_MANAGER/docker-compose.yml
  - UNS_HISTORIAN/docker-compose.yml
  - UNS_DASHBOARD/docker-compose.yml
  - UNS_SILVER/docker-compose.yml
  - UNS_COPILOT/docker-compose.yml
```

Also update the file's top comment block to list `UNS_COPILOT` alongside the other four stacks (same style as the existing four bullet points).

- [ ] **Step 7: Commit**

```bash
git add UNS_COPILOT/backend/requirements.txt UNS_COPILOT/backend/Dockerfile \
        UNS_COPILOT/backend/app/__init__.py UNS_COPILOT/backend/app/main.py \
        UNS_COPILOT/backend/tests/__init__.py UNS_COPILOT/backend/tests/test_health.py \
        UNS_COPILOT/.env.example UNS_COPILOT/postgres/init.sql UNS_COPILOT/docker-compose.yml \
        UNS_COPILOT/scripts/ docker-compose.yml
git commit -m "feat(uns-copilot): scaffold backend service, compose wiring, health endpoint"
```

---

### Task 2: Configuration (`app/config.py`)

**Files:**
- Create: `UNS_COPILOT/backend/app/config.py`
- Test: `UNS_COPILOT/backend/tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings` (pydantic-settings model), `app.config.settings` (module-level singleton instance). Fields: `database_url: str`, `silver_database_url: str`, `llm_base_url: str`, `llm_api_key: str`, `llm_model: str`, `max_iterations: int`, `max_history_messages: int`, `query_row_limit: int`, `raw_query_max_range_hours: int`, `seed_users: str`, `langfuse_enabled: bool`, `langfuse_host: str`, `langfuse_public_key: str`, `langfuse_secret_key: str`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_config.py
import os

from app.config import Settings


def test_defaults_when_env_is_empty(monkeypatch):
    for key in [
        "DATABASE_URL", "SILVER_DATABASE_URL", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
        "MAX_ITERATIONS", "MAX_HISTORY_MESSAGES", "QUERY_ROW_LIMIT", "RAW_QUERY_MAX_RANGE_HOURS",
        "SEED_USERS", "LANGFUSE_ENABLED", "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)
    assert settings.max_iterations == 5
    assert settings.max_history_messages == 40
    assert settings.query_row_limit == 1000
    assert settings.raw_query_max_range_hours == 24
    assert settings.langfuse_enabled is False
    assert settings.seed_users == "Operario Juan,Ingeniera Maria,Administrador"


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("MAX_ITERATIONS", "3")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.max_iterations == 3
    assert settings.langfuse_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Write `app/config.py`**

```python
# UNS_COPILOT/backend/app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot"
    silver_database_url: str = "postgresql+asyncpg://silver:silverpassword@localhost:5436/uns_silver"

    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:14b"

    max_iterations: int = 5
    max_history_messages: int = 40
    query_row_limit: int = 1000
    raw_query_max_range_hours: int = 24

    seed_users: str = "Operario Juan,Ingeniera Maria,Administrador"

    langfuse_enabled: bool = False
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""


settings = Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/config.py UNS_COPILOT/backend/tests/test_config.py
git commit -m "feat(uns-copilot): add environment-variable configuration"
```

---

### Task 3: Database module and models (`users`/`conversations`/`messages`)

**Files:**
- Create: `UNS_COPILOT/backend/app/database.py`
- Create: `UNS_COPILOT/backend/app/models/__init__.py`
- Create: `UNS_COPILOT/backend/app/models/chat.py`
- Test: `UNS_COPILOT/backend/tests/test_models.py`

**Interfaces:**
- Consumes: `app.config.settings` (Task 2).
- Produces: `app.database.Base` (declarative base), `app.database.engine`/`AsyncSessionLocal`/`get_db()` (own Postgres), `app.database.silver_engine`/`SilverSessionLocal`/`get_silver_db()` (read-only against `uns_silver_postgres`), `app.database.create_tables()`. Models: `app.models.chat.User(id, display_name, created_at)`, `Conversation(id, user_id, title, created_at, updated_at)`, `Message(id, conversation_id, role, content, tool_calls, tool_call_id, created_at)`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_models.py
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base
from app.models.chat import User, Conversation, Message

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set; requires a live Postgres (docker compose up -d copilot_postgres)"
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        yield s
        await s.execute(Message.__table__.delete())
        await s.execute(Conversation.__table__.delete())
        await s.execute(User.__table__.delete())
        await s.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_and_message_cascade_from_user(session: AsyncSession):
    user = User(display_name="pytest Operario")
    session.add(user)
    await session.flush()

    conversation = Conversation(user_id=user.id, title="pytest chat")
    session.add(conversation)
    await session.flush()

    message = Message(conversation_id=conversation.id, role="user", content="hello")
    session.add(message)
    await session.commit()

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=None,
        tool_calls=[{"id": "call_1", "name": "get_catalog", "arguments": {}}],
    )
    session.add(assistant_message)
    await session.commit()

    tool_message = Message(
        conversation_id=conversation.id, role="tool", tool_call_id="call_1", content='{"result": []}',
    )
    session.add(tool_message)
    await session.commit()

    result = (
        await session.execute(Message.__table__.select().where(Message.conversation_id == conversation.id))
    ).fetchall()
    assert len(result) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.database'`

- [ ] **Step 3: Write `app/database.py` and `app/models/chat.py`**

```python
# UNS_COPILOT/backend/app/database.py
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Read-only against uns_silver_postgres — see Global Constraints: never
# INSERT/UPDATE/DELETE through this engine.
silver_engine = create_async_engine(settings.silver_database_url, echo=False, future=True)
SilverSessionLocal = async_sessionmaker(silver_engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def get_silver_db() -> AsyncSession:
    async with SilverSessionLocal() as session:
        yield session


async def create_tables() -> None:
    from app.models import chat  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

```python
# UNS_COPILOT/backend/app/models/__init__.py
```

```python
# UNS_COPILOT/backend/app/models/chat.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' | 'assistant' | 'tool'
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSONB)
    tool_call_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run (with a live Postgres — e.g. `cd UNS_COPILOT && cp .env.example .env && docker compose up -d copilot_postgres`, then from `backend/`): `DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/database.py UNS_COPILOT/backend/app/models/ UNS_COPILOT/backend/tests/test_models.py
git commit -m "feat(uns-copilot): add database module and users/conversations/messages models"
```

---

### Task 4: Users router and seeding

**Files:**
- Create: `UNS_COPILOT/backend/app/schemas/__init__.py`
- Create: `UNS_COPILOT/backend/app/schemas/chat.py`
- Create: `UNS_COPILOT/backend/app/seed.py`
- Create: `UNS_COPILOT/backend/app/routers/__init__.py`
- Create: `UNS_COPILOT/backend/app/routers/users.py`
- Modify: `UNS_COPILOT/backend/app/main.py`
- Test: `UNS_COPILOT/backend/tests/test_users_router.py`

**Interfaces:**
- Consumes: `app.database.get_db`, `app.database.create_tables`, `app.models.chat.User` (Task 3); `app.config.settings` (Task 2).
- Produces: `app.schemas.chat.UserRead(id, display_name, created_at)`. `app.seed.seed_default_users(session, names: list[str]) -> None` (idempotent — skips if `users` is non-empty). Router `app.routers.users.router`: `GET /users/ -> list[UserRead]`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_users_router.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_users_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers'`

- [ ] **Step 3: Write `app/schemas/chat.py`, `app/seed.py`, `app/routers/users.py`, and update `app/main.py`**

```python
# UNS_COPILOT/backend/app/schemas/__init__.py
```

```python
# UNS_COPILOT/backend/app/schemas/chat.py
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(_Base):
    id: int
    display_name: str
    created_at: datetime
```

```python
# UNS_COPILOT/backend/app/seed.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import User


async def seed_default_users(session: AsyncSession, names: list[str]) -> None:
    """Idempotent: does nothing if the users table already has any rows,
    so a restart never duplicates or resets the selector's user list."""
    existing = (await session.execute(select(User.id).limit(1))).first()
    if existing is not None:
        return
    for name in names:
        name = name.strip()
        if name:
            session.add(User(display_name=name))
    await session.commit()
```

```python
# UNS_COPILOT/backend/app/routers/__init__.py
```

```python
# UNS_COPILOT/backend/app/routers/users.py
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models.chat import User
from app.schemas.chat import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=list[UserRead])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.display_name))
    return result.scalars().all()
```

```python
# UNS_COPILOT/backend/app/main.py
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import AsyncSessionLocal, create_tables
from app.routers import users
from app.seed import seed_default_users

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    async with AsyncSessionLocal() as session:
        await seed_default_users(session, settings.seed_users.split(","))
    yield


app = FastAPI(
    title="UNS Copilot",
    description="Natural-language chat backend over UNS_SILVER, via controlled tool-calling",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot pytest tests/test_users_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/schemas/ UNS_COPILOT/backend/app/seed.py \
        UNS_COPILOT/backend/app/routers/__init__.py UNS_COPILOT/backend/app/routers/users.py \
        UNS_COPILOT/backend/app/main.py UNS_COPILOT/backend/tests/test_users_router.py
git commit -m "feat(uns-copilot): add users router with idempotent seeding"
```

---

### Task 5: Conversations router and CRUD helpers

**Files:**
- Create: `UNS_COPILOT/backend/app/crud.py`
- Modify: `UNS_COPILOT/backend/app/schemas/chat.py`
- Create: `UNS_COPILOT/backend/app/routers/conversations.py`
- Modify: `UNS_COPILOT/backend/app/main.py`
- Test: `UNS_COPILOT/backend/tests/test_conversations_router.py`

**Interfaces:**
- Consumes: `app.models.chat.{User,Conversation,Message}`, `app.database.get_db` (Task 3); `app.schemas.chat.UserRead` (Task 4).
- Produces: `app.crud.create_message(session, conversation_id, role, content=None, tool_calls=None, tool_call_id=None) -> Message`, `app.crud.load_messages(session, conversation_id, limit) -> list[Message]` (most recent `limit` messages, oldest first). Schemas: `ConversationCreate(user_id: int, title: str | None = None)`, `ConversationRead(id, user_id, title, created_at, updated_at)`, `MessageRead(id, conversation_id, role, content, tool_calls, tool_call_id, created_at)`, `ConversationDetailRead(ConversationRead + messages: list[MessageRead])`. Router `app.routers.conversations.router`: `POST /conversations/ -> ConversationRead`, `GET /conversations/?user_id= -> list[ConversationRead]`, `GET /conversations/{id} -> ConversationDetailRead`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_conversations_router.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_conversations_router.py -v`
Expected: FAIL — `404 Not Found` / no `/conversations/` route (`app.routers.conversations` doesn't exist yet).

- [ ] **Step 3: Write `app/crud.py`, extend `app/schemas/chat.py`, write `app/routers/conversations.py`, update `app/main.py`**

```python
# UNS_COPILOT/backend/app/crud.py
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Message


async def create_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id, role=role, content=content,
        tool_calls=tool_calls, tool_call_id=tool_call_id,
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def load_messages(session: AsyncSession, conversation_id: int, limit: int) -> list[Message]:
    """Most recent `limit` messages for the conversation, returned oldest-first
    so callers can feed them straight into an LLM as chat history."""
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))
```

Append to `app/schemas/chat.py`:

```python
class ConversationCreate(BaseModel):
    user_id: int
    title: str | None = None


class ConversationRead(_Base):
    id: int
    user_id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageRead(_Base):
    id: int
    conversation_id: int
    role: str
    content: str | None
    tool_calls: list[dict] | None
    tool_call_id: str | None
    created_at: datetime


class ConversationDetailRead(ConversationRead):
    messages: list[MessageRead]
```

```python
# UNS_COPILOT/backend/app/routers/conversations.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models.chat import Conversation, Message
from app.schemas.chat import ConversationCreate, ConversationDetailRead, ConversationRead

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/", response_model=ConversationRead, status_code=201)
async def create_conversation(body: ConversationCreate, db: AsyncSession = Depends(get_db)):
    conversation = Conversation(user_id=body.user_id, title=body.title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("/", response_model=list[ConversationRead])
async def list_conversations(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc())
    )
    return result.scalars().all()


@router.get("/{conversation_id}", response_model=ConversationDetailRead)
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = (
        await db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        )
    ).scalars().all()
    return ConversationDetailRead(
        id=conversation.id, user_id=conversation.user_id, title=conversation.title,
        created_at=conversation.created_at, updated_at=conversation.updated_at,
        messages=messages,
    )
```

In `app/main.py`, add the import and registration:

```python
from app.routers import conversations, users
...
app.include_router(users.router)
app.include_router(conversations.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot pytest tests/test_conversations_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/crud.py UNS_COPILOT/backend/app/schemas/chat.py \
        UNS_COPILOT/backend/app/routers/conversations.py UNS_COPILOT/backend/app/main.py \
        UNS_COPILOT/backend/tests/test_conversations_router.py
git commit -m "feat(uns-copilot): add conversations router and message CRUD helpers"
```

---

### Task 6: `LLMProvider` abstraction and fake test double

**Files:**
- Create: `UNS_COPILOT/backend/app/llm/__init__.py`
- Create: `UNS_COPILOT/backend/app/llm/base.py`
- Create: `UNS_COPILOT/backend/app/llm/fake.py`
- Test: `UNS_COPILOT/backend/tests/test_llm_fake.py`

**Interfaces:**
- Produces: `app.llm.base.ToolCall(id: str, name: str, arguments: dict)`, `app.llm.base.ChatResponse(content: str | None, tool_calls: list[ToolCall], finish_reason: str)`, `app.llm.base.LLMProvider` (ABC with `async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse`). `app.llm.fake.FakeLLMProvider(responses: list[ChatResponse])` — pops one scripted response per call, records every `messages` it was called with in `self.calls`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_llm_fake.py
import pytest

from app.llm.base import ChatResponse, ToolCall
from app.llm.fake import FakeLLMProvider


@pytest.mark.asyncio
async def test_fake_provider_returns_scripted_responses_in_order():
    responses = [
        ChatResponse(
            content=None,
            tool_calls=[ToolCall(id="call_1", name="get_catalog", arguments={})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="Here is the catalog.", tool_calls=[], finish_reason="stop"),
    ]
    provider = FakeLLMProvider(responses)

    first = await provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert first.finish_reason == "tool_calls"
    assert first.tool_calls[0].name == "get_catalog"

    second = await provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert second.content == "Here is the catalog."

    assert len(provider.calls) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_fake.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm'`

- [ ] **Step 3: Write `app/llm/base.py` and `app/llm/fake.py`**

```python
# UNS_COPILOT/backend/app/llm/__init__.py
```

```python
# UNS_COPILOT/backend/app/llm/base.py
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class ChatResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall] = []
    finish_reason: str  # "tool_calls" | "stop" | "length" | ...


class LLMProvider(ABC):
    """Provider-agnostic chat interface. No implementation here may leak a
    provider's own wire format (OpenAI-style tool_calls, Anthropic-style
    tool_use blocks, ...) past this boundary — see spec Section 3."""

    @abstractmethod
    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...
```

```python
# UNS_COPILOT/backend/app/llm/fake.py
from __future__ import annotations

from app.llm.base import ChatResponse, LLMProvider


class FakeLLMProvider(LLMProvider):
    """Test double: returns one scripted ChatResponse per call, in order.
    Records every `messages` list it was called with for assertions."""

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        self.calls.append(messages)
        return self._responses.pop(0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_fake.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/llm/ UNS_COPILOT/backend/tests/test_llm_fake.py
git commit -m "feat(uns-copilot): add provider-agnostic LLMProvider interface and fake test double"
```

---

### Task 7: `OllamaAdapter`

**Files:**
- Create: `UNS_COPILOT/backend/app/llm/ollama_adapter.py`
- Test: `UNS_COPILOT/backend/tests/test_ollama_adapter.py`

**Interfaces:**
- Consumes: `app.llm.base.{LLMProvider, ChatResponse, ToolCall}` (Task 6).
- Produces: `app.llm.ollama_adapter.OllamaAdapter(base_url: str, api_key: str, model: str)` implementing `LLMProvider`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_ollama_adapter.py
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm.ollama_adapter import OllamaAdapter


def _fake_openai_response(content, tool_calls, finish_reason):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


@pytest.mark.asyncio
async def test_translates_tool_calls_from_openai_shape():
    adapter = OllamaAdapter(base_url="http://fake:11434/v1", api_key="ollama", model="qwen2.5:14b")
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_catalog", arguments=json.dumps({"topic_filter": "line3"})),
    )
    adapter._client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response(None, [tool_call], "tool_calls")
    )

    response = await adapter.chat(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "get_catalog"
    assert response.tool_calls[0].arguments == {"topic_filter": "line3"}


@pytest.mark.asyncio
async def test_final_answer_has_no_tool_calls():
    adapter = OllamaAdapter(base_url="http://fake:11434/v1", api_key="ollama", model="qwen2.5:14b")
    adapter._client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response("The answer is 42.", None, "stop")
    )

    response = await adapter.chat(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert response.content == "The answer is 42."
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_malformed_tool_call_arguments_become_empty_dict():
    adapter = OllamaAdapter(base_url="http://fake:11434/v1", api_key="ollama", model="qwen2.5:14b")
    tool_call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="get_catalog", arguments="not json"))
    adapter._client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response(None, [tool_call], "tool_calls")
    )

    response = await adapter.chat(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert response.tool_calls[0].arguments == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ollama_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.ollama_adapter'`

- [ ] **Step 3: Write `app/llm/ollama_adapter.py`**

```python
# UNS_COPILOT/backend/app/llm/ollama_adapter.py
from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.llm.base import ChatResponse, LLMProvider, ToolCall

logger = logging.getLogger(__name__)


class OllamaAdapter(LLMProvider):
    """Talks to Ollama's OpenAI-compatible endpoint via the `openai` SDK.
    Ollama, OpenAI, and OpenRouter all speak this wire format, so this
    adapter is reusable almost as-is for those providers later — see spec
    Section 3. A true Anthropic-native adapter is the one that would need
    real translation (tool_use/tool_result blocks instead of tool_calls),
    and is deferred until actually needed."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools or None,
        )
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        for raw_call in (message.tool_calls or []):
            try:
                arguments = json.loads(raw_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                # Never crash on a malformed tool call — the downstream
                # Pydantic parameter validation (Task 8) will reject an
                # empty/wrong argument set and report it back to the model
                # as a tool error instead of blowing up the whole turn.
                logger.warning("Malformed tool call arguments from model: %r", raw_call.function.arguments)
                arguments = {}
            tool_calls.append(ToolCall(id=raw_call.id, name=raw_call.function.name, arguments=arguments))

        return ChatResponse(content=message.content, tool_calls=tool_calls, finish_reason=choice.finish_reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ollama_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/llm/ollama_adapter.py UNS_COPILOT/backend/tests/test_ollama_adapter.py
git commit -m "feat(uns-copilot): add OllamaAdapter over the openai SDK"
```

---

### Task 8: Tool parameter models and guardrails

**Files:**
- Create: `UNS_COPILOT/backend/app/tools/__init__.py`
- Create: `UNS_COPILOT/backend/app/tools/params.py`
- Test: `UNS_COPILOT/backend/tests/test_tool_params.py`

**Interfaces:**
- Produces: `app.tools.params.GetCatalogParams(topic_filter: str | None, signal_type: Literal["raw","kpi"] | None)`, `GetLatestValueParams(topic: str, signal_key: str)`, `QueryReadingsParams(topic: str, signal_key: str, from_time: datetime, to_time: datetime, agg: Literal["raw","1m","1h"])`, `ListEventsParams(topic_filter: str | None, event_key: str | None, from_time: datetime, to_time: datetime)`. `app.tools.params.check_raw_range(params: QueryReadingsParams, max_raw_range_hours: int) -> None` (raises `ValueError` if `agg == "raw"` and the span exceeds the limit).

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_tool_params.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tool_params.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools'`

- [ ] **Step 3: Write `app/tools/params.py`**

```python
# UNS_COPILOT/backend/app/tools/__init__.py
```

```python
# UNS_COPILOT/backend/app/tools/params.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel


class GetCatalogParams(BaseModel):
    topic_filter: str | None = None
    signal_type: Literal["raw", "kpi"] | None = None


class GetLatestValueParams(BaseModel):
    topic: str
    signal_key: str


class QueryReadingsParams(BaseModel):
    topic: str
    signal_key: str
    from_time: datetime
    to_time: datetime
    agg: Literal["raw", "1m", "1h"] = "raw"


class ListEventsParams(BaseModel):
    topic_filter: str | None = None
    event_key: str | None = None
    from_time: datetime
    to_time: datetime


def check_raw_range(params: QueryReadingsParams, max_raw_range_hours: int) -> None:
    """Guardrail from spec Section 4: a raw-resolution query spanning too
    long a window could pull unbounded 1Hz+ data into a (possibly small,
    local-model) context window. Aggregated queries (1m/1h) are exempt --
    they already return a bounded number of buckets regardless of span."""
    if params.agg != "raw":
        return
    span = params.to_time - params.from_time
    if span > timedelta(hours=max_raw_range_hours):
        raise ValueError(
            f"raw query range of {span} exceeds the {max_raw_range_hours}h limit for agg='raw'; "
            "use agg='1m' or agg='1h' for a wider window"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tool_params.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/tools/__init__.py UNS_COPILOT/backend/app/tools/params.py \
        UNS_COPILOT/backend/tests/test_tool_params.py
git commit -m "feat(uns-copilot): add tool parameter models and raw-range guardrail"
```

---

### Task 9: Tool implementations against `uns_silver_postgres`

**Files:**
- Create: `UNS_COPILOT/backend/app/tools/catalog.py`
- Create: `UNS_COPILOT/backend/app/tools/readings.py`
- Create: `UNS_COPILOT/backend/app/tools/events.py`
- Test: `UNS_COPILOT/backend/tests/test_tools_integration.py`

**Interfaces:**
- Consumes: `app.tools.params.{GetCatalogParams, GetLatestValueParams, QueryReadingsParams, ListEventsParams, check_raw_range}` (Task 8); `app.database.get_silver_db`/`SilverSessionLocal` (Task 3).
- Produces: `app.tools.catalog.get_catalog(session, params: GetCatalogParams) -> list[dict]`. `app.tools.readings.get_latest_value(session, params: GetLatestValueParams) -> dict | None`, `app.tools.readings.query_readings(session, params: QueryReadingsParams, row_limit: int, max_raw_range_hours: int) -> list[dict]` (raises `ValueError` via `check_raw_range` if the raw span is too wide). `app.tools.events.list_events(session, params: ListEventsParams, row_limit: int) -> list[dict]`.

This task assumes `UNS_SILVER`'s schema (spec `UNS_SILVER/docs/superpowers/specs/2026-09-05-uns-silver-design.md`, Section 2) already exists in the Postgres pointed to by `SILVER_DATABASE_URL` — run `UNS_SILVER`'s own `./scripts/up.sh` first if testing against a real instance.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_tools_integration.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools_integration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools.catalog'`

- [ ] **Step 3: Write `app/tools/catalog.py`, `app/tools/readings.py`, `app/tools/events.py`**

```python
# UNS_COPILOT/backend/app/tools/catalog.py
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.params import GetCatalogParams


async def get_catalog(session: AsyncSession, params: GetCatalogParams) -> list[dict]:
    query = (
        "SELECT topic, signal_key, signal_type, unit, description, range_min, range_max, thresholds "
        "FROM signal_catalog WHERE effective_until IS NULL"
    )
    bind: dict = {}
    if params.topic_filter:
        query += " AND topic LIKE :topic_prefix"
        bind["topic_prefix"] = f"{params.topic_filter}%"
    if params.signal_type:
        query += " AND signal_type = :signal_type"
        bind["signal_type"] = params.signal_type

    result = await session.execute(text(query), bind)
    return [dict(row) for row in result.mappings()]
```

```python
# UNS_COPILOT/backend/app/tools/readings.py
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.params import GetLatestValueParams, QueryReadingsParams, check_raw_range

_AGG_TABLES = {"raw": "silver_readings", "1m": "silver_readings_1m", "1h": "silver_readings_1h"}
_AGG_TIME_COLUMN = {"raw": "time", "1m": "bucket", "1h": "bucket"}


async def get_latest_value(session: AsyncSession, params: GetLatestValueParams) -> dict | None:
    result = await session.execute(
        text(
            "SELECT topic, signal_key, signal_type, time, value_numeric, value_text "
            "FROM silver_latest_value WHERE topic = :topic AND signal_key = :signal_key"
        ),
        {"topic": params.topic, "signal_key": params.signal_key},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def query_readings(
    session: AsyncSession, params: QueryReadingsParams, row_limit: int, max_raw_range_hours: int
) -> list[dict]:
    check_raw_range(params, max_raw_range_hours)  # raises ValueError if violated -- see spec Section 4

    table = _AGG_TABLES[params.agg]
    time_column = _AGG_TIME_COLUMN[params.agg]
    if params.agg == "raw":
        select_cols = f"{time_column} AS time, value_numeric, value_text"
    else:
        select_cols = f"{time_column} AS time, avg_value, min_value, max_value, sample_count"

    result = await session.execute(
        text(
            f"SELECT {select_cols} FROM {table} "
            f"WHERE topic = :topic AND signal_key = :signal_key AND {time_column} BETWEEN :from_time AND :to_time "
            f"ORDER BY {time_column} LIMIT :row_limit"
        ),
        {
            "topic": params.topic, "signal_key": params.signal_key,
            "from_time": params.from_time, "to_time": params.to_time, "row_limit": row_limit,
        },
    )
    return [dict(row) for row in result.mappings()]
```

```python
# UNS_COPILOT/backend/app/tools/events.py
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.params import ListEventsParams


async def list_events(session: AsyncSession, params: ListEventsParams, row_limit: int) -> list[dict]:
    query = (
        "SELECT time, topic, event_key, payload, signal_type FROM silver_events "
        "WHERE time BETWEEN :from_time AND :to_time"
    )
    bind: dict = {"from_time": params.from_time, "to_time": params.to_time, "row_limit": row_limit}
    if params.topic_filter:
        query += " AND topic LIKE :topic_prefix"
        bind["topic_prefix"] = f"{params.topic_filter}%"
    if params.event_key:
        query += " AND event_key = :event_key"
        bind["event_key"] = params.event_key
    query += " ORDER BY time LIMIT :row_limit"

    result = await session.execute(text(query), bind)
    return [dict(row) for row in result.mappings()]
```

- [ ] **Step 4: Run test to verify it passes**

Run (with `UNS_SILVER` up and its schema applied): `SILVER_DATABASE_URL=postgresql+asyncpg://silver:silverpassword@localhost:5436/uns_silver pytest tests/test_tools_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/tools/catalog.py UNS_COPILOT/backend/app/tools/readings.py \
        UNS_COPILOT/backend/app/tools/events.py UNS_COPILOT/backend/tests/test_tools_integration.py
git commit -m "feat(uns-copilot): implement read-only tools against uns_silver_postgres"
```

---

### Task 10: Tool schemas and executor dispatcher

**Files:**
- Create: `UNS_COPILOT/backend/app/tools/schemas.py`
- Create: `UNS_COPILOT/backend/app/tools/executor.py`
- Test: `UNS_COPILOT/backend/tests/test_tool_executor.py`

**Interfaces:**
- Consumes: `app.tools.params.*` (Task 8), `app.tools.{catalog.get_catalog, readings.get_latest_value, readings.query_readings, events.list_events}` (Task 9).
- Produces: `app.tools.schemas.TOOL_SCHEMAS: list[dict]` (OpenAI function-calling format, passed straight through to `LLMProvider.chat(tools=...)`). `app.tools.executor.execute_tool(session, name: str, arguments: dict, *, row_limit: int, max_raw_range_hours: int) -> dict` — never raises; returns `{"result": ...}` on success or `{"error": "..."}` on any failure (unknown tool, validation error, DB error).

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_tool_executor.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tool_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools.executor'`

- [ ] **Step 3: Write `app/tools/schemas.py` and `app/tools/executor.py`**

```python
# UNS_COPILOT/backend/app/tools/schemas.py
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_catalog",
            "description": (
                "List cataloged signals/KPIs. Use this to discover what signals exist before "
                "querying their readings, or to answer 'what does X measure?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_filter": {
                        "type": "string",
                        "description": "ISA-95 topic prefix to filter by, e.g. 'plant1.line3'. Omit to list everything.",
                    },
                    "signal_type": {
                        "type": "string",
                        "enum": ["raw", "kpi"],
                        "description": "Restrict to raw physical signals or computed KPIs. Omit for both.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_latest_value",
            "description": "Get the current value of one specific signal. Use for 'what is X right now?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "The signal's ISA-95 topic (without any suffix)."},
                    "signal_key": {"type": "string", "description": "The signal's key within that topic."},
                },
                "required": ["topic", "signal_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_readings",
            "description": (
                "Get the historical time series of one signal between two instants, with optional aggregation. "
                "Use agg='1m' or agg='1h' for wide time ranges; agg='raw' is capped to a short window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "The signal's ISA-95 topic (without any suffix)."},
                    "signal_key": {"type": "string", "description": "The signal's key within that topic."},
                    "from_time": {"type": "string", "format": "date-time", "description": "Start of the range, ISO 8601."},
                    "to_time": {"type": "string", "format": "date-time", "description": "End of the range, ISO 8601."},
                    "agg": {
                        "type": "string",
                        "enum": ["raw", "1m", "1h"],
                        "description": "Aggregation level. 'raw' for individual samples (short ranges only), '1m'/'1h' for wider ranges.",
                    },
                },
                "required": ["topic", "signal_key", "from_time", "to_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "List discrete events (alarms, failures) for a topic within a time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_filter": {"type": "string", "description": "ISA-95 topic prefix to filter by. Omit for all topics."},
                    "event_key": {"type": "string", "description": "The event array's field name, e.g. 'alarms'. Omit for all."},
                    "from_time": {"type": "string", "format": "date-time", "description": "Start of the range, ISO 8601."},
                    "to_time": {"type": "string", "format": "date-time", "description": "End of the range, ISO 8601."},
                },
                "required": ["from_time", "to_time"],
            },
        },
    },
]
```

```python
# UNS_COPILOT/backend/app/tools/executor.py
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
    except ValidationError as exc:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tool_executor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/tools/schemas.py UNS_COPILOT/backend/app/tools/executor.py \
        UNS_COPILOT/backend/tests/test_tool_executor.py
git commit -m "feat(uns-copilot): add tool schemas and error-safe executor dispatcher"
```

---

### Task 11: Agentic loop

**Files:**
- Create: `UNS_COPILOT/backend/app/agent/__init__.py`
- Create: `UNS_COPILOT/backend/app/agent/history.py`
- Create: `UNS_COPILOT/backend/app/agent/loop.py`
- Test: `UNS_COPILOT/backend/tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `app.crud.{create_message, load_messages}` (Task 5), `app.llm.base.{LLMProvider, ChatResponse, ToolCall}` and `app.llm.fake.FakeLLMProvider` (Task 6), `app.tools.executor.execute_tool` (Task 10), `app.tools.schemas.TOOL_SCHEMAS` (Task 10).
- Produces: `app.agent.history.to_llm_messages(messages: list[Message]) -> list[dict]` (OpenAI wire format). `app.agent.loop.run_turn(db_session, silver_session, llm: LLMProvider, conversation_id: int, user_text: str, *, max_iterations: int, max_history_messages: int, row_limit: int, max_raw_range_hours: int) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_agent_loop.py
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.llm.base import ChatResponse, ToolCall
from app.llm.fake import FakeLLMProvider
from app.agent.loop import run_turn

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set; requires a live Postgres (docker compose up -d copilot_postgres)"
)


@pytest.fixture
async def conversation_id():
    from app.database import AsyncSessionLocal, create_tables
    from app.models.chat import Conversation, User

    await create_tables()
    async with AsyncSessionLocal() as session:
        user = User(display_name="pytest agent-loop user")
        session.add(user)
        await session.flush()
        conversation = Conversation(user_id=user.id)
        session.add(conversation)
        await session.commit()
        yield conversation.id


@pytest.mark.asyncio
async def test_final_answer_with_no_tool_calls(conversation_id):
    from app.database import AsyncSessionLocal

    llm = FakeLLMProvider([ChatResponse(content="42.", tool_calls=[], finish_reason="stop")])
    async with AsyncSessionLocal() as session:
        reply = await run_turn(
            session, silver_session=None, llm=llm, conversation_id=conversation_id, user_text="what is the answer?",
            max_iterations=5, max_history_messages=40, row_limit=1000, max_raw_range_hours=24,
        )
    assert reply == "42."


@pytest.mark.asyncio
async def test_one_tool_call_then_final_answer(conversation_id):
    from app.database import AsyncSessionLocal

    llm = FakeLLMProvider([
        ChatResponse(
            content=None,
            tool_calls=[ToolCall(id="call_1", name="get_latest_value", arguments={"topic": "line3", "signal_key": "temp"})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="It's 22.0.", tool_calls=[], finish_reason="stop"),
    ])
    with patch("app.agent.loop.execute_tool", new=AsyncMock(return_value={"result": {"value_numeric": 22.0}})):
        async with AsyncSessionLocal() as session:
            reply = await run_turn(
                session, silver_session=None, llm=llm, conversation_id=conversation_id,
                user_text="what is the temperature?", max_iterations=5, max_history_messages=40,
                row_limit=1000, max_raw_range_hours=24,
            )
    assert reply == "It's 22.0."
    # The second call to the LLM must include the tool's result as a 'tool' message.
    assert any(m.get("role") == "tool" for m in llm.calls[1])


@pytest.mark.asyncio
async def test_max_iterations_exhausted_returns_graceful_fallback(conversation_id):
    from app.database import AsyncSessionLocal

    endless_tool_call = ChatResponse(
        content=None,
        tool_calls=[ToolCall(id="call_1", name="get_latest_value", arguments={"topic": "line3", "signal_key": "temp"})],
        finish_reason="tool_calls",
    )
    llm = FakeLLMProvider([endless_tool_call] * 3)
    with patch("app.agent.loop.execute_tool", new=AsyncMock(return_value={"result": {"value_numeric": 22.0}})):
        async with AsyncSessionLocal() as session:
            reply = await run_turn(
                session, silver_session=None, llm=llm, conversation_id=conversation_id,
                user_text="loop forever", max_iterations=3, max_history_messages=40,
                row_limit=1000, max_raw_range_hours=24,
            )
    assert "not" in reply.lower() or "could not" in reply.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent'`

- [ ] **Step 3: Write `app/agent/history.py` and `app/agent/loop.py`**

```python
# UNS_COPILOT/backend/app/agent/__init__.py
```

```python
# UNS_COPILOT/backend/app/agent/history.py
from __future__ import annotations

from app.models.chat import Message


def to_llm_messages(messages: list[Message]) -> list[dict]:
    """Rows -> OpenAI chat-format dicts, the shape every LLMProvider adapter
    expects (see spec Section 2 -- the schema was designed to make this a
    direct mapping, no translation logic)."""
    result = []
    for m in messages:
        if m.role == "tool":
            result.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        elif m.role == "assistant" and m.tool_calls:
            result.append({
                "role": "assistant",
                "content": m.content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": __import__("json").dumps(tc["arguments"])},
                    }
                    for tc in m.tool_calls
                ],
            })
        else:
            result.append({"role": m.role, "content": m.content or ""})
    return result
```

```python
# UNS_COPILOT/backend/app/agent/loop.py
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.history import to_llm_messages
from app.crud import create_message, load_messages
from app.llm.base import LLMProvider
from app.tools.executor import execute_tool
from app.tools.schemas import TOOL_SCHEMAS

_FALLBACK_REPLY = "Could not complete the request after several attempts — please rephrase the question."


async def run_turn(
    db_session: AsyncSession,
    silver_session: AsyncSession,
    llm: LLMProvider,
    conversation_id: int,
    user_text: str,
    *,
    max_iterations: int,
    max_history_messages: int,
    row_limit: int,
    max_raw_range_hours: int,
) -> str:
    await create_message(db_session, conversation_id, role="user", content=user_text)

    for _ in range(max_iterations):
        history = await load_messages(db_session, conversation_id, limit=max_history_messages)
        response = await llm.chat(messages=to_llm_messages(history), tools=TOOL_SCHEMAS)

        await create_message(
            db_session, conversation_id, role="assistant", content=response.content,
            tool_calls=[tc.model_dump() for tc in response.tool_calls] or None,
        )

        if not response.tool_calls:
            return response.content or ""

        for call in response.tool_calls:
            result = await execute_tool(
                silver_session, call.name, call.arguments, row_limit=row_limit, max_raw_range_hours=max_raw_range_hours,
            )
            await create_message(
                db_session, conversation_id, role="tool", tool_call_id=call.id, content=json.dumps(result),
            )

    return _FALLBACK_REPLY
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot pytest tests/test_agent_loop.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/agent/ UNS_COPILOT/backend/tests/test_agent_loop.py
git commit -m "feat(uns-copilot): add the manual agentic tool-calling loop"
```

---

### Task 12: Chat endpoint

**Files:**
- Modify: `UNS_COPILOT/backend/app/schemas/chat.py`
- Create: `UNS_COPILOT/backend/app/routers/chat.py`
- Modify: `UNS_COPILOT/backend/app/main.py`
- Test: `UNS_COPILOT/backend/tests/test_chat_router.py`

**Interfaces:**
- Consumes: `app.agent.loop.run_turn` (Task 11), `app.database.{get_db, get_silver_db}` (Task 3), `app.llm.base.LLMProvider` / `app.llm.ollama_adapter.OllamaAdapter` (Task 6/7), `app.config.settings` (Task 2).
- Produces: `app.schemas.chat.ChatMessageCreate(text: str)`, `app.schemas.chat.ChatMessageResponse(conversation_id: int, reply: str)`. `app.routers.chat.get_llm_provider()` (FastAPI dependency, singleton `OllamaAdapter`, overridable in tests). Router `app.routers.chat.router`: `POST /conversations/{id}/messages -> ChatMessageResponse`.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_chat_router.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_llm_provider' from 'app.routers.chat'` (module doesn't exist yet).

- [ ] **Step 3: Extend `app/schemas/chat.py`, write `app/routers/chat.py`, update `app/main.py`**

Append to `app/schemas/chat.py`:

```python
class ChatMessageCreate(BaseModel):
    text: str


class ChatMessageResponse(BaseModel):
    conversation_id: int
    reply: str
```

```python
# UNS_COPILOT/backend/app/routers/chat.py
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import run_turn
from app.config import settings
from app.database import get_db, get_silver_db
from app.llm.base import LLMProvider
from app.llm.ollama_adapter import OllamaAdapter
from app.models.chat import Conversation
from app.schemas.chat import ChatMessageCreate, ChatMessageResponse

router = APIRouter(prefix="/conversations", tags=["chat"])


@lru_cache
def _default_llm_provider() -> LLMProvider:
    return OllamaAdapter(base_url=settings.llm_base_url, api_key=settings.llm_api_key, model=settings.llm_model)


def get_llm_provider() -> LLMProvider:
    """FastAPI dependency -- overridden with a FakeLLMProvider in tests via
    app.dependency_overrides, never patched globally."""
    return _default_llm_provider()


@router.post("/{conversation_id}/messages", response_model=ChatMessageResponse)
async def send_message(
    conversation_id: int,
    body: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    silver_db: AsyncSession = Depends(get_silver_db),
    llm: LLMProvider = Depends(get_llm_provider),
):
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    reply = await run_turn(
        db, silver_db, llm, conversation_id, body.text,
        max_iterations=settings.max_iterations,
        max_history_messages=settings.max_history_messages,
        row_limit=settings.query_row_limit,
        max_raw_range_hours=settings.raw_query_max_range_hours,
    )
    return ChatMessageResponse(conversation_id=conversation_id, reply=reply)
```

In `app/main.py`, add the import and registration:

```python
from app.routers import chat, conversations, users
...
app.include_router(users.router)
app.include_router(conversations.router)
app.include_router(chat.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot pytest tests/test_chat_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add UNS_COPILOT/backend/app/schemas/chat.py UNS_COPILOT/backend/app/routers/chat.py \
        UNS_COPILOT/backend/app/main.py UNS_COPILOT/backend/tests/test_chat_router.py
git commit -m "feat(uns-copilot): add the chat endpoint wiring the agentic loop into the API"
```

---

### Task 13: Observability (Langfuse, fail-open)

**Files:**
- Create: `UNS_COPILOT/backend/app/observability.py`
- Modify: `UNS_COPILOT/backend/app/agent/loop.py`
- Test: `UNS_COPILOT/backend/tests/test_observability.py`

**Interfaces:**
- Consumes: `app.config.settings` (Task 2).
- Produces: `app.observability.get_tracer() -> Tracer` (singleton; `LangfuseTracer` if `settings.langfuse_enabled` and construction succeeds, else `NoOpTracer`). `Tracer.span(name: str, **metadata)` — an async context manager that never raises, regardless of what happens inside or during Langfuse I/O.

- [ ] **Step 1: Write the failing test**

```python
# UNS_COPILOT/backend/tests/test_observability.py
import pytest

from app.observability import NoOpTracer


@pytest.mark.asyncio
async def test_noop_tracer_span_never_raises():
    tracer = NoOpTracer()
    async with tracer.span("run_turn", conversation_id=1):
        pass  # must not raise, must not require any network access


@pytest.mark.asyncio
async def test_noop_tracer_swallows_exceptions_raised_inside_the_body():
    tracer = NoOpTracer()
    with pytest.raises(ValueError):
        async with tracer.span("run_turn"):
            raise ValueError("boom")  # the tracer must propagate the caller's own error...
    # ...but must never raise one of its own on top of it (nothing to assert
    # here beyond the block above not raising a *different* exception type).
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_observability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability'`

- [ ] **Step 3: Write `app/observability.py`**

```python
# UNS_COPILOT/backend/app/observability.py
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator, Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class Tracer(Protocol):
    def span(self, name: str, **metadata) -> AsyncIterator[None]: ...


class NoOpTracer:
    """Default tracer: does nothing, costs nothing, never touches the
    network. Used whenever Langfuse is disabled or unreachable -- tracing
    must never block or break a chat response (see Global Constraints)."""

    @asynccontextmanager
    async def span(self, name: str, **metadata) -> AsyncIterator[None]:
        yield


class LangfuseTracer:
    """Thin wrapper over the Langfuse low-level client. Every Langfuse call
    is isolated in its own try/except so a Langfuse outage, a wrong host,
    or an SDK error degrades to "no trace recorded", never to a broken
    chat response."""

    def __init__(self, public_key: str, secret_key: str, host: str):
        from langfuse import Langfuse  # imported lazily so importing this
        # module never requires the langfuse package to be installed/working
        # when LANGFUSE_ENABLED=false.

        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    @asynccontextmanager
    async def span(self, name: str, **metadata) -> AsyncIterator[None]:
        trace = None
        try:
            trace = self._client.trace(name=name, metadata=metadata)
        except Exception as exc:  # noqa: BLE001 - fail open, see class docstring
            logger.warning("Langfuse trace() failed, continuing without tracing: %s", exc)

        try:
            yield
        finally:
            if trace is not None:
                try:
                    trace.update(output="ok")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Langfuse trace.update() failed: %s", exc)


@lru_cache
def get_tracer() -> Tracer:
    if not settings.langfuse_enabled:
        return NoOpTracer()
    try:
        return LangfuseTracer(
            public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key, host=settings.langfuse_host,
        )
    except Exception as exc:  # noqa: BLE001 - fail open, see class docstring above
        logger.warning("Failed to construct LangfuseTracer, falling back to NoOpTracer: %s", exc)
        return NoOpTracer()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_observability.py -v`
Expected: PASS

- [ ] **Step 5: Wire the tracer into the agentic loop**

Edit `app/agent/loop.py` — wrap the per-turn work in a span (import and use `get_tracer()`):

```python
# Add to the imports at the top of app/agent/loop.py
from app.observability import get_tracer
```

Replace the function body of `run_turn` so the whole turn runs inside one span, and each tool execution gets its own nested span:

```python
async def run_turn(
    db_session: AsyncSession,
    silver_session: AsyncSession,
    llm: LLMProvider,
    conversation_id: int,
    user_text: str,
    *,
    max_iterations: int,
    max_history_messages: int,
    row_limit: int,
    max_raw_range_hours: int,
) -> str:
    tracer = get_tracer()
    async with tracer.span("run_turn", conversation_id=conversation_id):
        await create_message(db_session, conversation_id, role="user", content=user_text)

        for _ in range(max_iterations):
            history = await load_messages(db_session, conversation_id, limit=max_history_messages)
            response = await llm.chat(messages=to_llm_messages(history), tools=TOOL_SCHEMAS)

            await create_message(
                db_session, conversation_id, role="assistant", content=response.content,
                tool_calls=[tc.model_dump() for tc in response.tool_calls] or None,
            )

            if not response.tool_calls:
                return response.content or ""

            for call in response.tool_calls:
                async with tracer.span("tool_call", name=call.name, arguments=call.arguments):
                    result = await execute_tool(
                        silver_session, call.name, call.arguments,
                        row_limit=row_limit, max_raw_range_hours=max_raw_range_hours,
                    )
                await create_message(
                    db_session, conversation_id, role="tool", tool_call_id=call.id, content=json.dumps(result),
                )

        return _FALLBACK_REPLY
```

- [ ] **Step 6: Run the full test suite to confirm nothing broke**

Run (from `UNS_COPILOT/backend/`, with `DATABASE_URL` and `SILVER_DATABASE_URL` set against live instances): `pytest -v`
Expected: All tests PASS, including `tests/test_agent_loop.py` (the loop's behavior is unchanged — only wrapped in no-op spans, since `LANGFUSE_ENABLED` defaults to `false`).

- [ ] **Step 7: Commit**

```bash
git add UNS_COPILOT/backend/app/observability.py UNS_COPILOT/backend/app/agent/loop.py \
        UNS_COPILOT/backend/tests/test_observability.py
git commit -m "feat(uns-copilot): add fail-open Langfuse observability hook in the agentic loop"
```

---

### Task 14: README and manual end-to-end verification

**Files:**
- Create: `UNS_COPILOT/README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Write `UNS_COPILOT/README.md`**

```markdown
# UNS Copilot

Natural-language chat backend over `UNS_SILVER` data, via controlled
tool-calling (never free-form SQL). See
`docs/superpowers/specs/2026-09-06-uns-copilot-design.md` for the full design.

## Running standalone

Requires `UNS_SILVER` already running (for the catalog/readings/events tools)
and an Ollama instance reachable at `LLM_BASE_URL` with a tool-calling-capable
model pulled (e.g. `ollama pull qwen2.5:14b`):

```bash
cd UNS_SILVER && docker compose up -d
cd ../UNS_COPILOT && cp .env.example .env && ./scripts/up.sh
```

- Backend: http://localhost:8002 (docs at `/docs`)

Langfuse tracing is optional and disabled by default (`LANGFUSE_ENABLED=false`)
— `INFRAESTRUCTURA/`'s Langfuse stack is a separate, not-yet-built prerequisite
(see the spec's "Explicitly deferred" section). Chat works with it off.

## End-to-end smoke test

1. `docker compose up -d` from the repo root (or standalone per above).
2. `curl http://localhost:8002/users/` — confirm the seeded users appear.
3. Create a conversation:
   `curl -X POST http://localhost:8002/conversations/ -H "Content-Type: application/json" -d '{"user_id": 1}'`
4. Send a message referencing a real signal already flowing through `UNS_SILVER`:
   `curl -X POST http://localhost:8002/conversations/1/messages -H "Content-Type: application/json" -d '{"text": "what signals do you know about?"}'`
5. Confirm the model calls `get_catalog` and answers in natural language —
   check `GET /conversations/1` to see the full turn (user → assistant tool
   call → tool result → assistant final answer) persisted in order.
6. Ask a follow-up in the same conversation ("and what's its current value?")
   and confirm it resolves correctly using the prior turn's context.
```

- [ ] **Step 2: Commit**

```bash
git add UNS_COPILOT/README.md
git commit -m "docs(uns-copilot): add README with standalone run instructions and smoke test"
```

---

## Self-Review

**Spec coverage:** Section 1 (architecture/compose) → Task 1. Section 2 (data model) → Task 3. Section 3 (LLM abstraction) → Tasks 6-7. Section 4 (tools + guardrails) → Tasks 8-10. Section 5 (agentic loop, error handling, observability hook) → Tasks 11 & 13. Section 6 (deployment) → Task 1 + README (Task 14). Section 7 (testing) → unit tests throughout, integration tests in Tasks 3/4/5/9/11/12, manual verification in Task 14. Users/conversations CRUD (spec Section 2's rationale) → Tasks 4-5. All spec sections have a corresponding task.

**Placeholder scan:** No "TBD"/"TODO"/"similar to Task N" found — every step has complete, runnable code, including full test bodies.

**Type consistency:** `ChatResponse`/`ToolCall` (Task 6) are used identically by `OllamaAdapter` (Task 7), `FakeLLMProvider` (Task 6/11/12), and `run_turn` (Task 11). `execute_tool`'s signature (`session, name, arguments, *, row_limit, max_raw_range_hours`) is identical between its definition (Task 10) and its call sites in `run_turn` (Task 11/13). `create_message`/`load_messages` (Task 5) are used with matching signatures in `run_turn` (Task 11). `run_turn`'s signature is identical between Task 11's definition, Task 12's router call, and Task 13's rewritten body.
