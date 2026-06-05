"""Round 13 — unit tests for the {{#each}} loop_substitution feature.

Tests cover:
  - Basic list expansion (3 items → 3 lines)
  - Empty list → empty output
  - Multi-line block body
  - Nested {{this.field}} access
  - {{this}} whole-item substitution
  - Mismatched {{/each}} raises ValueError
  - Nested {{#each}} raises ValueError
  - Backward compat: templates without {{#each}} blocks substitute unchanged
  - Indentation preservation from the {{#each}} line
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _sub(template: str, role_defaults: dict) -> str:
    from service.isaac_assist_service.chat.canonical_instantiator import (
        substitute_role_placeholders,
    )
    return substitute_role_placeholders(template, role_defaults)


def _expand(template: str, role_defaults: dict) -> str:
    from service.isaac_assist_service.chat.canonical_instantiator import (
        _expand_each_blocks,
    )
    return _expand_each_blocks(template, role_defaults)


# ---------------------------------------------------------------------------
# Basic expansion
# ---------------------------------------------------------------------------

def test_basic_list_expansion():
    """3-item list produces 3 lines."""
    tmpl = "{{#each cubes}}\ncreate_prim(path={{this.path}})\n{{/each}}"
    defaults = {
        "cubes": [
            {"path": "/World/Cube_1"},
            {"path": "/World/Cube_2"},
            {"path": "/World/Cube_3"},
        ]
    }
    result = _expand(tmpl, defaults)
    lines = [l for l in result.split("\n") if l.strip()]
    assert len(lines) == 3
    assert "'/World/Cube_1'" in lines[0]
    assert "'/World/Cube_2'" in lines[1]
    assert "'/World/Cube_3'" in lines[2]


def test_empty_list_produces_empty_block():
    """Empty list → no output lines from the block."""
    tmpl = "before\n{{#each cubes}}\ncreate_prim(path={{this.path}})\n{{/each}}\nafter"
    defaults = {"cubes": []}
    result = _expand(tmpl, defaults)
    lines = [l for l in result.split("\n") if l.strip()]
    assert lines == ["before", "after"]


# ---------------------------------------------------------------------------
# Multi-line block body
# ---------------------------------------------------------------------------

def test_multiline_block_body():
    """Each item expands the full multi-line block."""
    tmpl = (
        "{{#each items}}\n"
        "create_prim(path={{this.path}})\n"
        "apply_api_schema(prim_path={{this.path}}, schema_name=\"PhysicsCollisionAPI\")\n"
        "{{/each}}"
    )
    defaults = {
        "items": [
            {"path": "/World/A"},
            {"path": "/World/B"},
        ]
    }
    result = _expand(tmpl, defaults)
    lines = [l for l in result.split("\n") if l.strip()]
    # 2 items × 2 lines = 4 non-empty lines
    assert len(lines) == 4
    assert lines[0].startswith("create_prim") and "'/World/A'" in lines[0]
    assert lines[1].startswith("apply_api_schema") and "'/World/A'" in lines[1]
    assert lines[2].startswith("create_prim") and "'/World/B'" in lines[2]
    assert lines[3].startswith("apply_api_schema") and "'/World/B'" in lines[3]


# ---------------------------------------------------------------------------
# Nested field access
# ---------------------------------------------------------------------------

def test_nested_field_access():
    """{{this.position}} renders the position list as Python literal."""
    tmpl = "{{#each cubes}}\nset_attr(path={{this.path}}, pos={{this.position}})\n{{/each}}"
    defaults = {
        "cubes": [
            {"path": "/World/C1", "position": [1.0, 2.0, 3.0]},
        ]
    }
    result = _expand(tmpl, defaults)
    assert "[1.0, 2.0, 3.0]" in result


def test_this_whole_item_is_scalar():
    """{{this}} on a scalar list formats each item directly."""
    tmpl = "paths = [\n{{#each paths}}\n    {{this}},\n{{/each}}\n]"
    defaults = {"paths": ["/World/A", "/World/B"]}
    result = _expand(tmpl, defaults)
    assert "'/World/A'" in result
    assert "'/World/B'" in result


# ---------------------------------------------------------------------------
# Role dotted notation: {{#each role.field}}
# ---------------------------------------------------------------------------

def test_role_dotted_each():
    """{{#each primary_robot.items}} is not supported — role itself must be
    the list. But {{#each workpieces}} where workpieces is the list works."""
    tmpl = "{{#each workpieces}}\nx={{this.x}}\n{{/each}}"
    defaults = {
        "workpieces": [
            {"x": 1},
            {"x": 2},
        ]
    }
    result = _expand(tmpl, defaults)
    assert "1" in result
    assert "2" in result


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_mismatched_close_tag_raises():
    """{{#each}} without {{/each}} raises ValueError."""
    tmpl = "{{#each items}}\nfoo\n"
    with pytest.raises(ValueError, match="no matching"):
        _expand(tmpl, {"items": [{"x": 1}]})


def test_nested_each_raises():
    """Nested {{#each}} blocks raise ValueError."""
    tmpl = (
        "{{#each outer}}\n"
        "  {{#each inner}}\n"
        "  foo\n"
        "  {{/each}}\n"
        "{{/each}}"
    )
    with pytest.raises(ValueError, match="Nested"):
        _expand(tmpl, {"outer": [{"x": 1}], "inner": [{"y": 2}]})


# ---------------------------------------------------------------------------
# Indentation preservation
# ---------------------------------------------------------------------------

def test_indentation_preserved():
    """Block lines in an indented {{#each}} keep the opening tag's indentation."""
    tmpl = "    {{#each cubes}}\npath={{this.path}}\n    {{/each}}"
    defaults = {"cubes": [{"path": "/World/X"}]}
    result = _expand(tmpl, defaults)
    # The body line "path=..." should have the 4-space indent prepended.
    expanded_lines = [l for l in result.split("\n") if l.strip()]
    assert expanded_lines[0].startswith("    path=")


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_backward_compat_no_each():
    """Templates without {{#each}} pass through the scalar substitution
    path unchanged — existing CP-01..CP-19 behaviour preserved."""
    tmpl = "robot_wizard(robot_name={{primary_robot.class}}, dest_path={{primary_robot.path}})"
    defaults = {
        "primary_robot": {
            "class": "franka_panda",
            "path": "/World/Franka",
        }
    }
    result = _sub(tmpl, defaults)
    assert "'franka_panda'" in result
    assert "'/World/Franka'" in result
    assert "{{" not in result


def test_backward_compat_indexed():
    """Existing {{role[N].field}} indexed placeholders still work alongside
    the new #each feature."""
    tmpl = (
        "create_prim(path={{workpieces[0].path}})\n"
        "{{#each workpieces}}\n"
        "apply_api_schema(prim_path={{this.path}}, schema_name=\"Foo\")\n"
        "{{/each}}"
    )
    defaults = {
        "workpieces": [
            {"path": "/World/Cube_1"},
            {"path": "/World/Cube_2"},
        ]
    }
    result = _sub(tmpl, defaults)
    # Indexed reference
    assert result.startswith("create_prim(path='/World/Cube_1')")
    # Loop expansion
    assert result.count("apply_api_schema") == 2


# ---------------------------------------------------------------------------
# Full round-trip: substitute_role_placeholders with #each
# ---------------------------------------------------------------------------

def test_full_roundtrip_with_each():
    """Scalar + loop substitution together, simulating a minimal template."""
    tmpl = (
        "robot_wizard(robot_name={{primary_robot.class}})\n"
        "{{#each workpieces}}\n"
        "create_prim(prim_path={{this.path}}, position={{this.position}})\n"
        "{{/each}}"
    )
    defaults = {
        "primary_robot": {"class": "franka_panda"},
        "workpieces": [
            {"path": "/World/Cube_1", "position": [-1.0, 0.4, 0.835]},
            {"path": "/World/Cube_2", "position": [-0.8, 0.4, 0.835]},
            {"path": "/World/Cube_3", "position": [-0.6, 0.4, 0.835]},
        ],
    }
    result = _sub(tmpl, defaults)
    assert "'franka_panda'" in result
    assert result.count("create_prim") == 3
    assert "'/World/Cube_1'" in result
    assert "[-1.0, 0.4, 0.835]" in result
    assert "{{" not in result
