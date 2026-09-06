import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm.ollama_adapter import OllamaAdapter


def _fake_openai_response(content, tool_calls, finish_reason):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


@pytest.mark.asyncio
async def test_translates_tool_calls_from_openai_shape():
    adapter = OllamaAdapter(base_url="http://fake:11434/v1", api_key="ollama", model="qwen2.5:14b")
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_catalog", arguments=json.dumps({"topic_filter": "line3"})),
    )
    adapter._client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response(None, [tool_call], "tool_calls")
    )

    response = await adapter.chat(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "get_catalog"
    assert response.tool_calls[0].arguments == {"topic_filter": "line3"}


@pytest.mark.asyncio
async def test_final_answer_has_no_tool_calls():
    adapter = OllamaAdapter(base_url="http://fake:11434/v1", api_key="ollama", model="qwen2.5:14b")
    adapter._client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response("The answer is 42.", None, "stop")
    )

    response = await adapter.chat(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert response.content == "The answer is 42."
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_malformed_tool_call_arguments_become_empty_dict():
    adapter = OllamaAdapter(base_url="http://fake:11434/v1", api_key="ollama", model="qwen2.5:14b")
    tool_call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="get_catalog", arguments="not json"))
    adapter._client.chat.completions.create = AsyncMock(
        return_value=_fake_openai_response(None, [tool_call], "tool_calls")
    )

    response = await adapter.chat(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert response.tool_calls[0].arguments == {}
