import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base
from app.models.chat import User, Conversation, Message

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set; requires a live Postgres (docker compose up -d copilot_postgres)"
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        yield s
        await s.execute(Message.__table__.delete())
        await s.execute(Conversation.__table__.delete())
        await s.execute(User.__table__.delete())
        await s.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_and_message_cascade_from_user(session: AsyncSession):
    user = User(display_name="pytest Operario")
    session.add(user)
    await session.flush()

    conversation = Conversation(user_id=user.id, title="pytest chat")
    session.add(conversation)
    await session.flush()

    message = Message(conversation_id=conversation.id, role="user", content="hello")
    session.add(message)
    await session.commit()

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=None,
        tool_calls=[{"id": "call_1", "name": "get_catalog", "arguments": {}}],
    )
    session.add(assistant_message)
    await session.commit()

    tool_message = Message(
        conversation_id=conversation.id, role="tool", tool_call_id="call_1", content='{"result": []}',
    )
    session.add(tool_message)
    await session.commit()

    result = (
        await session.execute(Message.__table__.select().where(Message.conversation_id == conversation.id))
    ).fetchall()
    assert len(result) == 3
