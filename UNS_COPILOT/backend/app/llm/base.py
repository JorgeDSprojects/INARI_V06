from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class ChatResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall] = []
    finish_reason: str  # "tool_calls" | "stop" | "length" | ...


class LLMProvider(ABC):
    """Chat interface between the agent loop and a model provider.

    Outbound (`ChatResponse`/`ToolCall`) is provider-agnostic by design: no
    adapter may leak its own wire format (OpenAI `tool_calls`, Anthropic
    `tool_use` blocks, ...) back past this boundary — see spec Section 3.

    Inbound is deliberately *not* neutral: `messages` (including the
    `tool_calls`/`tool_call_id` shape built by
    `app.agent.history.to_llm_messages`) and `tools` (`TOOL_SCHEMAS`) are
    OpenAI-shaped, because spec Section 2/3 picked that as the house format
    and the DB schema mirrors it one-to-one. A future Anthropic-native
    adapter is where the translation belongs, not here.
    """

    @abstractmethod
    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...
