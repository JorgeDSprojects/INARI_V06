import pytest

from app.llm.base import ChatResponse, ToolCall
from app.llm.fake import FakeLLMProvider


@pytest.mark.asyncio
async def test_fake_provider_returns_scripted_responses_in_order():
    responses = [
        ChatResponse(
            content=None,
            tool_calls=[ToolCall(id="call_1", name="get_catalog", arguments={})],
            finish_reason="tool_calls",
        ),
        ChatResponse(content="Here is the catalog.", tool_calls=[], finish_reason="stop"),
    ]
    provider = FakeLLMProvider(responses)

    first = await provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert first.finish_reason == "tool_calls"
    assert first.tool_calls[0].name == "get_catalog"

    second = await provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert second.content == "Here is the catalog."

    assert len(provider.calls) == 2
