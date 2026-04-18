"""CI-style scan of tool_executor.py for known silent-success antipatterns.

Runs as an L0 test. If a new handler introduces a pattern that looked
silent-success during the 2026-04-18 audit (`try/except ... print(...)`
without a `raise`, or `omni.kit.commands.execute` without a before/after
state-diff, or `AddReference` without `HasAuthoredReferences`, etc.),
add that handler to `AUDITED_CLEAN` after verifying it's either actually
honest or fixed. Keeps the corpus honest as new handlers get bundled.

The test intentionally allowlists-by-name — whitelisting by behavior
would require parsing the generated string template, which is brittle.
If a handler fails this test, either:
  1. Fix the honesty hole and remove it from the failing set, or
  2. Add it to AUDITED_CLEAN if you've verified the pattern is false-
     positive for this handler (comment inside the set explaining why).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


_TOOL_EXECUTOR = (
    Path(__file__).resolve().parent.parent
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "tool_executor.py"
)


def _iter_handlers():
    """Yield (name, body) for every def `_gen_*` / `_handle_*` in tool_executor."""
    src = _TOOL_EXECUTOR.read_text()
    heads = list(re.finditer(
        r"^(?:async\s+)?def (_gen_[a-z0-9_]+|_handle_[a-z0-9_]+)\(",
        src, re.M,
    ))
    for i, h in enumerate(heads):
        name = h.group(1)
        start = h.start()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(src)
        yield name, src[start:end]


# Handlers audited on 2026-04-18 and confirmed OR fixed to raise on
# silent-failure conditions. Membership here means: this function has
# either (a) a verification post-check or (b) the antipattern match is
# spurious (e.g. the `print` is inside an explicit else-branch that
# doesn't hide a mutation result). DO NOT add a handler here without
# verifying via live Kit RPC that bad inputs actually raise.
AUDITED_CLEAN = frozenset({
    # Fixed 2026-04-18:
    "_gen_apply_api_schema",
    "_gen_bulk_apply_schema",
    "_gen_find_prims_by_schema",
    "_gen_check_physics_health",
    "_gen_create_graph",
    "_gen_create_arena",
    "_gen_create_behavior",
    "_gen_import_robot",
    "_gen_add_reference",
    "_gen_add_usd_reference",
    "_gen_robot_wizard",
    "_gen_delete_prim",
    "_gen_open_stage",
    "_gen_enable_extension",
    "_gen_export_stage",
    "_gen_batch_set_attributes",
    "_gen_batch_delete_prims",
    "_gen_set_viewport_camera",
    "_gen_anchor_robot",
    "_gen_build_scene_from_blueprint",
    "_gen_save_stage",
    "_gen_set_audio_property",
    "_gen_focus_viewport_on",
    "_gen_scatter_on_surface",
    # Verified honest by inspection (already raises or is read-only):
    "_gen_set_attribute",           # USD attr.Set raises on type mismatch
    "_gen_teleport_prim",           # Xformable(invalid_prim) raises
    "_gen_fix_collision_mesh",      # explicit raise on missing prim / non-mesh
    "_gen_check_path_clearance",    # validates prim, raises on kin fallback
    "_gen_set_joint_targets",       # attr.Set on invalid attr raises
    "_gen_apply_force",              # explicit raise on invalid prim
    "_gen_clone_prim",              # Sdf.CopySpec raises on bad source
    "_gen_set_light_intensity",     # validates prim, raises
    "_gen_start_teleop_session",    # `assert robot_prim.IsValid()` at top
    "_gen_visualize_collision_mesh", # `raise RuntimeError("Prim not found")`
    "_gen_restore_delta_snapshot",   # query/informational; print-on-apply-fail ok
    "_gen_set_drive_gains",          # UsdPhysics.DriveAPI.Apply on invalid prim raises internally
    "_gen_set_semantic_label",       # Semantics.SemanticsAPI.Apply on invalid prim raises internally
    "_gen_batch_apply_operation",    # explicit `raise RuntimeError('Parent prim not found')`
    "_gen_enable_deterministic_mode",# no scene mutation — sets solver/timestep knobs; smoke-tested to succeed
    "_gen_quick_demo",               # smoke-tested 2026-04-18 — builds a full preset scene successfully
    "_gen_create_broken_scene",      # explicit raise when setup validation fails
    "_gen_load_scene_template",      # preset template; apply loop validates prim types internally
    # Query-only handlers (return data or print a structured result).
    # `print(json.dumps({'error': ...}))` is acceptable because the caller
    # parses the JSON output and can see the error field — not a
    # scene-mutation silent success.
    "_handle_get_render_config",
    "_handle_list_layers",
    "_handle_get_timeline_state",
    "_handle_list_semantic_classes",
    "_handle_validate_semantic_labels",
    # eval_harness — coarse test runner; print-and-continue is intentional
    # for partial-result reporting (the result payload lists passes/fails).
    "_gen_eval_harness",
    # Additional handlers fixed 2026-04-18 evening:
    "_gen_configure_self_collision",  # now raises on invalid robot_prim + filter pair links
    "_gen_tune_gains",                # raises on missing joint/art + zero-drives case
    "_gen_create_prim",               # allowlist of prim_type + post-check via type match
    "_gen_set_render_mode",           # explicit reject of unknown mode
    "_gen_add_node",                  # pre-check graph + post-check node landed
    "_gen_create_hdri_skydome",       # SdfPath validity + IsValid + HasAuthoredValue
    "_gen_assemble_robot",            # fail-fast NotImplementedError (5.x API drift)
    "_gen_set_variant",               # @honesty_checked prim-exists pre-check + variant post-check
    # Verified by Agent F audit + live-probe (false positives, USD raises internally):
    "_gen_assign_material",           # UsdShade.MaterialBindingAPI(invalid).Bind raises internally
    "_gen_configure_camera",          # UsdGeom.Camera(invalid).GetFocalLengthAttr raises
    "_gen_deformable_body",           # verified: fails with 'Accessed invalid null prim' on bad prim
    "_gen_deformable_surface",        # same pattern as deformable_body
    "_gen_grasp_object",              # Xformable(invalid).ComputeLocalToWorldTransform raises
    # Verified by Agent B audit (false positives):
    "_gen_connect_nodes",             # og.Controller raises OmniGraphError on invalid endpoints
    "_gen_create_material",           # Sdf.Path validity check raises on malformed paths
    "_gen_set_prim_metadata",         # explicit raise on missing prim
    "_gen_create_omnigraph",          # fails loudly via Kit API mismatch
    "_gen_clone_envs",                # GridCloner raises 'Source prim does not exist'
    "_gen_create_conveyor",           # fails loudly (though confusing error)
})


_TRY_PRINT = re.compile(r"except\s+Exception[^\n]*:\s*\n\s*print\(")
_KIT_COMMAND = re.compile(r"omni\.kit\.commands\.execute\(['\"][A-Za-z]+Command")
_ADD_REFERENCE = re.compile(r"\.AddReference\(")
_HAS_AUTHORED = re.compile(r"HasAuthoredReferences")
# Plain `print('Failed to …')` / `print('No … found')` / `print('Nothing to …')`
# outside a try/except is a silent-swallow on a negative-sentinel path: the
# operation didn't happen but the tool still returns success=True. This class
# of bug was common enough in 2026-04-18's trajectory-handler audit to warrant
# its own scan pattern (record_trajectory / replay_trajectory / record_waypoints
# / plan_trajectory / move_to_pose / configure_ros2_bridge were all six fixed
# in a single sitting). Matches both 'print(...)' and 'print(f"...")' forms.
_PRINT_FAIL = re.compile(
    r"print\(f?['\"](?:Failed (?:to|at|-)|No\s+\w+\s+(?:found|specified)|"
    r"Nothing (?:to|was)|Could not|Unable to)",
    re.I,
)


def test_no_new_try_except_print_without_raise():
    """Silent-swallow pattern: `except Exception: print(...)` without a
    subsequent `raise` turns scene-mutating failures into success=True."""
    offenders = []
    for name, body in _iter_handlers():
        if name in AUDITED_CLEAN:
            continue
        if not _TRY_PRINT.search(body):
            continue
        # Allow if the except block also re-raises somewhere in the body
        except_idx = body.find("except Exception")
        tail = body[except_idx:except_idx + 800] if except_idx >= 0 else ""
        if "raise" in tail:
            continue
        offenders.append(name)
    assert not offenders, (
        "These handlers match the 'try/except + print, no raise' silent-success "
        f"pattern and are not in AUDITED_CLEAN:\n  " + "\n  ".join(offenders) +
        "\n\nFix: verify the handler via live Kit RPC with bad inputs. If it "
        "already raises, add its name to AUDITED_CLEAN. If it silently succeeds, "
        "replace the `print(...)` with `raise RuntimeError(...)` carrying the "
        "specific failure reason."
    )


def test_no_new_kit_command_without_before_after_diff():
    """omni.kit.commands.execute('...Command', ...) silently no-ops on
    unknown command args; handlers using it must diff state before/after."""
    offenders = []
    for name, body in _iter_handlers():
        if name in AUDITED_CLEAN:
            continue
        if not _KIT_COMMAND.search(body):
            continue
        # Must include some before/after state check
        if "_before" in body and "_after" in body:
            continue
        if "raise" not in body:
            offenders.append(name)
    assert not offenders, (
        "These handlers call omni.kit.commands.execute but don't diff state "
        f"before/after:\n  " + "\n  ".join(offenders) +
        "\n\nKit commands silently no-op on invalid args (unknown api names, "
        "unknown command names). Without a before/after check the tool "
        "reports success on operations that did nothing."
    )


def test_no_new_print_fail_without_raise():
    """Plain `print('Failed to …')` or `print('No sensors specified …')` on a
    negative-sentinel path, without a following `raise`, is a silent-swallow:
    the operation didn't happen but the tool returns success=True anyway.

    Six handlers had this pattern before 2026-04-18 (record_trajectory,
    replay_trajectory, record_waypoints, plan_trajectory, move_to_pose
    lula_rrt, configure_ros2_bridge) — now all fixed. This scan keeps
    future additions honest.
    """
    offenders = []
    for name, body in _iter_handlers():
        if name in AUDITED_CLEAN:
            continue
        match = _PRINT_FAIL.search(body)
        if not match:
            continue
        # Allow if there's any raise in the 300 chars after the failing print
        # OR if the print is a diagnostic followed by a raise within the same
        # if-block (heuristic: just check 'raise' appears after the match).
        tail = body[match.start():match.start() + 400]
        if "raise" in tail:
            continue
        offenders.append(name)
    assert not offenders, (
        "These handlers print a failure-sentinel but don't raise — the tool "
        f"reports success=True to the agent even though nothing happened:\n  "
        + "\n  ".join(offenders) +
        "\n\nFix: replace the print with a `raise RuntimeError(...)` carrying "
        "the specific failure reason, OR add the handler to AUDITED_CLEAN if "
        "the print is informational (e.g. a legitimate idempotent no-op)."
    )


def test_add_reference_has_post_check():
    """prim.GetReferences().AddReference(url) returns True even for
    nonexistent USD files (composition is lazy). Every handler that uses
    it must post-check HasAuthoredReferences OR pre-check os.path.exists."""
    offenders = []
    for name, body in _iter_handlers():
        if name in AUDITED_CLEAN:
            continue
        if not _ADD_REFERENCE.search(body):
            continue
        if _HAS_AUTHORED.search(body) or "os.path.exists" in body:
            continue
        offenders.append(name)
    assert not offenders, (
        "These handlers call AddReference but don't verify the reference "
        f"landed:\n  " + "\n  ".join(offenders) +
        "\n\nUSD AddReference accepts any URL and composition is lazy — "
        "bad paths produce a prim with HasAuthoredReferences=True but no "
        "children. Add either os.path.exists precheck or HasAuthoredReferences "
        "post-check."
    )
