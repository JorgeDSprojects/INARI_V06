# Tutorial: how function calling is actually programmed — a real case study with `UNS_COPILOT`

This manual isn't "how to test UNS_COPILOT" (that's
`02-test-uns-copilot.md`) — it's **how it's programmed internally**, read
as a class: every *function calling / tool calling* concept explained and
then pointed at the exact file and lines where it lives in this repo.

Every piece of code quoted lives under `UNS_COPILOT/backend/app/`.

---

## 1. The concept, before any code

**Function calling** (also called *tool calling*, *tool use*) is a
mechanism by which an LLM, instead of only ever answering with free text,
can ask **your program** to run one specific function with specific
arguments, and then keep reasoning with the result.

The LLM **never executes code**. It never touches a database. It only
does three things:

1. Receives the conversation plus a list of "available functions"
   described as data (JSON Schema), not as code.
2. Decides (this is a statistical prediction, not magic) whether it needs
   to call one, and with what arguments — also as JSON.
3. Once your program hands the result back, it keeps reasoning with it and
   drafts the final answer.

Everything else — validating the arguments, actually running the
function, handling errors, deciding when to stop — **you program**. That's
what this tutorial is about: not "how to use an LLM", but how to build the
scaffolding around it that makes it safe and reliable.

### Why this, and not "let the LLM write SQL"

The obvious alternative — asking the model to generate SQL directly — is
exactly what this design explicitly avoids (see
`docs/superpowers/specs/2026-09-06-uns-copilot-design.md`, "Context"
section): it's imprecise (a small local model gets the column, the join,
the syntax wrong) and it's an injection surface — nothing guarantees the
generated SQL is read-only or bounded. With function calling, **the LLM
picks from a closed menu of functions you wrote**; it never generates the
query, only the parameters of a query that already exists and that you
control.

### The three mental pieces

```
┌─────────────┐  1. messages + tool "menu"        ┌─────────────┐
│ Your backend│ ────────────────────────────────► │     LLM     │
│ (the loop)  │                                    │             │
│             │ ◄──────────────────────────────── │             │
└──────┬──────┘  2. "I want to call X(args)"        └─────────────┘
       │            or "here's the final answer"
       │ 3. you validate and actually run X(args)
       ▼
┌─────────────┐
│  Executor   │──► validates parameters (Pydantic) ──► parameterized SQL ──► DB
│ (dispatcher)│◄── never raises, always returns something ─────────────────┘
└─────────────┘
       │
       └── the result flows back into step 1, as one more message, and the
           loop repeats until the LLM stops asking for tools
```

Three pieces you'll see again and again in any function-calling
implementation, whether it's Ollama, OpenAI, or Anthropic:

- **The contract** (`TOOL_SCHEMAS` in `tools/schemas.py`): which functions
  exist, described in a format the model understands.
- **The loop** (`run_turn` in `agent/loop.py`): the back-and-forth between
  your code and the model, iteration by iteration.
- **The executor** (`execute_tool` in `tools/executor.py`): the bridge
  between "the model asked for this" and "this actually ran, safely".

---

## 2. The contract: how you describe a function to the model

The model never sees your Python code. It sees exactly this —
`tools/schemas.py:1-26` (trimmed to one tool):

```python
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
    ...
]
```

This is **JSON Schema** — the same format OpenAPI/Swagger uses. It's the
de-facto standard adopted by OpenAI, and Ollama and most providers follow
it because they expose an "OpenAI-compatible" endpoint.

Points to internalize:

- **`name`** is literally the identifier the model later returns to say
  "I want to call this one". It has to match exactly how you register it
  in your executor (section 5).
- **`description`** is not a decorative comment — it's the only
  information the model has to decide *when* to use this function and not
  another one. This is where you actually "program" the agent's behavior:
  a vague description ("fetch data") produces misdirected calls; a
  description like the one above, which explicitly says *"Use this to
  discover what signals exist before querying"*, nudges the model toward
  calling `get_catalog` before `query_readings` when it doesn't know the
  exact `signal_key`. This is *prompt engineering applied to tools*, not
  to the conversation.
