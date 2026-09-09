# UNS Copilot — Natural-Language Chat Backend Design Spec
**Date:** 2026-09-06
**Status:** Approved — confirmed by project owner 2026-09-09
**Scope:** Backend service only — chat API, conversation persistence, and a closed set of typed tools that let an LLM answer questions about `UNS_SILVER` data via controlled tool-calling (never free-form SQL). Chat UI and report export (Markdown/PDF/Excel) are explicitly out of scope — separate future sub-projects, each with its own spec/plan cycle.

---

## Context

`UNS_SILVER`'s design spec already names this as its own next milestone: *"LLM/agent tool layer for querying Silver ... the next sub-project, built on top of this spec."* `UNS_SILVER` provides exactly the substrate an agent needs — a versioned semantic catalog, typed readings with continuous aggregates, and an event log — so this milestone is the access layer on top, not a new data model.

The core risk this design avoids is letting an LLM write or approve free-form SQL against production data — imprecise, and a security surface. Instead, the LLM is given a **closed set of typed tools** (function calling / tool use) and decides which to call and with what parameters; the application, not the model, controls what SQL actually runs.

**No vendor lock.** Testing runs against **Ollama**, self-hosted on local hardware (DGX Spark / RTX 5090) — chosen explicitly over any hosted LLM API for this phase. Ollama exposes an OpenAI-compatible `/v1/chat/completions` endpoint, including tool calling, for models that support it. Other providers (OpenAI, OpenRouter, Anthropic) are a later phase, not built now — the design isolates provider-specific request/response shapes behind one internal interface so adding them later touches only one adapter, never the application logic.

---

## Key Decisions (from clarifying questions)

