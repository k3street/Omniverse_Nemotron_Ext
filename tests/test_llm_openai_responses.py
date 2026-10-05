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


# --- transient failures on the Responses path ---
import asyncio

from aiohttp import web


def _serve_and_call(statuses, monkeypatch, served=None):
    """Serve `statuses` in order from a local /v1/responses, then call it."""
    from service.isaac_assist_service.chat import llm_openai_compat as compat

    monkeypatch.setattr(compat, "RETRY_FIRST_BACKOFF_S", 0.01)
    served = [] if served is None else served
    ok_body = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
               "usage": {"input_tokens": 3, "output_tokens": 1}}

    async def handler(request):
        status = statuses[min(len(served), len(statuses) - 1)]
        served.append(status)
        if status == 200:
            return web.json_response(ok_body)
        if status == "quota":
            return web.json_response({"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted",
                                                "message": "You have no credits remaining."}}, status=429)
        return web.json_response({"error": "x"}, status=status, headers={"retry-after": "0"})

    async def main():
        app = web.Application()
        app.router.add_post("/v1/responses", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        provider = compat.OpenAICompatProvider("k", "gpt-6-sol", f"http://127.0.0.1:{port}/v1/chat/completions")
        try:
            return await provider.complete([{"role": "user", "content": "hi"}], {})
        finally:
            await runner.cleanup()

    return asyncio.run(main()), served


def test_a_rate_limit_is_retried_and_the_reply_returned(monkeypatch):
    response, served = _serve_and_call([429, 200], monkeypatch)
    assert response.text == "done" and served == [429, 200]


def test_a_client_error_is_not_retried(monkeypatch):
    with pytest.raises(RuntimeError, match="HTTP 400"):
        _serve_and_call([400, 200], monkeypatch)


def test_persistent_server_errors_give_up_after_the_attempt_limit(monkeypatch):
    from service.isaac_assist_service.chat.llm_openai_compat import RETRY_ATTEMPTS

    served = []
    with pytest.raises(RuntimeError, match="HTTP 503"):
        _serve_and_call([503], monkeypatch, served)
    assert len(served) == RETRY_ATTEMPTS


def test_retry_after_lengthens_but_never_shortens_the_wait():
    from service.isaac_assist_service.chat.llm_openai_compat import RETRY_MAX_WAIT_S, retry_wait_s

    assert retry_wait_s(4.0, None) == 4.0
    assert retry_wait_s(4.0, "1") == 4.0
    assert retry_wait_s(4.0, "12") == 12.0
    assert retry_wait_s(4.0, "900") == RETRY_MAX_WAIT_S
    assert retry_wait_s(4.0, "soon") == 4.0


def test_an_empty_account_is_not_retried(monkeypatch):
    served = []
    with pytest.raises(RuntimeError, match="no credits remaining"):
        _serve_and_call(["quota", 200], monkeypatch, served)
    assert served == ["quota"]
