"""Gemini smoke — direct GeminiProvider.complete() health check.

Default: SKIPPED via `@pytest.mark.gemini_live`.

When live: sends a minimal "respond with JSON" prompt to Gemini and
asserts the response is non-empty + parseable. Catches:
- API key / quota issues (provider returns error text)
- Prompt-format drift (Gemini API contract changes)
- JSON-mode regression
- Tool-format conversion bugs (we don't test tools here — kept minimal)

Budget: ~200 input + 200 output tokens per run. The cheapest possible
end-to-end check; downstream paths (vision, spec_generator, critic)
share the same transport so this one test catches the common failure.

To run:
    pytest tests/gemini_smoke/ -m gemini_live -v
    GEMINI_SMOKE_DISABLED=1 pytest tests/gemini_smoke/ -m gemini_live  # killswitch
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = [pytest.mark.gemini_live, pytest.mark.asyncio]


def _kill_switch_active() -> bool:
    return os.environ.get("GEMINI_SMOKE_DISABLED") == "1"


def _api_key_present() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


async def test_gemini_provider_returns_non_empty_response():
    """Minimal smoke: send 'reply with the literal string "ok"' and assert
    the response text is non-empty. This catches auth/quota/transport bugs.
    """
    if _kill_switch_active():
        pytest.skip("GEMINI_SMOKE_DISABLED=1 — live smoke disabled")
    if not _api_key_present():
        pytest.skip("GEMINI_API_KEY not in env — live smoke requires real auth")

    from service.isaac_assist_service.chat.llm_gemini import GeminiProvider

    provider = GeminiProvider(
        api_key=os.environ["GEMINI_API_KEY"],
        model="gemini-2.0-flash",
    )
    messages = [
        {"role": "user", "content": "Reply with exactly the single word: ok"}
    ]
    response = await provider.complete(messages, context={})

    assert response is not None, "GeminiProvider.complete returned None"
    text = getattr(response, "text", None) or ""
    assert text.strip(), f"Empty response from Gemini: {response!r}"
    print(f"[gemini_smoke] response_text={text[:80]!r}")


async def test_gemini_provider_returns_parseable_json():
    """JSON-mode smoke: ask Gemini for a tiny JSON object. Catches:
    - JSON-mode prompt regression (Gemini ignores 'respond with JSON')
    - downstream parser breakage (the project's response handlers expect JSON)

    This is the contract the vision provider + critic agent rely on.
    """
    if _kill_switch_active():
        pytest.skip("GEMINI_SMOKE_DISABLED=1 — live smoke disabled")
    if not _api_key_present():
        pytest.skip("GEMINI_API_KEY not in env")

    from service.isaac_assist_service.chat.llm_gemini import GeminiProvider

    provider = GeminiProvider(
        api_key=os.environ["GEMINI_API_KEY"],
        model="gemini-2.0-flash",
    )
    messages = [
        {
            "role": "user",
            "content": (
                "Return only valid JSON with a single key 'status' set to 'ok'. "
                'Format: {"status": "ok"}'
            ),
        }
    ]
    response = await provider.complete(messages, context={})
    text = (getattr(response, "text", None) or "").strip()

    # Strip code-fence noise if Gemini wraps the JSON
    if text.startswith("```"):
        text = text.strip("`").lstrip("json\n").rstrip("`").strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        pytest.fail(f"Gemini did not return valid JSON: {e}; text={text!r}")

    assert "status" in parsed, f"Expected 'status' key, got {parsed!r}"
    print(f"[gemini_smoke] parsed_json={parsed}")
