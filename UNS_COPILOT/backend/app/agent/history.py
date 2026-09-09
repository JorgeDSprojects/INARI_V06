from __future__ import annotations

import json

from app.models.chat import Message


def to_llm_messages(messages: list[Message]) -> list[dict]:
    """Rows -> OpenAI chat-format dicts, the shape every LLMProvider adapter
    expects (see spec Section 2 -- the schema was designed to make this a
    direct mapping, no translation logic)."""
    result = []
    for m in messages:
        if m.role == "tool":
            result.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        elif m.role == "assistant" and m.tool_calls:
            result.append({
                "role": "assistant",
                "content": m.content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                    }
                    for tc in m.tool_calls
                ],
            })
        else:
            result.append({"role": m.role, "content": m.content or ""})
    return result
