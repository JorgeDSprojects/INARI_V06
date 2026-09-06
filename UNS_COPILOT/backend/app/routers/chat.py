from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import run_turn
from app.config import settings
from app.database import get_db, get_silver_db
from app.llm.base import LLMProvider
from app.llm.ollama_adapter import OllamaAdapter
from app.models.chat import Conversation
from app.schemas.chat import ChatMessageCreate, ChatMessageResponse

router = APIRouter(prefix="/conversations", tags=["chat"])


@lru_cache
def _default_llm_provider() -> LLMProvider:
    return OllamaAdapter(base_url=settings.llm_base_url, api_key=settings.llm_api_key, model=settings.llm_model)


def get_llm_provider() -> LLMProvider:
    """FastAPI dependency -- overridden with a FakeLLMProvider in tests via
    app.dependency_overrides, never patched globally."""
    return _default_llm_provider()


@router.post("/{conversation_id}/messages", response_model=ChatMessageResponse)
async def send_message(
    conversation_id: int,
    body: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    silver_db: AsyncSession = Depends(get_silver_db),
    llm: LLMProvider = Depends(get_llm_provider),
):
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    reply = await run_turn(
        db, silver_db, llm, conversation_id, body.text,
        max_iterations=settings.max_iterations,
        max_history_messages=settings.max_history_messages,
        row_limit=settings.query_row_limit,
        max_raw_range_hours=settings.raw_query_max_range_hours,
    )
    return ChatMessageResponse(conversation_id=conversation_id, reply=reply)
