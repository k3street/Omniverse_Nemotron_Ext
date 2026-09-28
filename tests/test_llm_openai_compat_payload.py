"""L0 tests for the OpenAI-compatible request body.

Each case here is a 400 from a live API, or worse a silent behaviour change:
a reasoning model sent max_tokens is rejected outright, and a caller that
requires exactly one tool call but is quietly downgraded to tool_choice=auto
gets prose back and raises on the missing call.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _imp():
    # Imported inside the test so the module's aiohttp dependency is only
    # needed by the code that actually performs I/O.
    from service.isaac_assist_service.chat.llm_openai_compat import (
        build_payload,
        is_reasoning_model,
    )
    return build_payload, is_reasoning_model


MESSAGES = [{"role": "user", "content": "hi"}]


def test_gpt6_is_recognised_as_a_reasoning_model():
    _, is_reasoning = _imp()
    # gpt-6 fell outside the original gpt-5/o1/o3 list.
    assert is_reasoning("gpt-6-astra")
    assert is_reasoning("gpt-5.1")
    assert is_reasoning("o4-mini")
    assert not is_reasoning("gpt-4o")


def test_reasoning_models_get_max_completion_tokens_and_no_temperature():
    build_payload, _ = _imp()
    payload = build_payload("gpt-6-astra", MESSAGES, {})
    assert "max_completion_tokens" in payload
    assert "max_tokens" not in payload
    # These models reject a temperature they did not choose.
    assert "temperature" not in payload


def test_older_models_keep_max_tokens_and_temperature():
    build_payload, _ = _imp()
    payload = build_payload("gpt-4o", MESSAGES, {})
    assert payload["max_tokens"] == 4096
    assert payload["temperature"] == 0.2


def test_required_tool_choice_is_passed_through():
    build_payload, _ = _imp()
    payload = build_payload(
        "gpt-6-astra", MESSAGES, {"tools": [{"type": "function"}], "tool_choice": "required"}
    )
    assert payload["tool_choice"] == "required"


def test_tool_choice_defaults_to_auto():
    build_payload, _ = _imp()
    payload = build_payload("gpt-4o", MESSAGES, {"tools": [{"type": "function"}]})
    assert payload["tool_choice"] == "auto"


def test_no_tools_means_no_tool_choice():
    build_payload, _ = _imp()
    assert "tool_choice" not in build_payload("gpt-4o", MESSAGES, {})


def test_kimi_is_pinned_to_temperature_one():
    build_payload, _ = _imp()
    assert build_payload("kimi-k2.6", MESSAGES, {})["temperature"] == 1.0
