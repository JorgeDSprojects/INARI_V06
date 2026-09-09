# INARI_V05

**An industrial SCADA system built as a Unified Namespace (UNS) architecture**: it connects machines, sensors, and PLCs on a plant floor to a full history, a clean and normalized data layer, real-time dashboards, and a natural-language assistant over the plant's own data — all as independent services that deploy and scale on their own.

---

## For non-technical reviewers (HR)

This project is a complete industrial monitoring system, of the kind used in factories and power plants to know at all times what's happening with their machines: temperatures, speeds, alarms, consumption.

What it demonstrates, beyond the specific industrial domain:

- **End-to-end distributed systems design**: this isn't a single app — it's five independent services that talk to each other, each with its own database, its own lifecycle, and the ability to start standalone or alongside the others.
- **Real data engineering**: raw data coming off the machines goes through several layers until it becomes information with actual meaning (name, unit, thresholds, version history) — the same "bronze → silver" pattern used in large-scale data projects.
- **Backend, frontend, databases, and infrastructure**, all in one project: Python APIs (FastAPI), React interfaces, relational and time-series databases (PostgreSQL/TimescaleDB), real-time messaging (MQTT), all packaged and orchestrated with Docker.
- **Engineering discipline**: every new feature is documented first (what will be built and why), implemented with automated tests, and reviewed before being considered done — this isn't improvised code, there's a real process behind it.
- **The ability to carry a real project through to completion**: from the first line of code to a system that starts with a single command and genuinely works.

If you're looking for someone capable of designing, building, and maintaining a system with this many moving pieces at once, this project is a direct sample of that work.

---

## For a technical reviewer

### Architecture

Five independent services, each with its own `docker-compose.yml`, orchestrated together by the root `docker-compose.yml` (which simply includes all of them under one shared Docker network):

```
                    ┌──────────────┐
   Sensors/PLCs  →  │  UNS_MANAGER │  MQTT (EMQX) + Node-RED + its own API/panel
                    └──────┬───────┘
                           │ raw MQTT messages ("bronze")
                           ▼
                    ┌──────────────┐
                    │ UNS_HISTORIAN│  full, unfiltered history (TimescaleDB)
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  UNS_SILVER  │  normalizes: versioned signal catalog,
                    └──────┬───────┘  typed readings, event/alarm log
                           │
                           ▼
                    ┌──────────────┐
                    │ UNS_COPILOT  │  natural-language assistant: an LLM that
                    └──────────────┘  answers by querying UNS_SILVER via tools

                    ┌──────────────┐
                    │ UNS_DASHBOARD│  live and historical charting dashboards
                    └──────────────┘  (reads from UNS_HISTORIAN + its own DB)
```

| Service | Responsibility | Stack | Ports (host) |
|---|---|---|---|
| **UNS_MANAGER** | Entry point: receives machines' MQTT traffic, models the asset hierarchy (an ISA-95 tree), automation via Node-RED | EMQX (MQTT 5.0 broker), Node-RED, FastAPI, React, PostgreSQL | Postgres 5433, MQTT 1883, EMQX dashboard 18083, API 8000, frontend 3001, Node-RED 1880 |
| **UNS_HISTORIAN** | Archives **every** MQTT message exactly as it arrives — the raw, unfiltered ("bronze") history | TimescaleDB, pgAdmin, a dedicated MQTT ingestor (deduplication, batched flush buffer, resilient reconnection) | Postgres 5434, pgAdmin 5051 |
| **UNS_SILVER** | Turns the raw history into meaningful data: a versioned signal catalog (with thresholds and a change history), typed readings, continuous aggregates (1m/1h), an event log | TimescaleDB (continuous aggregates), pgAdmin, a bronze-to-silver normalizer | Postgres 5436 |
| **UNS_COPILOT** | Answers plant questions in natural language: an LLM reads `UNS_SILVER` through a fixed set of tools (catalog, latest value, historical readings, events) — never free-form SQL — and keeps the conversation history per user | FastAPI + async SQLAlchemy, PostgreSQL, an OpenAI-compatible LLM (Ollama), optional Langfuse tracing | Postgres 5437, backend 8002 |
| **UNS_DASHBOARD** | Visual dashboards: live and historical charts, full dashboard/chart CRUD, dashboard publishing | FastAPI + async SQLAlchemy, React + Vite + TypeScript, PostgreSQL, Redis (an MQTT→Redis bridge for low-latency live data) | Postgres 5435, backend 8001, frontend 3002 |

### Notable design decisions

- **A medallion pattern (bronze → silver)** applied to industrial time-series data: `UNS_HISTORIAN` stores the raw data with zero interpretation (so nothing is ever lost), and `UNS_SILVER` is the single layer that decides what each signal actually means — with **real versioning**: changing a signal's unit or thresholds doesn't overwrite history, it closes the old version (`effective_until`) and opens a new one.
- **TimescaleDB continuous aggregates** (`silver_readings_1m`/`_1h`) so querying a wide historical range doesn't mean scanning millions of raw rows.
- **Per-service Docker networking**, with one shared network (`uns_manager_net`) reserved only for what genuinely needs to cross services — every `docker-compose.yml` still works standalone.
- **A deduplicating MQTT ingestor with a bounded buffer**: avoids duplicate rows on reconnects and never lets memory grow unbounded if Postgres becomes temporarily unreachable.
- **An LLM that queries through tools, never through free-form SQL**: `UNS_COPILOT` exposes exactly four read-only tools over `UNS_SILVER`, each with validated parameters and row/time-range limits — so the model can answer open questions without ever being able to write, or to pull an unbounded result set into its context window.

### Running it

```bash
docker compose up -d        # all five services together, from the repo root
```

Each service can also start on its own, from its own folder — downstream services need their upstreams' Docker network already up, so in order:

```bash
cd UNS_MANAGER    && docker compose up -d
cd ../UNS_HISTORIAN && docker compose up -d
cd ../UNS_SILVER    && docker compose up -d
cd ../UNS_COPILOT   && docker compose up -d   # needs UNS_SILVER
cd ../UNS_DASHBOARD && docker compose up -d
```

### Documentation

- **Design specs and implementation plans**, per feature: `<service>/docs/superpowers/specs/` and `<service>/docs/superpowers/plans/`
- **Repository development rules**: [`AGENTS.md`](AGENTS.md)
- **Step-by-step verification guides**: [`manual/`](manual/) (English and Spanish)
- **Each service's own README**, with setup/operation details: `UNS_MANAGER/`, `UNS_HISTORIAN/README.md`, `UNS_SILVER/README.md`, `UNS_COPILOT/README.md`, `UNS_DASHBOARD/README.md`
