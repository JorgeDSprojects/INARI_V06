import os
import uuid

import pytest
import pytest_asyncio

from app.crud import create_message, load_messages

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
        user = User(display_name=f"pytest crud user {uuid.uuid4()}")
        session.add(user)
        await session.flush()
        conversation = Conversation(user_id=user.id)
        session.add(conversation)
        await session.commit()
        yield conversation.id


@pytest.mark.asyncio
async def test_load_messages_never_starts_with_an_orphaned_tool_message(conversation_id):
    """A count-based truncation window can open on a `tool` message whose
    triggering assistant message was cut away. The OpenAI-compatible wire
    format requires a tool message to follow its assistant parent, so a real
    provider rejects that history with a 400."""
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # 8 messages: two complete user -> assistant(tool_calls) -> tool ->
        # assistant cycles. A limit of 6 cuts right between #2 and #3, so the
        # window would otherwise open on the first `tool` message.
        for turn in (1, 2):
            await create_message(session, conversation_id, role="user", content=f"question {turn}")
            await create_message(
                session, conversation_id, role="assistant", content=None,
                tool_calls=[{"id": f"call_{turn}", "name": "get_latest_value", "arguments": {}}],
            )
            await create_message(
                session, conversation_id, role="tool", tool_call_id=f"call_{turn}", content='{"result": 1}',
            )
            await create_message(session, conversation_id, role="assistant", content=f"answer {turn}")

        untrimmed = await load_messages(session, conversation_id, limit=100)
        assert [m.role for m in untrimmed] == [
            "user", "assistant", "tool", "assistant", "user", "assistant", "tool", "assistant",
        ]

        trimmed = await load_messages(session, conversation_id, limit=6)

    assert trimmed[0].role != "tool"
    # The orphaned tool message (and only it) was dropped from the window.
    assert [m.role for m in trimmed] == ["assistant", "user", "assistant", "tool", "assistant"]


@pytest.mark.asyncio
async def test_load_messages_keeps_a_tool_message_that_has_its_assistant_parent(conversation_id):
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await create_message(session, conversation_id, role="user", content="question")
        await create_message(
            session, conversation_id, role="assistant", content=None,
            tool_calls=[{"id": "call_1", "name": "get_latest_value", "arguments": {}}],
        )
        await create_message(
            session, conversation_id, role="tool", tool_call_id="call_1", content='{"result": 1}',
        )
        await create_message(session, conversation_id, role="assistant", content="answer")

        messages = await load_messages(session, conversation_id, limit=3)

    assert [m.role for m in messages] == ["assistant", "tool", "assistant"]
