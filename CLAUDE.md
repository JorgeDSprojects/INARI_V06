# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An industrial SCADA/UNS (Unified Namespace) system built as **four independent services**, each with its own `docker-compose.yml`, database, and lifecycle. The root `docker-compose.yml` just `include:`s all four under one shared network so they can be brought up together; every service also runs standalone.

```
Sensors/PLCs → UNS_MANAGER (MQTT/EMQX + Node-RED + ISA-95 hierarchy API/panel)
                    │  raw MQTT messages ("bronze")
                    ▼
               UNS_HISTORIAN (full unfiltered history, TimescaleDB)
                    │
                    ▼
               UNS_SILVER (normalizes: versioned signal catalog, typed readings, event log)

               UNS_DASHBOARD (live + historical charting; reads UNS_HISTORIAN + its own DB)
```

| Service | Responsibility | Stack | Key host ports |
|---|---|---|---|
| `UNS_MANAGER` | Entry point: receives MQTT traffic, models the asset hierarchy (ISA-95 tree: enterprise→site→area→line→cell), automation via Node-RED | EMQX (MQTT 5.0), Node-RED, FastAPI, React, Postgres | Postgres 5433, MQTT 1883, EMQX dashboard 18083, API 8000, frontend 3001, Node-RED 1880 |
| `UNS_HISTORIAN` | Archives **every** MQTT message as-is into a TimescaleDB hypertable — the raw "bronze" layer. Dedicated ingestor: dedup, batched flush buffer, resilient reconnect | TimescaleDB, pgAdmin, MQTT ingestor | Postgres 5434, pgAdmin 5051 |
| `UNS_SILVER` | Turns bronze into meaning: versioned signal catalog (units/thresholds with real history via `effective_until`), typed readings, continuous aggregates (1m/1h), event log | TimescaleDB, pgAdmin, bronze-to-silver normalizer | Postgres 5436 |
| `UNS_DASHBOARD` | Dashboard/chart CRUD + live and historical charts | FastAPI + async SQLAlchemy, React+Vite+TS, Postgres, Redis (MQTT→Redis bridge) | Postgres 5435, backend 8001, frontend 3002 |

This is a medallion (bronze→silver) architecture applied to industrial time-series data: `UNS_HISTORIAN` never interprets anything (nothing is ever lost), `UNS_SILVER` is the single place that decides what a signal means, and changing a signal's unit/thresholds closes the old catalog version rather than overwriting it.

`UNS_HISTORIAN`'s ingestor emits a Postgres `NOTIFY silver_updates` after each insert; `UNS_SILVER`'s normalizer `LISTEN`s for it and processes newly-arrived bronze rows, falling back to polling every `NORMALIZER_POLL_INTERVAL_SECONDS` in case a notification is missed (e.g. across a restart). On first run the normalizer's watermark starts at 0 and replays the entire bronze history in one uninterrupted sequence of batches before switching to incremental processing — see `_process_until_caught_up` / `_safe_process_until_caught_up` in `UNS_SILVER/normalizer/app/main.py`.

`UNS_DASHBOARD`'s live charts do **not** read `UNS_HISTORIAN` — they browse topics currently flowing through Redis Streams via its own `bridge` service (`EMQX → bridge → Redis Stream → backend WebSocket → browser`). Historical charts query `UNS_HISTORIAN` directly.

## Cross-service dependencies (bring-up order)

Each service can run standalone, but downstream services need their upstreams' Docker network already up:

- `UNS_HISTORIAN` needs `UNS_MANAGER` running first (connects to its `emqx` broker over the `uns_manager_uns_net` network it creates).
- `UNS_SILVER` needs `UNS_HISTORIAN` running first (reads its Postgres `id`/`NOTIFY` hook — existing instances need the migration in `UNS_HISTORIAN/postgres/migrations/`).
- `UNS_DASHBOARD` needs both `UNS_MANAGER` (EMQX) and `UNS_HISTORIAN` (history/signal catalog) running first.

