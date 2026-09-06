from __future__ import annotations

from app.llm.base import ChatResponse, LLMProvider


class FakeLLMProvider(LLMProvider):
    """Test double: returns one scripted ChatResponse per call, in order.
    Records every `messages` list it was called with for assertions."""

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        self.calls.append(messages)
        return self._responses.pop(0)