- **`parameters`** is an object-shaped JSON Schema: each property with its
  type, description and (where relevant) `enum`; `required` lists which
  are mandatory. Notice there's **no real validation here** — it's only
  what the model is taught. Real validation lives in section 5, and it's
  essential: nothing stops the model from sending `topic_filter: 123` (an
  integer) even though the schema says `"string"`.

All four tools (`get_catalog`, `get_latest_value`, `query_readings`,
`list_events`) live in `tools/schemas.py`; each maps 1:1 to a
`UNS_SILVER` table (see
`docs/superpowers/specs/2026-09-06-uns-copilot-design.md`, Section 4).

---

## 3. What the model responds with: `tool_calls`

When it decides to use a function, the model doesn't execute anything —
it returns a data structure asking you to. Normalized in this project as
(`llm/base.py:8-17`):

```python
class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

class ChatResponse(BaseModel):
    content: str | None       # final text, when there are no further tool calls
    tool_calls: list[ToolCall] = []
    finish_reason: str        # "tool_calls" | "stop" | "length" | ...
```

Three fields you need to understand well, because the rest of the loop
revolves around them:

- **`id`**: an identifier **the model itself** generates for that
  specific call. You don't invent it. You must send it back verbatim
  (as `tool_call_id`) when you hand back the result, because if the model
  asked for several tools in the same turn, that's how it knows which
  result belongs to which request.
- **`name`** / **`arguments`**: which function and with what parameters —
  text and JSON, with no guarantee either is correct (hence section 5).
- **`content`**: usually `None` when there are `tool_calls` (the model
  "stays quiet" while asking for data) and has text once it no longer
  needs more tools — that's your signal to end the loop (section 4).

`OllamaAdapter.chat()` (`llm/ollama_adapter.py:25-47`) is what builds this
normalized object out of the raw response from the OpenAI SDK:

```python
tool_calls = []
for raw_call in (message.tool_calls or []):
    try:
        arguments = json.loads(raw_call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        # Never crash on a malformed tool call -- the downstream Pydantic
        # validation (section 5) will reject it and report it back to the
        # model as a tool error, not as a crashed turn.
        arguments = {}
    tool_calls.append(ToolCall(id=raw_call.id, name=raw_call.function.name, arguments=arguments))
```

Notice the detail: `arguments` arrives from the model as a **JSON text
string**, not a dict — it has to be parsed, and that parsing can fail (the
model can "hallucinate" invalid JSON). The adapter never lets that failure
take down the turn; it degrades to `{}` and lets the validation layer
(section 5) reject it in a controlled way.

---

## 4. The agentic loop: the heart of it all

This is the piece that actually "programs" agentic behavior. Simplified
version of `agent/loop.py:53-98` (`run_turn`):

```python
for _ in range(max_iterations):                       # ← safety bound
    history = await load_messages(db_session, conversation_id, limit=max_history_messages)
    messages = [_system_message(), *to_llm_messages(history)]
    response = await llm.chat(messages=messages, tools=TOOL_SCHEMAS)

    await create_message(db_session, conversation_id, role="assistant",
                          content=response.content, tool_calls=[...])

    if not response.tool_calls:
        return response.content or _FALLBACK_REPLY    # ← exit condition

    for call in response.tool_calls:
        result = await execute_tool(silver_session, call.name, call.arguments, ...)
        await create_message(db_session, conversation_id, role="tool",
                              tool_call_id=call.id, content=json.dumps(result))

return _FALLBACK_REPLY                                  # ← ran out of attempts
```

Things to learn from this loop, not just copy:

