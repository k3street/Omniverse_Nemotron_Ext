"""L0 unit tests for `_apply_result_cap` — P1 from kcode-spec sec 6.2.

Covers the per-tool result-size cap that bounds tool_result payload
to limit LLM token cost. Justified by Track C 9.4 measurement
(chars/token ratio = 2.25, half the chars/4 default heuristic).
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.l0


def _imp():
    from service.isaac_assist_service.chat.tools.tool_executor import (
        _apply_result_cap,
        _RESULT_CAP_DEFAULT_CHARS,
        _RESULT_CAP_OVERRIDES,
        _RESULT_CAP_EXEMPT,
    )
    return (
        _apply_result_cap,
        _RESULT_CAP_DEFAULT_CHARS,
        _RESULT_CAP_OVERRIDES,
        _RESULT_CAP_EXEMPT,
    )


def test_small_result_unchanged():
    cap, *_ = _imp()
    r = {"type": "data", "output": "ok", "success": True}
    assert cap("anything", r) == r


def test_oversized_output_truncated():
    cap, default, overrides, _ = _imp()
    # Use a non-overridden tool to test default cap
    big_size = max(default, 60_000) + 10_000
    r = {"type": "data", "output": "X" * big_size, "success": True}
    out = cap("non_overridden_tool", r)
    assert "_truncated" in out
    assert out["_truncated"]["tool"] == "non_overridden_tool"
    assert out["_truncated"]["original_chars"] > out["_truncated"]["kept_chars"]
    assert "truncated" in out["output"]


def test_idempotent():
    """Re-capping an already-capped result is a no-op."""
    cap, *_ = _imp()
    big = {"type": "code_patch", "output": "X" * 100_000, "code": "Y", "success": True}
    once = cap("run_usd_script", big)
    twice = cap("run_usd_script", once)
    assert once == twice


def test_per_tool_override_run_usd_script():
    cap, _, overrides, _ = _imp()
    assert "run_usd_script" in overrides
    big = {"type": "code_patch", "output": "X" * 100_000, "success": True}
    out = cap("run_usd_script", big)
    assert out["_truncated"]["cap"] == overrides["run_usd_script"]


def test_per_tool_override_setup_pick_place_drops_code():
    """Step 2 of cap: when output truncation isn't enough, drop the
    `code` field entirely (LLM rarely needs to re-read it)."""
    cap, _, overrides, _ = _imp()
    heavy = {
        "type": "code_patch",
        "output": "Y" * 30_000,  # already substantial
        "code": "Z" * 100_000,    # huge code blob
        "success": True,
    }
    out = cap("setup_pick_place_controller", heavy)
    assert "_truncated" in out
    assert "<dropped" in out.get("code", ""), "code field should be dropped"


def test_capture_viewport_exempt():
    """Image data must NOT be capped — VLM needs intact bytes."""
    cap, _, _, exempt = _imp()
    assert "capture_viewport" in exempt
    img = {"type": "data", "image_b64": "Z" * 500_000}
    out = cap("capture_viewport", img)
    assert "_truncated" not in out
    assert out == img


def test_vision_detect_objects_exempt():
    """Detection coordinates are typically small but precision matters."""
    cap, _, _, exempt = _imp()
    assert "vision_detect_objects" in exempt


def test_env_disable():
    """RESULT_CAP=off bypasses capping entirely."""
    cap, *_ = _imp()
    big = {"type": "code_patch", "output": "X" * 100_000}
    os.environ["RESULT_CAP"] = "off"
    try:
        out = cap("run_usd_script", big)
        assert "_truncated" not in out
        assert out == big
    finally:
        os.environ.pop("RESULT_CAP", None)


def test_env_disable_case_insensitive():
    cap, *_ = _imp()
    big = {"type": "code_patch", "output": "X" * 100_000}
    for val in ("off", "0", "false", "FALSE", "Off"):
        os.environ["RESULT_CAP"] = val
        try:
            out = cap("run_usd_script", big)
            assert "_truncated" not in out, f"value {val!r} should disable"
        finally:
            os.environ.pop("RESULT_CAP", None)


def test_non_dict_result_passthrough():
    """If for some reason result isn't a dict, don't modify it."""
    cap, *_ = _imp()
    assert cap("any", "string") == "string"
    assert cap("any", [1, 2, 3]) == [1, 2, 3]
    assert cap("any", None) is None


def test_already_truncated_marker_preserved():
    """If a result already has _truncated, capping must not double-mark."""
    cap, *_ = _imp()
    pre_capped = {
        "type": "code_patch",
        "output": "small now",
        "_truncated": {"tool": "x", "original_chars": 99999, "kept_chars": 100, "cap": 50000},
    }
    out = cap("run_usd_script", pre_capped)
    assert out == pre_capped


def test_truncation_marker_includes_required_fields():
    cap, *_ = _imp()
    big = {"type": "code_patch", "output": "X" * 100_000}
    out = cap("run_usd_script", big)
    marker = out["_truncated"]
    assert "tool" in marker
    assert "original_chars" in marker
    assert "kept_chars" in marker
    assert "cap" in marker
    assert marker["original_chars"] >= marker["kept_chars"]


def test_short_output_below_500_not_truncated():
    """Output already short doesn't get truncated even if total > cap.
    Typically other fields drove the size."""
    cap, *_ = _imp()
    # output is small but other fields make total big
    r = {
        "type": "code_patch",
        "output": "fine",  # 4 chars
        "code": "X" * 100_000,
        "success": True,
    }
    out = cap("setup_pick_place_controller", r)
    # output should be untouched
    assert out["output"] == "fine"
    # code should be dropped
    assert "<dropped" in out["code"]
