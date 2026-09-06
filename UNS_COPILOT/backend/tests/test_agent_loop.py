import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.llm.base import ChatResponse, ToolCall
from app.llm.fake import FakeLLMProvider
from app.agent.loop import run_turn

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set; requires a live Postgres (docker compose up -d copilot_postgres)"
)


@pytest_asyncio.fixture
async def conversation_id():
    from app.database import AsyncSessionLocal, create_tables
    from app.models.chat import Conversation, User

    await create_tables()
    async with AsyncSessionLocal() as session:
        user = User(display_name=f"pytest agent-loop user {uuid.uuid4()}")
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
