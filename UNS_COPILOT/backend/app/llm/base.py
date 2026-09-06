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
    """Provider-agnostic chat interface. No implementation here may leak a
    provider's own wire format (OpenAI-style tool_calls, Anthropic-style
    tool_use blocks, ...) past this boundary — see spec Section 3."""

    @abstractmethod
    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...
