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
    so callers can feed them straight into an LLM as chat history.

    Truncating purely by count can land the window's first row on a
    `role="tool"` message whose triggering `assistant`+`tool_calls` message
    fell outside it. The OpenAI-compatible wire format requires a tool
    message to immediately follow the assistant message that requested it, so
    a real provider rejects that history with a 400. Any tool message left at
    the head of the window is orphaned by definition (its assistant parent
    always sorts before it), so trim forward past them.
    """
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    messages = list(reversed(result.scalars().all()))

    start = 0
    while start < len(messages) and messages[start].role == "tool":
        start += 1
    return messages[start:]
