"""L0 unit tests for the pure functions in canonical_instantiator.py.

Covers:
- substitute_template_params (T2 parameter substitution)
- _extract_prim_paths (regex extraction of prim_path= / dest_path= /
  sensor_path= / robot_path= literals from canonical code)
- format_instantiation_summary (LLM directive after hard-instantiate)

These functions form the seam between canonical templates and the LLM:
if substitution puts the wrong value, scenes get built wrong; if path
extraction misses a prim, the directive omits it and the LLM hallucinates;
if the summary is malformed the LLM doesn't realize the scene is built.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _imp():
    from service.isaac_assist_service.chat.canonical_instantiator import (
        substitute_template_params,
        _extract_prim_paths,
        format_instantiation_summary,
    )
    return substitute_template_params, _extract_prim_paths, format_instantiation_summary


# ── substitute_template_params ─────────────────────────────────────────


def test_substitute_returns_unchanged_when_no_parameters():
    sub, _, _ = _imp()
    code = 'create_prim(prim_path="/World/X")'
    out, eff = sub(code, None)
    assert out == code
    assert eff == {}


def test_substitute_returns_unchanged_when_empty_parameters():
    sub, _, _ = _imp()
    code = 'create_prim(prim_path="/World/X")'
    out, eff = sub(code, {})
    assert out == code
    assert eff == {}


def test_substitute_replaces_single_placeholder():
    sub, _, _ = _imp()
    code = 'create_prim(prim_path="/World/{{name}}")'
    out, eff = sub(code, {"name": "MyCube"})
    assert out == 'create_prim(prim_path="/World/MyCube")'
    assert eff == {"name": "MyCube"}


def test_substitute_overrides_take_precedence():
    sub, _, _ = _imp()
    code = 'set_n({{count}})'
    out, eff = sub(code, {"count": 3}, overrides={"count": 5})
    assert out == "set_n(5)"
    assert eff == {"count": 5}


def test_substitute_multiple_placeholders():
    sub, _, _ = _imp()
    code = 'create_prim(prim_path="/World/{{name}}", n={{count}})'
    out, _ = sub(code, {"name": "X", "count": 3})
    assert out == 'create_prim(prim_path="/World/X", n=3)'


def test_substitute_repeated_same_placeholder():
    sub, _, _ = _imp()
    code = 'a={{x}}; b={{x}}'
    out, _ = sub(code, {"x": "Q"})
    assert out == "a=Q; b=Q"


def test_substitute_unknown_placeholder_left_untouched():
    sub, _, _ = _imp()
    code = "{{known}} {{unknown}}"
    out, _ = sub(code, {"known": "ok"})
    assert out == "ok {{unknown}}"


def test_substitute_handles_empty_code():
    sub, _, _ = _imp()
    out, eff = sub("", {"x": 1})
    assert out == ""
    assert eff == {"x": 1}


def test_substitute_coerces_non_string_values():
    sub, _, _ = _imp()
    code = "{{a}} {{b}} {{c}}"
    out, _ = sub(code, {"a": 42, "b": 3.14, "c": True})
    assert "42" in out
    assert "3.14" in out
    assert "True" in out


# ── _extract_prim_paths ────────────────────────────────────────────────


def test_extract_paths_finds_prim_path():
    _, ext, _ = _imp()
    code = '\ncreate_prim(prim_path="/World/Cube_1", type="Cube")\n'
    out = ext({"code": code})
    assert out == ["/World/Cube_1"]


def test_extract_paths_finds_multiple_distinct():
    _, ext, _ = _imp()
    code = '\ncreate_prim(prim_path="/World/A")\ncreate_prim(prim_path="/World/B")\n'
    out = ext({"code": code})
    assert out == ["/World/A", "/World/B"]


def test_extract_paths_deduplicates():
    _, ext, _ = _imp()
    code = (
        'create_prim(prim_path="/World/X")\n'
        'set_attr(prim_path="/World/X", attr="visible")\n'
    )
    out = ext({"code": code})
    assert out == ["/World/X"]


def test_extract_paths_handles_dest_path():
    _, ext, _ = _imp()
    code = 'setup_pp(robot_path="/World/Franka", dest_path="/World/Bin")'
    out = ext({"code": code})
    assert "/World/Franka" in out
    assert "/World/Bin" in out


def test_extract_paths_handles_sensor_path():
    _, ext, _ = _imp()
    code = 'add_sensor(sensor_path="/World/Sensor_1")'
    out = ext({"code": code})
    assert out == ["/World/Sensor_1"]


def test_extract_paths_returns_empty_for_no_paths():
    _, ext, _ = _imp()
    code = "import omni.usd\nstage = omni.usd.get_context().get_stage()"
    out = ext({"code": code})
    assert out == []


def test_extract_paths_returns_empty_for_missing_code():
    _, ext, _ = _imp()
    assert ext({}) == []
    assert ext({"code": ""}) == []


def test_extract_paths_handles_single_quotes():
    _, ext, _ = _imp()
    code = "create_prim(prim_path='/World/SingleQuoted')"
    out = ext({"code": code})
    assert out == ["/World/SingleQuoted"]


def test_extract_paths_skips_non_absolute():
    """Anchor: prim paths must start with /. Relative names get filtered."""
    _, ext, _ = _imp()
    code = 'create_prim(prim_path="World/Relative")'  # no leading slash
    out = ext({"code": code})
    assert out == []


def test_extract_paths_preserves_insertion_order():
    """Order is meaningful — agent reads it as build sequence."""
    _, ext, _ = _imp()
    code = (
        'create_prim(prim_path="/World/Z")\n'
        'create_prim(prim_path="/World/A")\n'
        'create_prim(prim_path="/World/M")\n'
    )
    out = ext({"code": code})
    assert out == ["/World/Z", "/World/A", "/World/M"]


# ── format_instantiation_summary ───────────────────────────────────────


def test_summary_returns_empty_when_not_instantiated():
    _, _, fmt = _imp()
    out = fmt({"instantiated": False})
    assert out == ""


def test_summary_includes_task_id_and_n_ok():
    _, _, fmt = _imp()
    out = fmt({
        "instantiated": True,
        "task_id": "CP-02",
        "n_ok": 23, "n_calls": 23,
        "executed": [{"tool": "create_prim", "ok": True}],
        "errors": [],
    })
    assert "CP-02" in out
    assert "23/23" in out


def test_summary_lists_prim_paths_from_template():
    _, _, fmt = _imp()
    out = fmt(
        {"instantiated": True, "task_id": "T1", "n_ok": 1, "n_calls": 1,
         "executed": [{"tool": "create_prim", "ok": True}], "errors": []},
        template={"code": 'create_prim(prim_path="/World/MyCube")'},
    )
    assert "/World/MyCube" in out


def test_summary_includes_forbidden_tools_used():
    """Tools that built the scene are listed as forbidden in subsequent
    turns (the LLM cannot call them again — schema is filtered)."""
    _, _, fmt = _imp()
    out = fmt({
        "instantiated": True, "task_id": "T1", "n_ok": 2, "n_calls": 2,
        "executed": [
            {"tool": "create_prim", "ok": True},
            {"tool": "robot_wizard", "ok": True},
        ],
        "errors": [],
    })
    # The directive mentions VERIFICATION ONLY — no rebuild
    assert "VERIFICATION" in out
    assert "build" in out.lower()


def test_summary_with_pre_executed_verify_pipeline_ok_true():
    """When verify pre-executed and passed, the summary surfaces it."""
    _, _, fmt = _imp()
    out = fmt(
        {"instantiated": True, "task_id": "T1", "n_ok": 1, "n_calls": 1,
         "executed": [{"tool": "create_prim", "ok": True}], "errors": []},
        template={"code": 'create_prim(prim_path="/World/X")'},
        verify_result={"executed": True, "pipeline_ok": True},
    )
    assert "TRUE" in out or "passed" in out.lower()


def test_summary_with_pre_executed_verify_failure_lists_issues():
    _, _, fmt = _imp()
    out = fmt(
        {"instantiated": True, "task_id": "T1", "n_ok": 1, "n_calls": 1,
         "executed": [{"tool": "create_prim", "ok": True}], "errors": []},
        template={"code": 'create_prim(prim_path="/World/X")'},
        verify_result={
            "executed": True, "pipeline_ok": False,
            "issues": ["[reach] foo", "[bridge] bar"],
        },
    )
    assert "FALSE" in out or "failed" in out.lower() or "✗" in out
    assert "foo" in out or "bar" in out


# ── _extract_cube_positions_from_code (settle_after_canonical helper) ──


def _imp_settle():
    from service.isaac_assist_service.chat.canonical_instantiator import (
        _extract_cube_positions_from_code,
        _extract_conveyor_velocities_from_code,
    )
    return _extract_cube_positions_from_code, _extract_conveyor_velocities_from_code


def test_extract_cubes_simple_create_prim():
    fn, _ = _imp_settle()
    code = 'create_prim(prim_path="/World/Cube_A", prim_type="Cube", position=[1.0, 2.0, 0.5], size=0.05)'
    out = fn(code)
    assert out == {"/World/Cube_A": [1.0, 2.0, 0.5]}


def test_extract_cubes_enumerate_loop_pattern():
    """CP-01 / CP-04 use this exact pattern. Must extract Cube_1..Cube_4
    with x-positions from the enumerate list."""
    fn, _ = _imp_settle()
    code = (
        'for i, x in enumerate([-1.4, -1.15, -0.9, -0.65]):\n'
        '    path = f"/World/Cube_{i+1}"\n'
        '    create_prim(prim_path=path, prim_type="Cube", position=[x, 0.4, 0.835], size=0.05)\n'
    )
    out = fn(code)
    assert out["/World/Cube_1"] == [-1.4, 0.4, 0.835]
    assert out["/World/Cube_2"] == [-1.15, 0.4, 0.835]
    assert out["/World/Cube_3"] == [-0.9, 0.4, 0.835]
    assert out["/World/Cube_4"] == [-0.65, 0.4, 0.835]


def test_extract_cubes_skips_unsubstituted_fstring():
    """f-string templates that didn't get substituted should be skipped,
    not return a path containing literal `{` braces."""
    fn, _ = _imp_settle()
    code = 'create_prim(prim_path=f"/World/{name}", position=[0, 0, 0])'
    out = fn(code)
    assert out == {}


def test_extract_cubes_empty_code():
    fn, _ = _imp_settle()
    assert fn("") == {}
    assert fn(None) == {}


def test_extract_cubes_no_cubes_in_code():
    fn, _ = _imp_settle()
    code = "import omni.usd\nstage = omni.usd.get_context().get_stage()"
    assert fn(code) == {}


def test_extract_cubes_handles_multiple_create_prim():
    fn, _ = _imp_settle()
    code = (
        'create_prim(prim_path="/World/A", prim_type="Cube", position=[0, 0, 0])\n'
        'create_prim(prim_path="/World/B", prim_type="Cube", position=[1, 1, 1])\n'
    )
    out = fn(code)
    assert "/World/A" in out and "/World/B" in out


def test_extract_conveyor_basic():
    _, fn = _imp_settle()
    code = 'create_conveyor(prim_path="/World/Belt", position=[0, 0, 0], surface_velocity=[0.2, 0, 0])'
    out = fn(code)
    assert out == {"/World/Belt": [0.2, 0.0, 0.0]}


def test_extract_conveyor_multiple():
    _, fn = _imp_settle()
    code = (
        'create_conveyor(prim_path="/World/Belt1", surface_velocity=[0.1, 0, 0])\n'
        'create_conveyor(prim_path="/World/Belt2", surface_velocity=[-0.2, 0, 0])\n'
    )
    out = fn(code)
    assert out["/World/Belt1"] == [0.1, 0.0, 0.0]
    assert out["/World/Belt2"] == [-0.2, 0.0, 0.0]


def test_extract_conveyor_no_conveyors():
    _, fn = _imp_settle()
    code = 'create_prim(prim_path="/World/X", position=[0,0,0])'
    assert fn(code) == {}


def test_extract_conveyor_empty_code():
    _, fn = _imp_settle()
    assert fn("") == {}
    assert fn(None) == {}


def test_extract_cubes_position_with_negative_floats():
    fn, _ = _imp_settle()
    code = 'create_prim(prim_path="/World/Q", prim_type="Cube", position=[-1.5, -0.4, -0.1])'
    out = fn(code)
    assert out == {"/World/Q": [-1.5, -0.4, -0.1]}
