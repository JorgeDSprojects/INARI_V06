from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.history import to_llm_messages
from app.crud import create_message, load_messages
from app.llm.base import LLMProvider
from app.models.chat import Conversation
from app.observability import get_tracer
from app.tools.executor import execute_tool
from app.tools.schemas import TOOL_SCHEMAS

_FALLBACK_REPLY = "Could not complete the request after several attempts — please rephrase the question."


def _system_message() -> dict:
    """Synthesised fresh on every turn and never persisted: the timestamp has
    to be current, and a stale "now" baked into conversation history would be
    worse than none at all."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "role": "system",
        "content": (
            "You are UNS Copilot, an assistant answering questions about an industrial plant's "
            "Unified Namespace data. "
            f"The current time is {now} (UTC, ISO 8601) — resolve relative expressions such as "
            '"today" or "the last hour" against it and always pass explicit from_time/to_time values. '
            "Signals are identified by an ISA-95 topic and a signal_key within that topic; use "
            "get_catalog to discover what is available before querying readings or events. "
            "Base every answer on tool results — never invent a measurement."
        ),
    }


async def _touch_conversation(db_session: AsyncSession, conversation_id: int, user_text: str) -> None:
    """Give the conversation a title from its first user message and mark it
    as active now, so `GET /conversations/?user_id=` sorts by real recency
    (spec Section 2). `onupdate=func.now()` on the model never fires by
    itself — nothing in the chat flow otherwise touches this row."""
    values: dict = {"updated_at": func.now()}
    first_line = user_text[:50].strip()
    if first_line:
        # COALESCE keeps an existing (or user-edited) title untouched.
        values["title"] = func.coalesce(Conversation.title, first_line)
    await db_session.execute(update(Conversation).where(Conversation.id == conversation_id).values(**values))
    await db_session.commit()


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
        await _touch_conversation(db_session, conversation_id, user_text)

        for _ in range(max_iterations):
            history = await load_messages(db_session, conversation_id, limit=max_history_messages)
            messages = [_system_message(), *to_llm_messages(history)]
            response = await llm.chat(messages=messages, tools=TOOL_SCHEMAS)

            await create_message(
                db_session, conversation_id, role="assistant", content=response.content,
                tool_calls=[tc.model_dump() for tc in response.tool_calls] or None,
            )

            if not response.tool_calls:
                return response.content or _FALLBACK_REPLY

            for call in response.tool_calls:
                async with tracer.span("tool_call", name=call.name, arguments=call.arguments):
                    result = await execute_tool(
                        silver_session, call.name, call.arguments,
                        row_limit=row_limit, max_raw_range_hours=max_raw_range_hours,
                    )
                await create_message(
                    db_session, conversation_id, role="tool", tool_call_id=call.id,
                    # Tool results are normalised to JSON-native types at the
                    # tool boundary (app/tools/normalize.py); `default=str` is
                    # a safety net so an unforeseen column type degrades to a
                    # string instead of crashing the whole turn.
                    content=json.dumps(result, ensure_ascii=False, default=str),
                )

        return _FALLBACK_REPLY
