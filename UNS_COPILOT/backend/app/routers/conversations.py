from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.chat import Conversation, Message, User
from app.schemas.chat import ConversationCreate, ConversationDetailRead, ConversationRead

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/", response_model=ConversationRead, status_code=201)
async def create_conversation(body: ConversationCreate, db: AsyncSession = Depends(get_db)):
    # Without this an unknown user_id surfaces as an unhandled FK violation (500).
    if not await db.get(User, body.user_id):
        raise HTTPException(status_code=404, detail="User not found")

    conversation = Conversation(user_id=body.user_id, title=body.title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("/", response_model=list[ConversationRead])
async def list_conversations(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc())
    )
    return result.scalars().all()


@router.get("/{conversation_id}", response_model=ConversationDetailRead)
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = (
        await db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        )
    ).scalars().all()
    return ConversationDetailRead(
        id=conversation.id, user_id=conversation.user_id, title=conversation.title,
        created_at=conversation.created_at, updated_at=conversation.updated_at,
        messages=messages,
    )