1. **It's a `for`, not a `while True`.** `max_iterations` (default 5,
   `config.py:14`) is a mandatory safety bound: without it, a model that
   gets "stuck" asking for the same tool over and over (say, because it
   can't interpret the result) would consume resources indefinitely. The
   test `test_max_iterations_exhausted_returns_graceful_fallback`
   (`tests/test_agent_loop.py:87-104`) verifies exactly this case: the
   loop ends with a readable message, not an error or a hang.
2. **The exit condition is "there are no `tool_calls`".** It isn't "the
   model said stop" or some special field — simply, if
   `response.tool_calls` comes back empty, `content` is assumed to be the
   final answer. This is universal across every function-calling
   implementation: the model itself decides, turn by turn, whether it
   needs more tools or can already answer.
3. **The history is re-read from the database on every iteration**
   (`load_messages`), not accumulated in a local in-memory variable. This
   is a deliberate choice in this project: every step of the loop
   (including every tool call) is persisted immediately
   (`create_message`), so if the process crashes mid-sequence of tool
   calls, nothing is lost.
4. **The system message is regenerated on every turn**
   (`_system_message()`, `agent/loop.py:20-36`) and **never persisted**.
   It carries the current UTC time, and saving a stale time into history
   would be worse than none — the code's own comment says so: *"a stale
   'now' baked into conversation history would be worse than none at
   all"*. General lesson: any data that changes over time (date/time, who
   you are, what permissions you have) belongs in a fresh *system prompt*,
   never in saved history.
5. **Every tool result is sent back as a `role: "tool"` message**
   correlated by its `tool_call_id` (section 3). That's how the model, on
   the next loop iteration, sees the whole conversation: its own request +
   the result + can reason about it.

`agent/history.py` (`to_llm_messages`) is the piece that converts rows
from the `messages` table back into the dict format `LLMProvider.chat()`
expects — a direct row→dict translation because the table schema
(`role`/`tool_calls`/`tool_call_id`, see `models/chat.py:32-41`) was
deliberately designed to mirror the OpenAI format (see Section 3 of the
design spec).

---

## 5. Actually running the function: validate → dispatch → never raise

This is where the safety loop closes. `tools/executor.py:23-63`
(`execute_tool`):

```python
_PARAM_MODELS = {
    "get_catalog": GetCatalogParams,
    "get_latest_value": GetLatestValueParams,
    "query_readings": QueryReadingsParams,
    "list_events": ListEventsParams,
}

async def execute_tool(session, name, arguments, *, row_limit, max_raw_range_hours) -> dict:
    param_model = _PARAM_MODELS.get(name)
    if param_model is None:
        return {"error": f"Unknown tool: {name!r}"}          # ① unknown name

    try:
        params = param_model(**arguments)
    except (ValidationError, TypeError) as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}   # ② invalid arguments

    try:
        if name == "get_catalog":
            result = await get_catalog(session, params, row_limit=row_limit)
        ...
    except Exception as exc:                                  # ③ real failure (DB down, etc.)
        if session is not None:
            await session.rollback()
        return {"error": str(exc)}

    return {"result": result}
```

The golden rule of this whole file, and probably the single most
important lesson of function calling in production: **`execute_tool`
never raises an exception back toward the loop**. It always returns a
dict — `{"result": ...}` or `{"error": ...}` — because that dict gets
serialized as-is and sent to the model as the result of its call
(`json.dumps(result)` in `agent/loop.py:95`). If you let an exception
propagate, you'd take down the whole turn (and probably the HTTP endpoint)
over, say, a typo'd parameter. Instead, the model *sees* the error as text
and can react: ask for the tool again with different parameters, or admit
it can't answer.

Three layers of why you never trust what the model sends blindly:

1. **① Unknown name** — the model could "hallucinate" a function name
   that doesn't exist. The `_PARAM_MODELS` dict acts as a *dispatch
   table*: if the name isn't there, a controlled error, not a `KeyError`.
