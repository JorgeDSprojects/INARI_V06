from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(_Base):
    id: int
    display_name: str
    created_at: datetime


class ConversationCreate(BaseModel):
    user_id: int
    title: str | None = None


class ConversationRead(_Base):
    id: int
    user_id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageRead(_Base):
    id: int
    conversation_id: int
    role: str
    content: str | None
    tool_calls: list[dict] | None
    tool_call_id: str | None
    created_at: datetime


class ConversationDetailRead(ConversationRead):
    messages: list[MessageRead]