```bash
docker compose up -d                    # everything, from repo root — handles ordering via depends_on/include
# or standalone, in order:
cd UNS_MANAGER && docker compose up -d
cd ../UNS_HISTORIAN && docker compose up -d
cd ../UNS_SILVER && docker compose up -d
cd ../UNS_DASHBOARD && docker compose up -d
```

## Commands

`UNS_HISTORIAN`, `UNS_SILVER`, and `UNS_DASHBOARD` each expose the same operational scripts in `<service>/scripts/`:

```bash
cp .env.example .env      # first-time setup, per service
./scripts/up.sh            # build and start all containers
./scripts/down.sh          # stop and remove containers
./scripts/restart.sh [svc] # restart one service, or the whole stack
./scripts/logs.sh [svc]    # tail logs (all, or one container)
./scripts/status.sh        # show container status
```

`UNS_MANAGER` has no `scripts/` dir — use `docker compose up -d` / `docker compose logs -f <service>` directly from `UNS_MANAGER/`.

### Backend tests (Python, pytest)

Only `UNS_HISTORIAN/ingestor`, `UNS_SILVER/normalizer`, and `UNS_DASHBOARD/backend` (+ `UNS_DASHBOARD/bridge`) have test suites. `UNS_MANAGER/backend` currently has none. Tests run locally against a venv, not inside Docker (the Dockerfiles `COPY tests ./tests` but don't run them at build time):

```bash
cd UNS_HISTORIAN/ingestor && pip install -r requirements.txt && pytest
cd UNS_SILVER/normalizer && pip install -r requirements.txt && pytest
cd UNS_DASHBOARD/backend && pip install -r requirements.txt && pytest
cd UNS_DASHBOARD/bridge && pip install -r requirements.txt && pytest

pytest tests/test_dedup.py::test_something   # single test, run from the service dir
```

`UNS_DASHBOARD/backend/tests/conftest.py` disposes the shared async SQLAlchemy engines after every test — each router test spins up its own `TestClient(app)` on a fresh event loop, and asyncpg connections are bound to the loop that created them, so pooled connections must not survive across tests.

### Frontend (React + Vite + TypeScript)

```bash
cd UNS_MANAGER/frontend && npm install && npm run dev      # no test script defined
cd UNS_DASHBOARD/frontend && npm install && npm run dev
cd UNS_DASHBOARD/frontend && npm test                       # vitest run
cd UNS_DASHBOARD/frontend && npm run build                  # tsc && vite build
```

## Repository conventions (from AGENTS.md — read it for the full rule set)

- Terminal interaction with the project owner is in Spanish; code, identifiers, comments, and commit messages are in English.
- Project documentation lives in English under `<service>/docs/`; each feature's design docs follow the superpowers pattern: `docs/superpowers/specs/YYYY-MM-DD-<topic>.md` (design) and `docs/superpowers/plans/YYYY-MM-DD-<topic>.md` (implementation plan, in Spanish where noted).
- Every runnable service needs a `Dockerfile`; `docker compose` is the standard local orchestrator; inter-container calls use Docker service DNS names, never `localhost`.
- Config via env vars, documented per service, with `.env.example` versioned and real `.env` gitignored. Never hardcode secrets.
- Short-lived branches (`feature/*`, `fix/*`, `chore/*`); no direct pushes to `main`/`master`; PRs required.

## Where to look for more detail

- Root `README.md` — architecture diagram and per-service responsibilities (kept in sync with this file's summary above).
- `<service>/README.md` (all but `UNS_MANAGER`) — quickstart, ports, and how to verify that service is working end-to-end.
- `<service>/docs/superpowers/specs/*-design.md` — the authoritative design doc per service (data model, processing pipeline, decisions).
- `manual/En/` and `manual/es/` — step-by-step guides for verifying data actually lands in the historian via pgAdmin.
