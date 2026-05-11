"""L0 unit tests for orchestrator's payload compression helpers.

Covers _compress_tool_result_content and _compress_old_tool_results to
guarantee the compression mechanism (commit ec07469) is correct under
edge cases that production traffic may exercise:

- already-compressed content (idempotency)
- non-JSON tool_result content
- non-dict JSON content
- exactly N tool results (boundary keep_last_n)
- empty messages list
- mixed message roles preserved
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.l0


def _import_helpers():
    """Import the helpers; orchestrator has heavy deps but the helpers
    are at module top before the heavy imports."""
    from service.isaac_assist_service.chat.orchestrator import (
        _compress_tool_result_content,
        _compress_old_tool_results,
        _measure_messages_bytes,
    )
    return _compress_tool_result_content, _compress_old_tool_results, _measure_messages_bytes


# ── _compress_tool_result_content ─────────────────────────────────────────


def test_compress_drops_verbose_code_field():
    fn, _, _ = _import_helpers()
    content = json.dumps({
        "type": "tool_result",
        "success": True,
        "code": "import omni.usd\n" + "x = 1\n" * 200,  # verbose
        "output": "ok",
    })
    out = fn(content)
    parsed = json.loads(out)
    assert "code" not in parsed
    assert parsed["success"] is True
    assert parsed["output"] == "ok"


def test_compress_truncates_long_output_with_marker():
    fn, _, _ = _import_helpers()
    long = "Z" * 5000
    content = json.dumps({"output": long, "success": True})
    out = fn(content, max_output_chars=100)
    parsed = json.loads(out)
    assert "truncated" in parsed["output"]
    assert parsed["output"].startswith("Z" * 100)
    assert "5000" in parsed["output"], "truncation marker should mention original length"


def test_compress_keeps_short_output_intact():
    fn, _, _ = _import_helpers()
    content = json.dumps({"output": "fine", "success": True, "type": "x"})
    out = fn(content)
    parsed = json.loads(out)
    assert parsed["output"] == "fine"
    assert parsed["type"] == "x"


def test_compress_preserves_diagnostic_fields():
    fn, _, _ = _import_helpers()
    content = json.dumps({
        "type": "tool_result", "success": False, "executed": True,
        "queued": False, "error": "Permission denied", "description": "Build cube",
        "output": "tried", "code": "verbose code blob",
    })
    out = fn(content)
    parsed = json.loads(out)
    for k in ("type", "success", "executed", "queued", "error", "description"):
        assert k in parsed
    assert "code" not in parsed


def test_compress_non_json_truncates_raw_with_marker():
    fn, _, _ = _import_helpers()
    raw = "some non-JSON text " * 200
    out = fn(raw, max_output_chars=50)
    assert out.startswith("some non-JSON text")
    assert "truncated" in out


def test_compress_non_json_short_unchanged():
    fn, _, _ = _import_helpers()
    out = fn("error: not authorized", max_output_chars=100)
    assert out == "error: not authorized"


def test_compress_non_dict_json_unchanged():
    fn, _, _ = _import_helpers()
    content = json.dumps([1, 2, 3])
    out = fn(content)
    assert out == content


def test_compress_drops_huge_aux_fields_keeps_small():
    fn, _, _ = _import_helpers()
    content = json.dumps({
        "success": True,
        "tag": "small",
        "huge_aux": "x" * 1000,  # >200 chars → drop
        "small_aux": [1, 2],
    })
    out = fn(content)
    parsed = json.loads(out)
    assert parsed["tag"] == "small"
    assert "huge_aux" not in parsed
    assert parsed["small_aux"] == [1, 2]


def test_compress_idempotent():
    """Compressing twice yields the same content as compressing once."""
    fn, _, _ = _import_helpers()
    content = json.dumps({
        "success": True, "output": "X" * 5000, "code": "verbose"
    })
    once = fn(content)
    twice = fn(once)
    assert once == twice, "compression must be idempotent"


# ── _compress_old_tool_results ─────────────────────────────────────────


def _msg(role, content="", **extra):
    return {"role": role, "content": content, **extra}


def test_compress_messages_keeps_last_3_tool_results_full():
    _, fn_msgs, _ = _import_helpers()
    long_output = json.dumps({"output": "Y" * 5000, "success": True})
    msgs = [
        _msg("user", "task"),
        _msg("assistant", "thinking"),
        _msg("tool", long_output, name="t0"),  # OLD — should compress
        _msg("tool", long_output, name="t1"),  # OLD — should compress
        _msg("tool", long_output, name="t2"),  # 3rd-from-last — keep
        _msg("tool", long_output, name="t3"),  # 2nd-from-last — keep
        _msg("tool", long_output, name="t4"),  # last — keep
    ]
    out = fn_msgs(msgs, keep_last_n=3)
    # Old tool results compressed
    assert "truncated" in out[2]["content"]
    assert "truncated" in out[3]["content"]
    # Last 3 untouched
    assert out[4]["content"] == long_output
    assert out[5]["content"] == long_output
    assert out[6]["content"] == long_output
    # Non-tool messages preserved
    assert out[0] == msgs[0]
    assert out[1] == msgs[1]


def test_compress_messages_few_tool_results_unchanged():
    _, fn_msgs, _ = _import_helpers()
    long_output = json.dumps({"output": "Y" * 5000, "success": True})
    msgs = [
        _msg("user", "task"),
        _msg("tool", long_output, name="only"),
    ]
    out = fn_msgs(msgs, keep_last_n=3)
    # 1 tool result, keep_last_n=3 — nothing to compress
    assert out[1]["content"] == long_output


def test_compress_messages_at_keep_boundary_unchanged():
    """Exactly keep_last_n tool results — none should be compressed."""
    _, fn_msgs, _ = _import_helpers()
    long_output = json.dumps({"output": "Z" * 5000})
    msgs = [
        _msg("tool", long_output, name=f"t{i}") for i in range(3)
    ]
    out = fn_msgs(msgs, keep_last_n=3)
    for orig, comp in zip(msgs, out):
        assert orig["content"] == comp["content"]


def test_compress_messages_empty_input_unchanged():
    _, fn_msgs, _ = _import_helpers()
    assert fn_msgs([], keep_last_n=3) == []


def test_compress_messages_preserves_role_order_and_extras():
    _, fn_msgs, _ = _import_helpers()
    long_output = json.dumps({"output": "X" * 5000})
    msgs = [
        _msg("system", "you are X"),
        _msg("user", "do thing"),
        _msg("assistant", "ok", tool_calls=[{"name": "t1", "args": {}}]),
        _msg("tool", long_output, tool_call_id="abc"),  # old
        _msg("assistant", "step 2", tool_calls=[{"name": "t2"}]),
        _msg("tool", long_output, tool_call_id="def"),  # old
        _msg("assistant", "step 3", tool_calls=[{"name": "t3"}]),
        _msg("tool", long_output, tool_call_id="xyz"),  # newest — keep
    ]
    out = fn_msgs(msgs, keep_last_n=1)
    # old tool messages compressed
    assert "truncated" in out[3]["content"]
    assert "truncated" in out[5]["content"]
    # last tool message untouched
    assert out[7]["content"] == long_output
    # tool_call_ids preserved
    assert out[3]["tool_call_id"] == "abc"
    assert out[5]["tool_call_id"] == "def"
    assert out[7]["tool_call_id"] == "xyz"
    # system/user/assistant roles untouched
    assert out[0] == msgs[0]
    assert out[1] == msgs[1]
    assert out[2] == msgs[2]


# ── _measure_messages_bytes ─────────────────────────────────────────


def test_measure_bytes_returns_positive_for_nonempty():
    _, _, fn = _import_helpers()
    msgs = [_msg("user", "hello"), _msg("tool", '{"x": 1}')]
    assert fn(msgs) > 0


def test_measure_bytes_zero_for_empty():
    _, _, fn = _import_helpers()
    assert fn([]) == 0


def test_compression_actually_reduces_bytes():
    """End-to-end: compressing a chunky message list shrinks measured bytes."""
    _, fn_msgs, fn_meas = _import_helpers()
    long = json.dumps({"output": "Q" * 10000, "code": "K" * 5000, "success": True})
    msgs = [_msg("tool", long, name=f"t{i}") for i in range(10)]
    pre = fn_meas(msgs)
    post = fn_meas(fn_msgs(msgs, keep_last_n=3))
    assert post < pre, f"compression must reduce bytes: pre={pre} post={post}"
    # Should reduce by >50% with 7/10 messages compressed
    assert post < pre * 0.6


# ── _apply_per_call_budget (P2) ───────────────────────────────────────


def _imp_budget():
    from service.isaac_assist_service.chat.orchestrator import (
        _apply_per_call_budget,
        _measure_messages_bytes,
    )
    return _apply_per_call_budget, _measure_messages_bytes


def test_budget_under_threshold_unchanged():
    fn, _ = _imp_budget()
    msgs = [_msg("user", "hi"), _msg("tool", "small", tool_call_id="x")]
    assert fn(msgs, budget_bytes=10000) == msgs


def test_budget_over_threshold_drops_oldest_tool():
    """FIFO drop: oldest tool_result gets marker; newest preserved."""
    fn, meas = _imp_budget()
    big = "X" * 50000
    msgs = [
        _msg("user", "do it"),
        _msg("assistant", "ok"),
        _msg("tool", big, tool_call_id="old"),
        _msg("tool", big, tool_call_id="mid"),
        _msg("tool", "recent_result", tool_call_id="new"),
    ]
    out = fn(msgs, budget_bytes=60000)
    assert meas(out) <= 60000
    assert "dropped tool_result" in out[2]["content"]
    # Newest tool preserved
    assert out[4]["content"] == "recent_result"


def test_budget_preserves_message_count_and_order():
    fn, _ = _imp_budget()
    big = "X" * 50000
    msgs = [
        _msg("user", "u1"),
        _msg("tool", big, tool_call_id="a"),
        _msg("tool", big, tool_call_id="b"),
    ]
    out = fn(msgs, budget_bytes=60000)
    assert len(out) == len(msgs)
    # tool_call_ids preserved (assistant chain stays valid)
    assert out[1]["tool_call_id"] == "a"
    assert out[2]["tool_call_id"] == "b"
    assert out[1]["role"] == "tool"


def test_budget_env_disable():
    import os
    fn, _ = _imp_budget()
    msgs = [_msg("tool", "X" * 50000, tool_call_id="x")]
    os.environ["PER_CALL_BUDGET"] = "off"
    try:
        out = fn(msgs, budget_bytes=100)
        assert out == msgs
    finally:
        os.environ.pop("PER_CALL_BUDGET", None)


def test_budget_idempotent():
    fn, _ = _imp_budget()
    big = "X" * 50000
    msgs = [
        _msg("tool", big, tool_call_id="a"),
        _msg("tool", big, tool_call_id="b"),
        _msg("tool", "recent", tool_call_id="c"),
    ]
    once = fn(msgs, budget_bytes=60000)
    twice = fn(once, budget_bytes=60000)
    assert once == twice


def test_budget_marker_preserves_size_info():
    """Marker should encode original size + msg index for debugging."""
    fn, _ = _imp_budget()
    msgs = [
        _msg("tool", "Y" * 50000, tool_call_id="a"),
        _msg("tool", "small", tool_call_id="b"),
    ]
    out = fn(msgs, budget_bytes=10000)
    marker = out[0]["content"]
    assert "n=" in marker
    assert "from_msg=" in marker
    assert "per_call_budget" in marker


def test_budget_skips_already_dropped():
    """Idempotency depends on not re-dropping marker content."""
    fn, _ = _imp_budget()
    msgs = [
        _msg("tool", "<dropped tool_result n=999 from_msg=0 reason=per_call_budget>",
             tool_call_id="a"),
        _msg("tool", "X" * 50000, tool_call_id="b"),
    ]
    out = fn(msgs, budget_bytes=10000)
    # Already-dropped should be left alone; only the big one drops
    assert out[0]["content"].startswith("<dropped tool_result")
    assert out[1]["content"].startswith("<dropped tool_result")
