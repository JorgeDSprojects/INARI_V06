from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Message


async def create_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id, role=role, content=content,
        tool_calls=tool_calls, tool_call_id=tool_call_id,
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def load_messages(session: AsyncSession, conversation_id: int, limit: int) -> list[Message]:
    """Most recent `limit` messages for the conversation, returned oldest-first
    so callers can feed them straight into an LLM as chat history."""
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))
