from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.history import to_llm_messages
from app.crud import create_message, load_messages
from app.llm.base import LLMProvider
from app.observability import get_tracer
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
