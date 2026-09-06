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
