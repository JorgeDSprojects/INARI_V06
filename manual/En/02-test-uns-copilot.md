# Tutorial: test UNS Copilot by hand (chat over UNS_SILVER data)

## 0. What's concretely implemented

`UNS_COPILOT` is a chat backend (FastAPI, no chat UI of its own yet)
that answers natural-language questions about `UNS_SILVER` data by
calling an LLM with **tool calling / function calling**.

What's actually built right now:

- **An LLM decides which tool to call — it never writes SQL.** The
  model (default: `qwen2.5:14b` via Ollama, local — not tied to any one
  provider; there's an `LLMProvider` interface so others can be added
  later) receives the user's question plus a **closed** set of 4 typed
  tools:

  | Tool | What it's for | Table it queries |
  |---|---|---|
  | `get_catalog` | List cataloged signals/KPIs | `signal_catalog` |
  | `get_latest_value` | Current value of one specific signal | `silver_latest_value` |
  | `query_readings` | Historical time series for a signal (raw, 1m, or 1h) | `silver_readings` / `_1m` / `_1h` |
  | `list_events` | Discrete alarms/events in a time range | `silver_events` |

- **All access to `uns_silver_postgres` is read-only**, and every
  parameter is Pydantic-validated before touching the database — nothing
  is ever interpolated into SQL. `query_readings`/`list_events` enforce
  row caps and a maximum time range so an ambiguous question can't pull
  weeks of data.
- **Persisted multi-turn conversations**: every turn (user question,
  tool call, tool result, final answer) is saved to
  `uns_copilot_postgres`, so you can ask follow-up questions ("and 5
  minutes ago?") within the same conversation.
- **Passwordless user selector** (no real authentication yet) — you just
  pick who you are from a list.
- **A tool failure never breaks the conversation** (DB down, invalid
  parameter...) — it's handed back to the model as the tool's result, so
  the model can retry or admit it can't answer.

What's **not** built yet (deliberately out of scope this round): a
graphical chat UI, Markdown/PDF/Excel export, Langfuse tracing enabled
by default (the hook exists but is off until the shared infrastructure
exists), any LLM provider besides Ollama.

## 1. Prerequisites

1. The full stack up from the repo root:
   ```bash
   docker compose up -d
   ```
   At minimum you need `UNS_MANAGER` (EMQX), `UNS_HISTORIAN`, and
   `UNS_SILVER` running before `UNS_COPILOT` — see `CLAUDE.md` for the
   bring-up order.
2. An Ollama instance reachable at `LLM_BASE_URL` (default
   `http://host.docker.internal:11434/v1`) with a tool-calling-capable
   model already pulled (`ollama pull qwen2.5:14b`, or whatever you have
   configured in `UNS_COPILOT/.env`).
3. **At least one real signal flowing** through
   `UNS_MANAGER → UNS_HISTORIAN → UNS_SILVER` — otherwise the tools work
   but there's nothing to query. Check there's data:
   ```bash
   docker exec uns_silver_postgres psql -U silver -d uns_silver \
     -c "SELECT topic, signal_key FROM silver_readings ORDER BY time DESC LIMIT 5;"
   ```
   If this returns no rows, check `manual/En/01-verify-historian-data-pgadmin.md`
   first, and the normalizer's logs (`docker compose logs silver_normalizer`).

## 2. Open the interactive docs (Swagger UI)

Open `http://localhost:8002/docs`. Every step below is done there with
each endpoint's **Try it out** button — no need to use `curl` by hand,
though that works too if you prefer it.

## 3. Check the seeded users

`GET /users/` → **Try it out** → **Execute**.

**Expected result:** a list of 3 users (`Operario Juan`,
`Ingeniera Maria`, `Administrador`), each with a numeric `id`. Note down
any one `id` — you'll need it next.

## 4. Create a conversation

`POST /conversations/` → **Try it out** → body:
```json
{ "user_id": 1 }
```
(use the `id` you noted). **Execute**.

**Expected result:** `201 Created` with an object
`{ "id": ..., "user_id": 1, "title": null, ... }`. Note this
conversation `id` — you'll use it in every step below.

## 5. Ask about the catalog

`POST /conversations/{conversation_id}/messages` → body:
```json
{ "text": "What signals do you know about?" }
```

**Expected result (on a freshly-brought-up stack, nothing manually
cataloged):** a reply saying the catalog is empty. **This is correct,
not a bug** — `get_catalog` reads `signal_catalog`, which only gets
populated when an MQTT message arrives on the `..._descriptive` topic
carrying metadata (unit, thresholds, description). If your simulator
only publishes to `_informative`/`_analytical` (like the one in
`UNS_MANAGER/nodered/flows.seed.json`), there will never be catalog
entries — but readings still get saved to `silver_readings`,
uncataloged. The remaining steps work fine without a catalog.

## 6. Ask for a known signal's current value

Use the real topic/`signal_key` you saw in step 1 (or the sample
simulator's: topic
`GALERNA_ENERGY/SPAIN/GALICIA_COSTA_MORTE/T01/GENERATOR`, signal
`Gen_RPM_Avg`):

```json
{ "text": "What is the current value of Gen_RPM_Avg on topic GALERNA_ENERGY/SPAIN/GALICIA_COSTA_MORTE/T01/GENERATOR?" }
```

**Expected result:** a sentence with a numeric value and a recent
timestamp, e.g.:
> *"The current value of Gen_RPM_Avg on topic ... is 1561.4 RPM at 2026-09-09T20:24:44Z."*

(The exact number will vary — the simulator generates a noisy sine
wave.)

## 7. Ask for history

```json
{ "text": "Show me Gen_RPM_Avg readings from the last 5 minutes" }
```

**Expected result:** a list of several readings with timestamp and
value, summarized in prose (the model won't paste a 150-row table —
it typically shows a handful and describes the trend).

## 8. Ask about alarms/events

```json
{ "text": "Have there been any alarms in the last 5 minutes?" }
```

**Expected result:** depends on whether the simulator crossed a
threshold in that window — it may say there are no active alarms, or
(with the sample simulator, which does produce RPM/temperature spikes
above threshold) a reply detailing which signal, what severity
(`WARNING`/`CRITICAL`), and since when.

## 9. Ask a follow-up (multi-turn memory)

In the **same conversation**, send a short message without repeating
context:
```json
{ "text": "and 10 minutes ago?" }
```

**Expected result:** the model understands which signal/topic you mean
without you repeating it — because the full conversation history is
sent to the model on every turn.

## 10. Inspect the full persisted thread

`GET /conversations/{conversation_id}` → **Execute**.

**Expected result:** a `messages` array with every turn in order:
`role: "user"` (your question) → `role: "assistant"` with `tool_calls`
(which tool it asked to run and with what parameters) →
`role: "tool"` with the raw result (JSON) → `role: "assistant"` with the
final natural-language answer. This is literally what happens "under
the hood" on every question.

## 11. If something doesn't work

- **The reply says there was a technical issue**: check the logs —
  ```bash
  docker compose logs copilot_backend --tail 50
  ```
  Look for `WARNING app.tools.executor: Tool ... failed: ...` lines —
  the tool's actual error is right there.
- **Confirm Ollama is responding:**
  ```bash
  curl http://localhost:11434/api/tags
  ```
- **Confirm there's data to query** (see step 1) — without real
  readings in `UNS_SILVER`, the model has nothing to return no matter
  how well tool-calling itself works.