2. **② Invalid arguments** — this is the step that does the actual heavy
   lifting, and it uses **Pydantic**, not manual checks.
   `tools/params.py` defines one model per tool:

   ```python
   class GetLatestValueParams(BaseModel):
       topic: str
       signal_key: str

   class QueryReadingsParams(_TimeRangeMixin):   # from_time, to_time + validation
       topic: str
       signal_key: str
       agg: Literal["raw", "1m", "1h"] = "raw"
   ```

   `param_model(**arguments)` tries to build the model with whatever the
   model sent; if a required field is missing, a type doesn't fit, or a
   custom validator rejects the value, Pydantic raises `ValidationError`
   — caught and turned into `{"error": ...}`. An example of a custom
   validator, `tools/params.py:17-24` (`_TimeRangeMixin`):

   ```python
   @model_validator(mode="after")
   def _check_time_order(self):
       if self.to_time <= self.from_time:
           raise ValueError(f"to_time ({self.to_time}) must be strictly after from_time ({self.from_time})")
       return self
   ```

   Without this, a `to_time` earlier than `from_time` wouldn't break
   anything type-wise — the SQL `BETWEEN` would just return zero rows, and
   the model would (incorrectly) conclude there's no data, instead of
   that the question was malformed. **Validating isn't just "don't
   crash": it's making sure the model gets the right error so it can
   correct itself.**

   There's a second guardrail, deliberately kept *outside* the Pydantic
   model because it depends on a configuration value
   (`max_raw_range_hours`) that isn't part of the tool's schema:
   `check_raw_range` (`tools/params.py:48-60`) rejects an `agg='raw'`
   query over too wide a time window — without it, an ambiguous question
   could try to pull weeks of 1Hz data and blow past the (already small)
   context window of a local model.

