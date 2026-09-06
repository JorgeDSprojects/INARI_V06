from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.llm.base import ChatResponse, LLMProvider, ToolCall

logger = logging.getLogger(__name__)


class OllamaAdapter(LLMProvider):
    """Talks to Ollama's OpenAI-compatible endpoint via the `openai` SDK.
    Ollama, OpenAI, and OpenRouter all speak this wire format, so this
    adapter is reusable almost as-is for those providers later — see spec
    Section 3. A true Anthropic-native adapter is the one that would need
    real translation (tool_use/tool_result blocks instead of tool_calls),
    and is deferred until actually needed."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools or None,
        )
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        for raw_call in (message.tool_calls or []):
            try:
                arguments = json.loads(raw_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                # Never crash on a malformed tool call — the downstream
                # Pydantic parameter validation (Task 8) will reject an
                # empty/wrong argument set and report it back to the model
                # as a tool error instead of blowing up the whole turn.
                logger.warning("Malformed tool call arguments from model: %r", raw_call.function.arguments)
                arguments = {}
            tool_calls.append(ToolCall(id=raw_call.id, name=raw_call.function.name, arguments=arguments))

        return ChatResponse(content=message.content, tool_calls=tool_calls, finish_reason=choice.finish_reason)
