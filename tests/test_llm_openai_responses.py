"""L0 tests for the Responses-API translation.

Reasoning models reject function tools on /v1/chat/completions, so they take
/v1/responses instead — a differently shaped request and a differently shaped
reply. These pin the translation in both directions, because a silent mistake
here means the planner's gates receive no tool call and raise on the absence
rather than on the cause.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _imp():
    from service.isaac_assist_service.chat.llm_openai_compat import (
        build_responses_payload,
        parse_responses_body,
        responses_url,
        to_responses_input,
        to_responses_tools,
    )
    return (
        build_responses_payload, parse_responses_body, responses_url,
        to_responses_input, to_responses_tools,
    )


CHAT_TOOL = [{
    "type": "function",
    "function": {"name": "report", "description": "d", "parameters": {"type": "object"}},
}]


def test_endpoint_is_derived_from_the_configured_chat_url():
    _, _, url, _, _ = _imp()
    assert url("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1/responses"
    # A bare base URL still gets the right path.
    assert url("https://example.test/openai/v1") == "https://example.test/openai/v1/v1/responses"


def test_system_messages_become_instructions():
    _, _, _, to_input, _ = _imp()
    instructions, items = to_input(
        [{"role": "system", "content": "be terse"}, {"role": "user", "content": "hi"}]
    )
    assert instructions == "be terse"
    assert [i["role"] for i in items] == ["user"]


def test_text_and_images_become_responses_parts():
    _, _, _, to_input, _ = _imp()
    _, items = to_input([{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
            {"type": "text", "text": "what is this"},
        ],
    }])
    kinds = [p["type"] for p in items[0]["content"]]
    assert kinds == ["input_image", "input_text"]
    assert items[0]["content"][0]["image_url"] == "data:image/jpeg;base64,AAA"


def test_tools_are_flattened_out_of_the_function_key():
    *_, to_tools = _imp()
    flat = to_tools(CHAT_TOOL)
    # Responses puts name/parameters at the top level, not nested.
    assert flat[0]["name"] == "report"
    assert "function" not in flat[0]


def test_payload_carries_tool_choice_and_output_cap():
    build, *_ = _imp()
    payload = build("gpt-6-astra", [{"role": "user", "content": "hi"}],
                    {"tools": CHAT_TOOL, "tool_choice": "required"})
    assert payload["tool_choice"] == "required"
    assert payload["max_output_tokens"] == 4096
    assert "max_tokens" not in payload and "temperature" not in payload


def test_function_calls_are_normalised_to_the_common_shape():
    _, parse, *_ = _imp()
    text, calls, usage = parse({
        "output": [{
            "type": "function_call", "call_id": "call_1",
            "name": "report", "arguments": '{"visible":true}',
        }],
        "usage": {"input_tokens": 60, "output_tokens": 33},
    })
    # Same shape the Gemini and chat paths emit, so gates dispatch either.
    assert calls == [{
        "id": "call_1", "type": "function",
        "function": {"name": "report", "arguments": '{"visible":true}'},
    }]
    assert usage == (60, 33)
    assert text == ""


def test_output_text_is_gathered():
    _, parse, *_ = _imp()
    text, calls, _ = parse({
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "hello"}]}],
        "usage": {},
    })
    assert text == "hello"
    assert calls is None