| Decision | Choice |
|---|---|
| Round scope | Backend only this round — service, tools, agentic loop. Chat UI and export are separate future sub-projects. |
| LLM provider | No vendor lock. Custom `LLMProvider` interface with one adapter per provider. First adapter: `OllamaAdapter`, implemented over the `openai` SDK pointed at Ollama's OpenAI-compatible endpoint (Ollama, OpenAI, and OpenRouter all speak the same wire format; only a true Anthropic-native adapter would need real translation, and is deferred). |
| Model selection | Configurable via `.env` (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) — not hardcoded, not chosen in this spec. Must be an Ollama-hosted model with tool-calling support (e.g. Qwen2.5, Llama 3.1). |
| Tool-calling loop | Hand-written manual loop, not Anthropic's SDK Tool Runner (that's Anthropic-specific; the primary provider here is Ollama). Loops until the model returns a final answer with no further tool calls, bounded by `MAX_ITERATIONS`. |
| Conversation model | Multi-turn with persistent history (not stateless, not in-memory-only) — a real conversational copilot needs follow-up questions ("¿y ayer?") to resolve against prior turns. |
| User identity | No real authentication in v1 (consistent with the other three stacks). A lightweight user **selector** (no password) gives a stable, human-readable `user_id` for scoping conversations — forward-compatible with real auth later (only how `user_id` is derived changes; the schema doesn't). |
| Conversations per user | Multiple independent conversations per user (ChatGPT-style "new chat"), not one ever-growing thread. |
| Tools | Four: `get_catalog`, `get_latest_value`, `query_readings`, `list_events` — mapped 1:1 to `UNS_SILVER` tables. `get_latest_value` reuses `silver_latest_value` (already built for O(1) "current value" lookups) rather than resolving "what's the value now" through a generic range query. |
| Guardrails | All tools are read-only against `uns_silver_postgres`. Parameters are Pydantic-validated before touching the database — never interpolated into SQL. Row-count limits and a maximum time range for raw-resolution queries prevent a single ambiguous question from pulling unbounded data into a (possibly small, local-model) context window. |
| Context window management | v1 truncates history to the last `MAX_HISTORY_MESSAGES` — no summarization/compaction yet. Local models via Ollama have much smaller context windows than hosted frontier models and no built-in compaction; real summarization is deferred until usage shows it's needed. |
| Observability | Langfuse (LLM tracing: which tool was called, with what arguments, latency, tokens), self-hosted — but as **shared cross-project infrastructure**, not part of this repo. Lives in a new sibling folder `INFRAESTRUCTURA/` (outside `INARI_V06`), its own `docker-compose.yml`, exposing an external Docker network (`infra_net`) other projects' services can join. Out of scope for this spec beyond the client-side integration point. |
| Observability failure mode | Tracing must never be a hard dependency — if Langfuse/`infra_net` is unreachable, chat still works; tracing calls are wrapped and fail silently (logged, not raised). |
| Module structure | New sibling folder `UNS_COPILOT/`, own `docker-compose.yml`, own Postgres — independently deployable, matching the pattern already established by `UNS_SILVER`/`UNS_DASHBOARD`. |

---

## Section 1 — Architecture

New sibling folder to `UNS_MANAGER/`, `UNS_HISTORIAN/`, `UNS_SILVER/`, `UNS_DASHBOARD/`: **`UNS_COPILOT/`**.

Two containers:

- **`uns_copilot_postgres`** — plain Postgres (no TimescaleDB needed — no time-series data lives here, only conversations).
- **`uns_copilot_backend`** — FastAPI (Python 3.12). Talks to three external things:
  1. `uns_silver_postgres` — **read-only** connection, for the four tools.
  2. Ollama — HTTP, OpenAI-compatible endpoint, **not** part of this `docker-compose` (runs on separate hardware — DGX Spark / RTX 5090 — reached via `LLM_BASE_URL`).
  3. `langfuse-web` — HTTP, from the separate `INFRAESTRUCTURA/` stack, for tracing.

Two Docker networks joined by `uns_copilot_backend`:

- **`uns_net`** — existing external network shared with `UNS_MANAGER`/`UNS_HISTORIAN`/`UNS_SILVER`/`UNS_DASHBOARD`. Reaches `uns_silver_postgres:5432`.
- **`infra_net`** — new external network from `INFRAESTRUCTURA/` (Langfuse's own stack, standing up separately). Reaches `langfuse-web`.

```
UNS_SILVER                                  UNS_COPILOT                          INFRAESTRUCTURA
┌────────────────────────┐                  ┌───────────────────────────┐        ┌─────────────────────┐
│ uns_silver_postgres     │◄──read-only──────┤ uns_copilot_backend         ├──────► │ langfuse-web          │
│ (also joins uns_net)    │   (uns_net)      │  (uns_net + infra_net)      │(infra_net)│ (+ worker/ClickHouse/│
└────────────────────────┘                  └─────────────┬─────────────┘        │  Redis/MinIO/Postgres)│
                                                             │ local bridge         └─────────────────────┘
                                              ┌──────────────┴──────────────┐
                                              │ uns_copilot_postgres         │
                                              │ (conversations/users)        │
                                              └──────────────────────────────┘

                                              uns_copilot_backend also calls out to
                                              Ollama (DGX Spark / RTX 5090) over HTTP,
                                              outside any docker-compose network.
```

`INFRAESTRUCTURA/` is a separate initiative (its own repo/folder, its own lifecycle) — this spec only fixes the contract `UNS_COPILOT` needs from it (an OTLP/Langfuse-compatible endpoint reachable on `infra_net`) and treats it as an external prerequisite, the same way `UNS_SILVER`'s spec treats `UNS_HISTORIAN` as a prerequisite it doesn't own.

---

## Section 2 — Data Model (`uns_copilot_postgres`)

```sql
-- Test users (selector, not login) — seeded via init.sql, listed via a simple GET /users
CREATE TABLE users (
    id           BIGSERIAL PRIMARY KEY,
    display_name TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One conversation = one independent chat thread for a user
CREATE TABLE conversations (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id),
    title      TEXT,                     -- first 50 chars of the first user message; editable later
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_conversations_user ON conversations (user_id, updated_at DESC);

-- Every turn, including the internal tool-calling steps (not just what the user sees)
CREATE TABLE messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,        -- 'user' | 'assistant' | 'tool'
    content         TEXT,                 -- NULL when the turn is tool_calls only
    tool_calls      JSONB,                -- what the assistant asked to run (role='assistant')
    tool_call_id    TEXT,                 -- which tool_call this responds to (role='tool')
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_conversation ON messages (conversation_id, created_at);
```

`role`/`tool_calls`/`tool_call_id` mirror the OpenAI-compatible wire format adopted in Section 3, so replaying history into `LLMProvider.chat()` is a direct row→dict mapping with no translation logic. `users` intentionally carries no password/email — a selector, not an auth system.

---

## Section 3 — LLM Provider Abstraction

```python
# app/llm/base.py
class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

class ChatResponse(BaseModel):
    content: str | None       # final text, when there are no further tool calls
    tool_calls: list[ToolCall]
    finish_reason: str        # "tool_calls" | "stop" | "length" | ...

class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...
```

`OllamaAdapter` implements this using the `openai` SDK internally, pointed at Ollama's OpenAI-compatible endpoint (`base_url` from `.env`), translating its raw `tool_calls` into the normalized `ToolCall`/`ChatResponse` shape above. No other part of the application — the agentic loop, the tools, persistence — ever sees a provider-specific payload shape.

Adding OpenAI/OpenRouter later is a near-identical adapter (same wire format). Anthropic-native, if ever needed, is the one adapter that requires real translation (`tool_use`/`tool_result` content blocks instead of `tool_calls`) — deferred until actually required (YAGNI).

---

## Section 4 — Tools

```python
TOOLS = [
    {
        "name": "get_catalog",
        "description": "List cataloged signals/KPIs. Use to discover what signals exist before "
                        "querying readings, or to answer 'what does X measure?'.",
        "parameters": {"topic_filter": "str | None", "signal_type": "'raw' | 'kpi' | None"},
        # -> signal_catalog WHERE effective_until IS NULL AND topic LIKE topic_filter || '%'
    },
    {
        "name": "get_latest_value",
        "description": "Current value of one specific signal. Use for 'what is X right now?'.",
        "parameters": {"topic": "str", "signal_key": "str"},
        # -> silver_latest_value, O(1) lookup
    },
    {
        "name": "query_readings",
        "description": "Historical time series for one signal between two instants, with optional aggregation.",
        "parameters": {
            "topic": "str", "signal_key": "str",
            "from_time": "ISO datetime", "to_time": "ISO datetime",
            "agg": "'raw' | '1m' | '1h'",
        },
        # -> silver_readings / silver_readings_1m / silver_readings_1h depending on agg
    },
    {
        "name": "list_events",
        "description": "Discrete events (alarms, failures) for a topic within a time range.",
        "parameters": {
            "topic_filter": "str | None", "event_key": "str | None",
            "from_time": "ISO datetime", "to_time": "ISO datetime",
        },
        # -> silver_events
    },
]
```

**Guardrails (mandatory, not optional hardening):**
- Every tool validates its parameters with its own Pydantic model before touching the database — tool inputs are never interpolated into SQL; tools are parameterized Python functions, not a channel to free-form SQL.
- `query_readings`/`list_events` enforce a maximum row count (e.g. 1000, `QUERY_ROW_LIMIT`) and reject `agg='raw'` when `to_time - from_time` exceeds a configurable ceiling (e.g. 24h, `RAW_QUERY_MAX_RANGE_HOURS`) — without this, one ambiguously-scoped question could pull weeks of 1Hz data into a small local-model context window.
- All four tools are **read-only** against `uns_silver_postgres`, matching `UNS_SILVER`'s own read-only relationship to `UNS_HISTORIAN`.

---

## Section 5 — Agentic Loop & Error Handling

```python
async def run_turn(conversation_id: int, user_text: str) -> str:
    history = load_history(conversation_id)          # rows -> OpenAI-format dicts, capped to MAX_HISTORY_MESSAGES
    history.append({"role": "user", "content": user_text})
    persist_message(conversation_id, role="user", content=user_text)

    for _ in range(MAX_ITERATIONS):                   # safety bound, e.g. 5
        response = llm_provider.chat(messages=history, tools=TOOL_SCHEMAS)
        persist_message(conversation_id, role="assistant",
                         content=response.content, tool_calls=response.tool_calls)

        if not response.tool_calls:
            return response.content                   # final answer, no further tools

        history.append({"role": "assistant", "tool_calls": response.tool_calls})
        for call in response.tool_calls:
            result = execute_tool(call.name, call.arguments)   # never raises; returns is_error on failure
            history.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
            persist_message(conversation_id, role="tool", tool_call_id=call.id, content=json.dumps(result))

    return "Could not complete the request after several attempts — please rephrase the question."
```

**Error handling:** a tool failure (invalid parameters, DB unreachable, unknown tool name) never raises toward the LLM — it's serialized as `{"error": "..."}` in the `tool_result`, the same "never dropped, always tagged" principle `UNS_SILVER` applies to unknown signals. This lets the model retry with different parameters or admit it can't answer, instead of the whole turn crashing.

**Known limitation, explicitly deferred:** local Ollama models have much smaller context windows than hosted frontier models (typically 8K–32K) and no built-in compaction. `load_history` truncates to the last `MAX_HISTORY_MESSAGES` rather than summarizing — a very long conversation will "forget" its beginning. Real summarization/compaction is deferred until real usage justifies it.

**Observability hook:** instrumented in `run_turn` itself — not inside `OllamaAdapter` — so a trace captures the whole turn (question → tool calls → answer) independent of provider. A Langfuse `@observe()` wraps `run_turn`, with nested spans per `llm_provider.chat()` call and per `execute_tool()` call. Every Langfuse call is wrapped so a failure to reach `langfuse-web` is logged and swallowed, never raised — tracing must never block a chat response.

---

## Section 6 — Deployment

- `UNS_COPILOT/docker-compose.yml` — the two services from Section 1, joining `uns_net` (external, existing) and `infra_net` (external, from `INFRAESTRUCTURA/`).
- `UNS_COPILOT/.env.example`: `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, `SILVER_DATABASE_URL` (read-only role against `uns_silver_postgres`), `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` (Ollama today), `MAX_ITERATIONS` (default `5`), `MAX_HISTORY_MESSAGES`, `RAW_QUERY_MAX_RANGE_HOURS` (default `24`), `QUERY_ROW_LIMIT` (default `1000`), `LANGFUSE_HOST`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`.
- `UNS_COPILOT/scripts/{up,down,restart,logs,status}.sh`, matching the existing pattern.
- `uns_copilot_backend/Dockerfile` — `python:3.12-slim`, FastAPI + uvicorn.
- README notes: `UNS_SILVER` must be running first (`uns_net` external dependency); `INFRAESTRUCTURA/`'s Langfuse stack is an optional-at-runtime external dependency (`infra_net`) — chat works without it, tracing simply won't appear.
- Root `docker-compose.yml` gains a fifth `include:` entry for `UNS_COPILOT/docker-compose.yml`. `INFRAESTRUCTURA/` stays outside this repo entirely — it is not included here.

---

## Section 7 — Testing

- **Unit tests** (no live Postgres/LLM required): Pydantic validation per tool, guardrail enforcement (row limit, raw-range cap); the agentic loop against a fake `LLMProvider` with scripted responses (tool call → final answer; tool error path; `MAX_ITERATIONS` exhausted path).
- **Integration tests**: seed a test `uns_silver_postgres` with known catalog/readings/events; run the real tool functions against it; assert the expected rows. `OllamaAdapter` integration test runs against a live Ollama instance when reachable, skipped otherwise (same convention as other integration tests in this repo that need a live external service).
- **Manual verification**: `docker compose up`, chat via `curl` against a tool-calling-capable Ollama model (e.g. Qwen2.5, Llama 3.1) running on the DGX/5090, confirm the conversation persists in `uns_copilot_postgres` and a trace appears in Langfuse.

---

## Explicitly deferred (future milestones)

- **Chat UI** — a new sub-project (embedded in `UNS_DASHBOARD`/`UNS_MANAGER`, or standalone), its own spec/plan cycle.
- **Report export** (Markdown/PDF/Excel) — depends on the chat UI, its own sub-project.
- **Real authentication** — replaces the user selector; only how `user_id` is derived changes, the `users`/`conversations`/`messages` schema does not.
- **Context compaction/summarization** for conversations exceeding local-model context windows — naive truncation stands until real usage shows it's insufficient.
- **Additional `LLMProvider` adapters** (OpenAI, OpenRouter, Anthropic-native) — the interface in Section 3 is designed for this, but none are built now (YAGNI).
- **`INFRAESTRUCTURA/`'s Langfuse stack itself** — a separate cross-project initiative with its own setup, outside this repo and this spec's scope. This spec fixes only the client-side integration contract (Section 5).
- **Streaming responses** (token-by-token to the eventual chat UI) — v1's `run_turn` returns a complete answer; revisit once a UI consumes it.
