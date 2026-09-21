"""Gemini provider contract for visual observations plus native tool calls."""
from __future__ import annotations

import base64

import pytest


pytestmark = pytest.mark.l0


def _provider():
    from service.isaac_assist_service.chat.llm_gemini import GeminiProvider

    return GeminiProvider(api_key="test-key", model="test-model")


def _tool_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "inspect_world",
            "description": "Inspect the current observed world.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["question"],
            },
        },
    }


def test_request_contains_fresh_image_and_runtime_tools_together():
    image_bytes = b"fresh-rgbd-observation"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    messages = [
        {"role": "system", "content": "Choose tools from current evidence."},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                },
                {"type": "text", "text": "Correct the next movement if needed."},
            ],
        },
    ]

    payload = _provider()._build_request_payload(
        messages, {"tools": [_tool_schema()]}
    )

    assert payload["system_instruction"]["parts"] == [
        {"text": "Choose tools from current evidence."}
    ]
    assert payload["contents"] == [
        {
            "role": "user",
            "parts": [
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": encoded,
                    }
                },
                {"text": "Correct the next movement if needed."},
            ],
        }
    ]
    declarations = payload["tools"][0]["function_declarations"]
    assert [declaration["name"] for declaration in declarations] == [
        "inspect_world"
    ]
    assert "default" not in declarations[0]["parameters"]["properties"]["limit"]


def test_plain_text_messages_remain_compatible():
    assert _provider()._format_messages(
        [{"role": "user", "content": "observe"}]
    ) == [{"role": "user", "parts": [{"text": "observe"}]}]


def test_required_tool_choice_forces_native_function_call_mode():
    payload = _provider()._build_request_payload(
        [{"role": "user", "content": "Choose exactly one tool."}],
        {"tools": [_tool_schema()], "tool_choice": "required"},
    )

    assert payload["toolConfig"] == {
        "functionCallingConfig": {"mode": "ANY"}
    }


def test_tool_choice_remains_automatic_unless_explicitly_required():
    payload = _provider()._build_request_payload(
        [{"role": "user", "content": "Use a tool if helpful."}],
        {"tools": [_tool_schema()]},
    )

    assert "toolConfig" not in payload


def test_const_schema_is_sent_as_single_value_enum():
    cleaned = _provider()._clean_params(
        {
            "type": "object",
            "properties": {
                "observation_id": {"type": "string", "const": "fresh-9"}
            },
        }
    )
    assert cleaned["properties"]["observation_id"] == {
        "type": "string",
        "enum": ["fresh-9"],
    }


def test_runtime_only_schema_keywords_are_not_sent_to_gemini():
    cleaned = _provider()._clean_params(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "count": {"type": "integer", "default": 3},
            },
        }
    )
    assert "additionalProperties" not in cleaned
    assert "default" not in cleaned["properties"]["count"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/frame.png",
        "data:text/plain;base64,Zm9v",
        "data:image/png;utf8,not-base64",
        "data:image/png;base64,%%%",
    ],
)
def test_non_inline_or_malformed_images_are_rejected(url: str):
    with pytest.raises(ValueError):
        _provider()._format_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": url}}
                    ],
                }
            ]
        )


def test_tool_call_and_result_round_trip_remains_compatible():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "inspect_world",
                        "arguments": '{"question":"collision?"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "inspect_world",
            "content": '{"safe":true}',
        },
    ]

    assert _provider()._format_messages(messages) == [
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "name": "inspect_world",
                        "args": {"question": "collision?"},
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "inspect_world",
                        "response": {"safe": True},
                    }
                }
            ],
        },
    ]