3. **③ A real execution failure** (the database doesn't respond, a table
   doesn't exist...) — the generic `except Exception` in `executor.py` is
   intentional (`# noqa: BLE001 - deliberate catch-all` comment), not an
   oversight: at this exact point, "something went wrong running the
   tool" is precisely what you want to catch, whatever it is. Also
   notice the `rollback()`: if a tool fails mid-transaction on a shared
   session, the session is left "poisoned" and any later tool call in the
   same turn would fail too without a rollback — an easy detail to miss,
   and exactly why the test `test_a_failing_tool_rolls_the_session_back`
   (`tests/test_tool_executor.py:64-76`) exists.

### A problem that isn't obvious until it bites you: serializing the result

`tools/normalize.py` solves something that trips up almost everyone the
first time they wire an LLM to a real database: `json.dumps` doesn't know
how to serialize a `datetime` or a `Decimal`, and both are exactly what
Postgres returns for `TIMESTAMPTZ` and `NUMERIC` columns.

```python
def to_json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    ...
```

The file's own comment explains a subtle decision: converting `Decimal`
to `float` rather than to `str` (which is what `json.dumps(...,
default=str)` would do by default) because a stringified number
(`"22.0"`) reaches the model as something it has to *interpret*, while a
real JSON number (`22.0`) is something it can *reason arithmetically*
about (compare, average, etc.) directly.

---

## 6. Why there's an abstraction layer over the provider

`llm/base.py` defines an `LLMProvider` interface with a single abstract
method:

```python
class LLMProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...
```

Right now there's only one real implementation, `OllamaAdapter`
(section 3), but the rest of the system — the loop, the tools, the
persistence — never sees Ollama's/OpenAI's specific wire format; it only
ever sees `ChatResponse`/`ToolCall`. The file's own comment says it
plainly: *"no adapter may leak its own wire format ... back past this
boundary"*. This matters for a very concrete reason cited in the design
spec: **Anthropic's tool-calling format is structurally different**
(`tool_use`/`tool_result` content blocks instead of a separate
`tool_calls` array) — so the day a native Anthropic adapter gets added,
that translation lives *only* inside that new adapter, and neither the
loop nor the tools need to change. This is the *Adapter* pattern applied
specifically so the rest of the program never couples to one provider's
particular way of talking.

The same mechanism is also what makes the loop testable without a real
Ollama running: `llm/fake.py` (`FakeLLMProvider`) implements the same
interface by returning a hand-scripted list of responses — that's how
`tests/test_agent_loop.py` tests "one tool call then a final answer",
"iterations run out", etc., with no network and no real model. In FastAPI
this is wired via *dependency injection*
(`routers/chat.py:24-27`, `get_llm_provider`), swapped in tests with
`app.dependency_overrides` instead of patching modules globally.

---

## 7. The full cycle, with a concrete example

User question: *"What is the current value of Gen_RPM_Avg?"*

```
User                    Backend (run_turn)                    LLM (Ollama)
   │                          │                                    │
   │ "what is the current     │                                    │
   │  value of Gen_RPM_Avg?"  │                                    │
   ├─────────────────────────►│ saves role=user                   │
   │                          │                                    │
   │                          │ 1) sends [system, ...history,      │
   │                          │    user] + TOOL_SCHEMAS ──────────►│
   │                          │                                    │ decides: I need
   │                          │                                    │ get_latest_value
   │                          │◄─── tool_calls=[{id:"c1",          │
   │                          │      name:"get_latest_value",      │
   │                          │      arguments:{topic:.., ...}}]   │
   │                          │ saves role=assistant, tool_calls   │
   │                          │                                    │
   │                          │ 2) execute_tool("get_latest_value",│
   │                          │    args) → validates (Pydantic) →  │
   │                          │    parameterized SQL → Postgres →  │
   │                          │    {"result": {"value_numeric":    │
   │                          │       1561.4, "time": "..."}}      │
   │                          │ saves role=tool, tool_call_id=c1   │
   │                          │                                    │
   │                          │ 3) sends [system, ...history       │
   │                          │    (includes the tool call and its │
   │                          │    result), user] ────────────────►│
   │                          │                                    │ now has the value,
   │                          │                                    │ drafts the answer
   │                          │◄─── content="The current value is  │
   │                          │      1561.4 RPM...", tool_calls=[] │
   │                          │ saves role=assistant               │
   │◄─────────────────────────┤ tool_calls empty → loop ends,      │
   │  "The current value is   │ returns response.content           │
   │   1561.4 RPM..."         │                                    │
```

Notice there are **two** calls to the model for a single user question:
one that decides "I need the tool" and another, now with the result in
history, that drafts the final answer. This is exactly what
`test_one_tool_call_then_final_answer`
(`tests/test_agent_loop.py:63-84`) verifies, including the assertion that
the second call to the model includes a `role: "tool"` message.

---

## 8. Guided exercise: add a fifth tool yourself

The best way to make this stick is to build it. Goal: a
`get_signal_stats(topic, signal_key, from_time, to_time)` tool that
returns `{min, max, avg, count}` for a signal over a range, using
`silver_readings` directly with SQL aggregate functions (it doesn't reuse
the `_1m`/`_1h` tables, so you get practice writing a new query).

Follow the exact same path the other four tools already follow — it's 4
files, in this order:

1. **`tools/params.py`** — the validation model:
   ```python
   class GetSignalStatsParams(_TimeRangeMixin):
       topic: str
       signal_key: str
   ```
   (reuse `_TimeRangeMixin` since you also need `from_time < to_time` —
   don't reimplement it).

2. **A new function in `tools/readings.py`** (or a new file) — the actual
   execution, parameterized SQL (never f-strings with user-supplied
   values inside the `WHERE`):
   ```python
   async def get_signal_stats(session, params: GetSignalStatsParams) -> dict | None:
       result = await session.execute(
           text(
               "SELECT MIN(value_numeric) AS min_value, MAX(value_numeric) AS max_value, "
               "AVG(value_numeric) AS avg_value, COUNT(*) AS sample_count "
               "FROM silver_readings WHERE topic = :topic AND signal_key = :signal_key "
               "AND time BETWEEN :from_time AND :to_time"
           ),
           {"topic": params.topic, "signal_key": params.signal_key,
            "from_time": params.from_time, "to_time": params.to_time},
       )
       row = result.mappings().first()
       return row_to_json_safe(row) if row else None   # don't forget normalize.py
   ```

3. **`tools/executor.py`** — register it in both dispatch tables: add
   `"get_signal_stats": GetSignalStatsParams` to `_PARAM_MODELS` and a
   branch `elif name == "get_signal_stats": result = await
   get_signal_stats(session, params)`.

4. **`tools/schemas.py`** — the contract the model sees, with a clear
   `description` of when to use it (for example: *"Use for questions like
   'what was the average/max/min of X over a period'; use query_readings
   instead if the user wants individual data points"* — the comparison
   with another tool helps the model choose correctly).

And to verify it works **without depending on a real LLM**, write a test
in the style of `tests/test_tool_executor.py`: patch
`app.tools.executor.get_signal_stats` with an `AsyncMock` and check that
`execute_tool` dispatches to it with the right parameters — exactly the
pattern used by
`test_valid_call_dispatches_to_the_right_tool_function`
(`tests/test_tool_executor.py:36-50`). If you have Ollama running, you can
also try it for real using the manual flow in `02-test-uns-copilot.md`,
asking something like *"what was the max of Gen_RPM_Avg in the last
hour?"*.

---

## 9. Common mistakes when programming this (checklist)

Based on the decisions this project made explicitly to avoid them:

- ☐ **Don't lose the `tool_call_id`.** If the result you send back to the
  model doesn't carry the same `id` it generated in its request, the
  model can't correlate (especially if it asked for several tools at
  once) and the conversation gets corrupted.
- ☐ **Don't let a tool's exception take down the turn.** Catch it, turn
  it into `{"error": ...}`, hand it back to the model as the result. It's
  a function call, not a system call that's allowed to fail "upward".
- ☐ **Don't trust the types the JSON Schema promises.** The schema is
  documentation for the model, not a parser. Always validate with
  something real (Pydantic here) before touching a database.
- ☐ **Don't leave the loop unbounded.** A `for _ in range(MAX_ITERATIONS)`,
  not a `while True` — a model can keep asking for tools without ever
  reaching a final answer.
- ☐ **Don't serialize "raw" database types.** `Decimal` and `datetime`
  break `json.dumps` — normalize them to `float`/ISO-8601 *before*
  building the result, not as a patch at serialization time.
- ☐ **Don't leave a shared DB session un-rolled-back after a failure.**
  If several tool calls share a session/transaction in the same turn, an
  un-rolled-back failure poisons the ones that follow.
- ☐ **Don't bake time-varying data (date/time, identity) into persisted
  history** — recompute it every turn, in the system message.

---

## 10. Glossary

| Term | What it means here |
|---|---|
| **Tool / function** | A function your backend exposes to the model, described as JSON Schema. In this repo: `get_catalog`, `get_latest_value`, `query_readings`, `list_events`. |
| **Tool call** | The model's request to run a specific tool with specific arguments (`ToolCall` in `llm/base.py`). |
| **Tool result / tool message** | The `role: "tool"` message you send back to the model with the result (or error) of running the tool, correlated by `tool_call_id`. |
| **System message / system prompt** | The first message in the conversation, which fixes the assistant's role and any context that changes over time (here, the current time). It isn't a tool, but it shapes the model's behavior just as strongly. |
| **Agentic loop** | The `for` loop that alternates "ask the model" ↔ "run whatever it asked for" until it stops asking (`run_turn`). |
| **`finish_reason`** | Why the model stopped generating in that response: `"tool_calls"` (it wants to run something), `"stop"` (final answer), `"length"` (cut off by token limit), etc. |
| **Guardrail** | A limit you impose that the model can't bypass (row cap, max time range, read-only) — the difference between "the model decides what to ask" and "the model decides what it's allowed to do". |
| **Dispatch table** | The name→function/model `dict` (`_PARAM_MODELS` in `executor.py`) that turns a text `name` into the real Python function to run, without a giant `if/elif` chain or `eval`. |
| **Adapter (pattern)** | The class that translates one provider's specific format (here, `OllamaAdapter`) into the neutral format the rest of your program uses (`ChatResponse`/`ToolCall`). |

---

## 11. Where to go next

- The full design, with the decisions and their reasoning:
  `UNS_COPILOT/docs/superpowers/specs/2026-09-06-uns-copilot-design.md`.
- Trying all of this live, with a real Ollama: `02-test-uns-copilot.md`.
- The tests are the best executable documentation of the edge cases:
  `UNS_COPILOT/backend/tests/test_agent_loop.py` (the loop) and
  `test_tool_executor.py` (validation/dispatch) — read them as a spec,
  not just a regression suite.
- Ideas for further practicing *tool use* on top of this same base (RAG,
  structured output, write-capable agents with human confirmation, MCP):
  `manual/ideas.md`.
