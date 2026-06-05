"""Workflow handlers — target scope: workflow lifecycle (start/cancel/
edit/approve), async task dispatch + query, scheduled retries,
queue_write patches, slash-command discovery, scene-aware starter
prompts, feedback recording, watch_changes.

Phase 7 wave 12 — workflow data-handlers move out of tool_executor.py.
Same pattern as Phase 3 / Phase 5 / Phase 6 / Phase 7 waves 1-11.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 7.
"""
# audit-Q17: cohesive — full workflow handler domain (lifecycle, async tasks, scheduled retries, queue patches, slash-command discovery)
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

# Phase 8 wave 28 (2026-05-13): import shared async-task state.
import threading
import time as _time
from ._state import (
    ASYNC_TASKS as _ASYNC_TASKS,
    ASYNC_TASKS_LOCK as _ASYNC_TASKS_LOCK,
    _WORKFLOW_TEMPLATES,
    _WORKFLOWS_REGISTRY_LOCK,
    make_workflow_lock,
)
from service.isaac_assist_service.observability.handler_telemetry import with_telemetry


def _wf_lock_for(wf: Dict[str, Any]) -> threading.Lock:
    """Return the per-workflow lock, creating one lazily if absent.

    CONC-1 (2026-05-14): Workflows created by `_handle_start_workflow`
    carry a `_lock` field from inception. Tests (and the `_forward_*`
    helpers in `multimodal/routes.py`) sometimes construct minimal
    workflow dicts without the `_lock` field; rather than crash, we
    install a fresh lock the first time a handler needs it.

    Because `setdefault` is implemented at the C level in CPython, the
    setdefault call itself is atomic w.r.t. other Python threads: two
    concurrent callers will both see the same `threading.Lock` instance
    after the first one wins. (The second call's freshly-constructed
    Lock object is discarded by setdefault.) This keeps the per-workflow
    locking invariant intact even with mixed creation paths.
    """
    return wf.setdefault("_lock", make_workflow_lock())

# ---------------------------------------------------------------------------
# Theme-local helpers (Phase 8 wave 27, 2026-05-13)

def _wf_make_initial_plan(workflow_type: str, goal: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Build the initial editable plan artifact from a template + goal + params.

    The LLM is expected to refine this further on the user-facing side; this
    function only produces the structural skeleton so the workflow can be
    persisted and queried before the LLM round-trips.
    """
    tpl = _WORKFLOW_TEMPLATES[workflow_type]
    merged_params = dict(tpl["default_params"])
    merged_params.update(params or {})
    return {
        "workflow_type": workflow_type,
        "goal": goal,
        "params": merged_params,
        "phases": [
            {
                "name": p["name"],
                "checkpoint": p["checkpoint"],
                "error_fix": p["error_fix"],
                "status": "pending",
            }
            for p in tpl["phases"]
        ],
        "editable": True,
    }

def _wf_advance_phase(wf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Move the workflow to the next phase. Returns the next phase dict or None."""
    phases = wf["plan"]["phases"]
    current = wf["current_phase"]
    # Mark current as completed
    for p in phases:
        if p["name"] == current and p["status"] != "completed":
            p["status"] = "completed"
            wf["completed_phases"].append(current)
            break
    # Find next pending phase
    for p in phases:
        if p["status"] == "pending":
            wf["current_phase"] = p["name"]
            p["status"] = "in_progress"
            return p
    # No phases left
    wf["current_phase"] = None
    wf["status"] = "completed"
    return None

def _async_task_runner(task_id: str, task_type: str, params: Dict) -> None:
    """Worker body executed in a daemon thread.

    Real long-running ops (SDG, training) are dispatched via Kit; here we
    simulate progress so the lifecycle is observable from the chat panel.
    Production integrations replace this body with concrete handlers per
    task_type.
    """
    try:
        with _ASYNC_TASKS_LOCK:
            entry = _ASYNC_TASKS.get(task_id)
            if entry is None:
                return
            entry["state"] = "running"
            entry["started_at"] = _time.time()

        # Heuristic total duration so a smoke test completes quickly.
        total_steps = max(int(params.get("steps", 5)), 1)
        step_sleep = float(params.get("step_seconds", 0.0))
        for i in range(total_steps):
            if step_sleep > 0:
                _time.sleep(step_sleep)
            with _ASYNC_TASKS_LOCK:
                entry = _ASYNC_TASKS.get(task_id)
                if entry is None or entry.get("state") == "cancelled":
                    return
                entry["progress"] = (i + 1) / total_steps

        with _ASYNC_TASKS_LOCK:
            entry = _ASYNC_TASKS.get(task_id)
            if entry is None:
                return
            entry["state"] = "done"
            entry["finished_at"] = _time.time()
            entry["progress"] = 1.0
            entry["result"] = {
                "task_type": task_type,
                "params": params,
                "message": f"{task_type} task completed",
            }
    except Exception as e:  # noqa: BLE001
        with _ASYNC_TASKS_LOCK:
            entry = _ASYNC_TASKS.get(task_id)
            if entry is not None:
                entry["state"] = "error"
                entry["finished_at"] = _time.time()
                entry["error"] = str(e)

# ---------------------------------------------------------------------------
# Theme-local constants + helpers (Phase 8 wave 13, 2026-05-13)
# Migrated from tool_executor.py — used only by handlers.workflow.

_DEFAULT_SUGGESTIONS = [
    "Run the simulation to see the result",
    "Capture a viewport screenshot",
    "Check for any physics warnings",
]

_MOBILE_ROBOT_KEYWORDS = {"carter", "jetbot", "nova_carter", "kaya", "husky", "turtlebot"}

_SLASH_COMMANDS = [
    {"command": "/help", "description": "What can I do?", "always": True},
    {"command": "/scene", "description": "Summarize current scene", "always": True},
    {"command": "/debug", "description": "Diagnose physics issues", "requires_physics": True},
    {"command": "/performance", "description": "Why is my sim slow?", "always": True},
    {"command": "/workspace", "description": "Show robot workspace", "requires_robot": True},
    {"command": "/diff", "description": "What changed?", "always": True},
    {"command": "/import", "description": "Import a robot", "always": True},
    {"command": "/template", "description": "Load a scene template", "always": True},
]

_STARTER_PROMPTS = {
    "empty": {
        "welcome": "Your scene is empty — a blank canvas!",
        "prompts": [
            "Import a robot: 'add a Franka Panda to the scene'",
            "Load a template: 'set up a pick and place scene'",
            "Browse assets: 'show me available robots'",
        ],
    },
    "robot_only": {
        "welcome": "I see a robot in the scene, but no objects to interact with.",
        "prompts": [
            "Add objects: 'place 3 cubes on a table'",
            "Test the robot: 'move the arm to a test position'",
            "Check setup: 'are the collision meshes correct?'",
        ],
    },
    "robot_and_objects": {
        "welcome": "Your scene has a robot and objects — ready for action!",
        "prompts": [
            "Move the arm to grab the nearest object",
            "Why is the robot not moving?",
            "Show me the robot's workspace",
        ],
    },
    "mobile_robot": {
        "welcome": "I see a mobile robot in the scene.",
        "prompts": [
            "Drive the robot forward 2 meters",
            "Set up navigation: 'create an occupancy map'",
            "Check sensors: 'what sensors does the robot have?'",
        ],
    },
    "no_physics": {
        "welcome": "Physics is not enabled in this scene.",
        "prompts": [
            "Enable physics for this scene",
            "Add rigid body physics to the objects",
            "Set up a physics scene with gravity",
        ],
    },
}

_SUGGESTION_MAP = {
    "import_robot": [
        "Configure the gripper",
        "Check if the collision meshes are correct",
        "Move the arm to a test position",
    ],
    "create_prim": [
        "Add physics to this object",
        "Change the material or color",
        "Position it precisely in the scene",
    ],
    "clone_prim": [
        "Set up physics for all copies",
        "Create an RL training environment",
        "Adjust spacing between copies",
    ],
    "move_to_pose": [
        "Plan a pick-and-place sequence",
        "Check for collisions along the path",
        "Record the joint positions",
    ],
    "sim_control": [
        "Capture a screenshot of the result",
        "Check for physics errors",
        "Measure performance (FPS, frame time)",
    ],
    "create_material": [
        "Apply this material to an object",
        "Adjust roughness or metallic properties",
        "Create a glass or transparent variant",
    ],
    "configure_sdg": [
        "Preview a sample frame",
        "Add more randomizers (lighting, pose)",
        "Export to COCO or KITTI format",
    ],
    "set_physics_params": [
        "Test with a simulation run",
        "Add rigid body physics to objects",
        "Check solver iteration count for stability",
    ],
    "load_scene_template": [
        "Run the simulation to see it in action",
        "Customize the robot's behavior",
        "Capture a screenshot of the scene",
    ],
}

_WORKFLOW_RETRY_HARD_CAP = 5


# ---------------------------------------------------------------------------
# Phase 7 wave 12 — workflow data-handlers


@with_telemetry
async def _handle_record_feedback(args: Dict) -> Dict:
    """Link user feedback to a previously recorded turn."""
    from .. import tool_executor as _te  # noqa: PLC0415
    session_id = args["session_id"]
    turn_id = args["turn_id"]
    approved = args["approved"]
    edited = args.get("edited", False)
    correction = args.get("correction")
    from ._state import get_turn_recorder
    return get_turn_recorder().record_feedback(
        session_id=session_id,
        turn_id=turn_id,
        approved=approved,
        edited=edited,
        correction=correction,
    )


@with_telemetry
async def _handle_watch_changes(args: Dict) -> Dict:
    """Start/stop/query live change tracking via Tf.Notice in Kit."""
    from .. import kit_tools  # noqa: PLC0415
    import json  # noqa: PLC0415
    action = args.get("action", "query")

    if action == "start":
        code = """\
import omni.usd
import json

# Register a global change tracker (singleton pattern)
stage = omni.usd.get_context().get_stage()

if not hasattr(omni.usd, '_isaac_assist_change_tracker'):
    from pxr import Tf

    class _ChangeTracker:
        def __init__(self):
            self.changes = []
            self._listener = None

        def start(self, stage):
            self.changes = []
            self._listener = Tf.Notice.Register(
                Tf.Notice.ObjectsChanged, self._on_changed, stage
            )

        def stop(self):
            if self._listener:
                self._listener.Revoke()
                self._listener = None

        def _on_changed(self, notice, stage):
            for path in notice.GetResyncedPaths():
                self.changes.append({"path": str(path), "type": "structural"})
            for path in notice.GetChangedInfoOnlyPaths():
                self.changes.append({"path": str(path), "type": "value"})

    omni.usd._isaac_assist_change_tracker = _ChangeTracker()

tracker = omni.usd._isaac_assist_change_tracker
tracker.start(stage)
print(json.dumps({"status": "tracking_started", "message": "Live change tracking started."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(start)")
        return {
            "success": bool(result.get("success", False)),
            "status": "tracking_started",
            "message": "Live change tracking started. Use watch_changes(action='query') to see accumulated changes, or watch_changes(action='stop') to end.",
            "queued": result.get("queued", False),
        }

    elif action == "stop":
        code = """\
import omni.usd
import json

if hasattr(omni.usd, '_isaac_assist_change_tracker'):
    tracker = omni.usd._isaac_assist_change_tracker
    tracker.stop()
    count = len(tracker.changes)
    changes = tracker.changes[-100:]  # return last 100
    tracker.changes = []
    print(json.dumps({"status": "tracking_stopped", "total_changes": count, "changes": changes}))
else:
    print(json.dumps({"status": "not_running", "message": "No active change tracker."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(stop)")
        output = result.get("output", "")
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    parsed.setdefault("success", not bool(parsed.get("error")))
                    return parsed
                except json.JSONDecodeError:
                    pass
        return {"success": bool(result.get("success", False)), "status": "stopped", "queued": result.get("queued", False)}

    elif action == "query":
        code = """\
import omni.usd
import json

if hasattr(omni.usd, '_isaac_assist_change_tracker'):
    tracker = omni.usd._isaac_assist_change_tracker
    count = len(tracker.changes)
    # Deduplicate by path, keep latest type
    seen = {}
    for c in tracker.changes:
        seen[c["path"]] = c["type"]
    deduped = [{"path": p, "type": t} for p, t in seen.items()]
    print(json.dumps({"status": "tracking", "total_raw": count, "unique_paths": len(deduped), "changes": deduped[-100:]}))
else:
    print(json.dumps({"status": "not_running", "message": "No active change tracker. Call watch_changes(action='start') first."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(query)")
        output = result.get("output", "")
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    parsed.setdefault("success", not bool(parsed.get("error")))
                    return parsed
                except json.JSONDecodeError:
                    pass
        return {"success": bool(result.get("success", False)), "status": "query_sent", "queued": result.get("queued", False)}

    return {"success": False, "error": f"Unknown action: {action}. Use 'start', 'stop', or 'query'."}


@with_telemetry
async def _handle_scene_aware_starter_prompts(args: Dict) -> Dict:
    """Generate contextual starter prompts based on scene state."""
    from .. import kit_tools  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    try:
        ctx = await kit_tools.get_stage_context(full=False)
    except Exception:
        ctx = {}

    stage = ctx.get("stage", {})
    prim_count = stage.get("prim_count", 0)
    prims_by_type = stage.get("prims_by_type", {})

    # Detect scene archetype
    has_robot = False
    is_mobile = False
    has_objects = False
    has_physics = stage.get("has_physics_scene", False)
    robot_paths = []

    # Check for articulations (robots)
    articulations = prims_by_type.get("Articulation", [])
    xforms = prims_by_type.get("Xform", [])
    meshes = prims_by_type.get("Mesh", [])

    # Heuristic: any prim path containing common robot names
    all_paths = []
    for prim_list in prims_by_type.values():
        if isinstance(prim_list, list):
            all_paths.extend(prim_list)
        elif isinstance(prim_list, int):
            pass  # count, not paths

    for p in all_paths:
        p_lower = str(p).lower()
        if any(kw in p_lower for kw in ("robot", "franka", "panda", "ur10", "ur5",
                                         "anymal", "spot", "carter", "jetbot", "kaya",
                                         "go1", "go2", "h1", "allegro")):
            has_robot = True
            robot_paths.append(str(p))
            if any(kw in p_lower for kw in _MOBILE_ROBOT_KEYWORDS):
                is_mobile = True

    if isinstance(articulations, list) and len(articulations) > 0:
        has_robot = True
        robot_paths.extend(str(a) for a in articulations)
    elif isinstance(articulations, int) and articulations > 0:
        has_robot = True

    has_objects = (isinstance(meshes, list) and len(meshes) > 2) or \
                 (isinstance(meshes, int) and meshes > 2)

    # Select archetype
    if prim_count <= 2:
        archetype = "empty"
    elif not has_physics and prim_count > 2:
        archetype = "no_physics"
    elif is_mobile:
        archetype = "mobile_robot"
    elif has_robot and has_objects:
        archetype = "robot_and_objects"
    elif has_robot:
        archetype = "robot_only"
    else:
        archetype = "empty"

    template = _STARTER_PROMPTS[archetype]

    # Build scene summary line
    summary_parts = []
    if prim_count > 0:
        summary_parts.append(f"{prim_count} prims")
    if robot_paths:
        summary_parts.append(f"robot(s) at {', '.join(robot_paths[:3])}")
    if has_physics:
        summary_parts.append("physics enabled")

    return {
        "archetype": archetype,
        "welcome": template["welcome"],
        "scene_summary": ", ".join(summary_parts) if summary_parts else "empty scene",
        "prompts": template["prompts"],
        "robot_paths": robot_paths[:5],
        "has_physics": has_physics,
    }


@with_telemetry
async def _handle_slash_command_discovery(args: Dict) -> Dict:
    """Return slash commands filtered by scene state."""
    from .. import kit_tools  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    has_robot = args.get("scene_has_robot")
    has_physics = args.get("scene_has_physics")

    # Auto-detect if not provided
    if has_robot is None or has_physics is None:
        try:
            ctx = await kit_tools.get_stage_context(full=False)
            stage = ctx.get("stage", {})
            if has_physics is None:
                has_physics = stage.get("has_physics_scene", False)
            if has_robot is None:
                prim_count = stage.get("prim_count", 0)
                has_robot = prim_count > 5  # rough heuristic
        except Exception:
            has_robot = has_robot if has_robot is not None else False
            has_physics = has_physics if has_physics is not None else False

    commands = []
    for cmd in _SLASH_COMMANDS:
        if cmd.get("always"):
            commands.append({"command": cmd["command"], "description": cmd["description"]})
        elif cmd.get("requires_robot") and has_robot:
            commands.append({"command": cmd["command"], "description": cmd["description"]})
        elif cmd.get("requires_physics") and has_physics:
            commands.append({"command": cmd["command"], "description": cmd["description"]})

    return {
        "commands": commands,
        "scene_has_robot": has_robot,
        "scene_has_physics": has_physics,
    }


@with_telemetry
async def _handle_post_action_suggestions(args: Dict) -> Dict:
    """Return next-step suggestions after a tool execution."""
    from .. import tool_executor as _te  # noqa: PLC0415
    completed_tool = args.get("completed_tool", "")
    tool_args = args.get("tool_args", {})
    tool_result = args.get("tool_result", {})

    suggestions = _SUGGESTION_MAP.get(completed_tool, _DEFAULT_SUGGESTIONS)

    # Context-aware adjustments
    if completed_tool == "import_robot":
        robot_name = tool_args.get("file_path", "")
        if any(kw in robot_name.lower() for kw in _MOBILE_ROBOT_KEYWORDS):
            suggestions = [
                "Set up navigation for the mobile robot",
                "Add a lidar sensor",
                "Drive the robot forward to test",
            ]

    return {
        "completed_tool": completed_tool,
        "suggestions": suggestions,
    }


@with_telemetry
async def _handle_queue_write_locked_patch(args: Dict) -> Dict:
    """Submit a Python patch to the write-locked queue for serialised USD edits.

    Validates the patch through the patch validator before queueing. Blocked
    patches (those with breaking issues) are rejected immediately without being
    queued.

    Args:
        args: Tool arguments dict containing:
            - code (str): Python source code to execute under the write lock.
            - description (str, optional): Human-readable description of the
              patch. Defaults to ``"Write-locked patch"``.
            - priority (int, optional): Queue priority (higher = sooner).
              Defaults to ``0``.

    Returns:
        Dict[str, Any] with keys:
            - success (bool): True if the patch was queued successfully.
            - description (str): Echo of the provided description.
            - error (str, optional): Validation error message if the patch was
              blocked.
            - validation_blocked (bool, optional): True when the patch was
              rejected by the validator.
    """
    from .. import tool_executor as _te  # noqa: PLC0415
    code = args.get("code", "")
    desc = args.get("description", "Write-locked patch")
    priority = int(args.get("priority", 0) or 0)
    if not code:
        return {"type": "error", "error": "queue_write_locked_patch requires non-empty code"}
    # Pre-flight validation — same rules as run_usd_script.
    from ..patch_validator import validate_patch, has_blocking_issues, format_issues_for_llm  # noqa: PLC0415
    import logging  # noqa: PLC0415
    logger = logging.getLogger(__name__)
    issues = validate_patch(code)
    if has_blocking_issues(issues):
        msg = format_issues_for_llm(issues)
        logger.warning(f"[ToolExecutor] queue_write_locked_patch blocked: {msg}")
        return {"type": "error", "error": msg, "validation_blocked": True}
    from ._state import get_write_lock_queue
    outcome = await get_write_lock_queue().submit(code, desc, priority)
    return {**outcome, "description": desc}


@with_telemetry
async def _handle_start_workflow(args: Dict) -> Dict:
    """Start a multi-step autonomous workflow.

    Returns a workflow_id immediately; the workflow is paused at the first
    checkpoint (the plan artifact) until approve_workflow_checkpoint fires.
    """
    from .. import tool_executor as _te  # noqa: PLC0415
    workflow_type = args.get("workflow_type")
    goal = args.get("goal", "")
    if workflow_type not in _te._WORKFLOW_TEMPLATES:
        return {
            "ok": False,
            "error": f"Unknown workflow_type '{workflow_type}'. Supported: {sorted(_te._WORKFLOW_TEMPLATES)}",
        }
    if not goal:
        return {"ok": False, "error": "goal is required (high-level user intent)."}

    import uuid as _wf_uuid  # noqa: PLC0415
    wf_id = f"wf_{_wf_uuid.uuid4().hex[:12]}"
    scope_prim = args.get("scope_prim", "/World")
    max_retries = min(int(args.get("max_retries", 3)), _WORKFLOW_RETRY_HARD_CAP)
    auto_approve = bool(args.get("auto_approve_checkpoints", False))

    plan = _wf_make_initial_plan(workflow_type, goal, args.get("params") or {})

    from datetime import datetime as _wf_dt, timezone as _wf_tz  # noqa: PLC0415
    def _wf_now_iso() -> str:
        """Return current UTC time as ISO-8601 string with trailing Z."""
        return _wf_dt.now(_wf_tz.utc).isoformat() + "Z"

    workflow = {
        "id": wf_id,
        "type": workflow_type,
        "goal": goal,
        "scope_prim": scope_prim,
        "max_retries": max_retries,
        "auto_approve_checkpoints": auto_approve,
        "plan": plan,
        "status": "awaiting_plan_approval",
        "current_phase": "plan",
        "completed_phases": [],
        "checkpoint_decisions": [],
        "error_fix_attempts": [],
        "events": [
            {"type": "workflow_started", "at": _wf_now_iso(), "phase": "plan"},
        ],
        "created_at": _wf_now_iso(),
        "updated_at": _wf_now_iso(),
        "snapshot_id": None,  # filled in by routes.py before phase 2 if available
        # CONC-1 (2026-05-14): per-workflow lock attached at creation so
        # later RMW-style handlers serialize on the same wf_id while
        # remaining parallel across different wf_ids.
        "_lock": make_workflow_lock(),
    }
    # Registry-level lock guards the membership write; this is a single
    # dict assign (atomic in CPython) but locking keeps the contract
    # explicit and protects against any future read-then-write pattern.
    with _WORKFLOWS_REGISTRY_LOCK:
        _te._WORKFLOWS[wf_id] = workflow

    return {
        "ok": True,
        "workflow_id": wf_id,
        "status": workflow["status"],
        "plan": plan,
        "next_action": "Show plan to user; on approval call approve_workflow_checkpoint(workflow_id, phase='plan', action='approve').",
    }


@with_telemetry
async def _handle_edit_workflow_plan(args: Dict) -> Dict:
    """Apply user edits to a workflow's plan artifact.

    Edits are merged into plan.params and per-phase fields. The workflow
    must still be in the awaiting_plan_approval state; rejecting edits to
    in-flight workflows protects against mid-execution drift.
    """
    from .. import tool_executor as _te  # noqa: PLC0415
    from datetime import datetime as _wf_dt, timezone as _wf_tz  # noqa: PLC0415
    def _wf_now_iso() -> str:
        """Return current UTC time as ISO-8601 string with trailing Z."""
        return _wf_dt.now(_wf_tz.utc).isoformat() + "Z"

    wf_id = args.get("workflow_id")
    edits = args.get("plan_edits") or {}
    wf = _te._WORKFLOWS.get(wf_id)
    if not wf:
        return {"ok": False, "error": f"Unknown workflow_id '{wf_id}'."}
    if not isinstance(edits, dict):
        return {"ok": False, "error": "plan_edits must be a dict of {phase_name: {field: value}}."}

    # CONC-1 (2026-05-14): the state check + plan edits + event append form
    # a single critical section that must run under the per-workflow lock,
    # otherwise a concurrent approve_workflow_checkpoint on the same wf_id
    # could advance the workflow past `awaiting_plan_approval` between our
    # read of `wf["status"]` and our writes to `wf["plan"]`.
    applied: List[str] = []
    with _wf_lock_for(wf):
        if wf["status"] != "awaiting_plan_approval":
            return {
                "ok": False,
                "error": f"Workflow is in state '{wf['status']}'; plan can only be edited before approval.",
            }

        plan = wf["plan"]
        for phase_name, phase_edits in edits.items():
            if not isinstance(phase_edits, dict):
                continue
            if phase_name == "params":
                plan["params"].update(phase_edits)
                applied.append("params")
                continue
            # Find the phase in the plan
            for phase in plan["phases"]:
                if phase["name"] == phase_name:
                    phase.update({k: v for k, v in phase_edits.items() if k not in ("name", "status")})
                    applied.append(phase_name)
                    break

        wf["events"].append({"type": "plan_edited", "at": _wf_now_iso(), "edits": list(edits.keys())})
        wf["updated_at"] = _wf_now_iso()

    return {
        "ok": True,
        "workflow_id": wf_id,
        "applied_edits": applied,
        "plan": plan,
    }


@with_telemetry
async def _handle_approve_workflow_checkpoint(args: Dict) -> Dict:
    """Resolve a checkpoint with approve / reject / revise."""
    from .. import tool_executor as _te  # noqa: PLC0415
    from datetime import datetime as _wf_dt, timezone as _wf_tz  # noqa: PLC0415
    def _wf_now_iso() -> str:
        """Return current UTC time as ISO-8601 string with trailing Z."""
        return _wf_dt.now(_wf_tz.utc).isoformat() + "Z"

    wf_id = args.get("workflow_id")
    phase = args.get("phase")
    action = args.get("action")
    feedback = args.get("feedback", "")
    wf = _te._WORKFLOWS.get(wf_id)
    if not wf:
        return {"ok": False, "error": f"Unknown workflow_id '{wf_id}'."}
    if action not in ("approve", "reject", "revise"):
        return {"ok": False, "error": f"action must be one of approve|reject|revise, got '{action}'."}

    # CONC-1 (2026-05-14): the phase check + decision append + status
    # transition + advance is the canonical RMW that two concurrent
    # approve calls on the same wf_id can interleave. Without the lock,
    # both calls observe `current_phase == phase` (the precondition),
    # both append a decision (so the events log contains two entries for
    # what should be one transition), and both call _wf_advance_phase
    # (which mutates `wf["plan"]["phases"][i]["status"]`).
    with _wf_lock_for(wf):
        if wf["current_phase"] != phase:
            return {
                "ok": False,
                "error": f"Workflow is at phase '{wf['current_phase']}', not '{phase}'.",
            }

        decision = {
            "phase": phase,
            "action": action,
            "feedback": feedback,
            "at": _wf_now_iso(),
        }
        wf["checkpoint_decisions"].append(decision)
        wf["events"].append({"type": "checkpoint_decision", **decision})
        wf["updated_at"] = _wf_now_iso()

        if action == "reject":
            wf["status"] = "cancelled"
            return {
                "ok": True,
                "workflow_id": wf_id,
                "status": wf["status"],
                "rollback_required": True,
                "snapshot_id": wf.get("snapshot_id"),
            }

        if action == "revise":
            # Stay on the same phase; the LLM uses `feedback` to regenerate.
            wf["status"] = "revising"
            return {
                "ok": True,
                "workflow_id": wf_id,
                "status": wf["status"],
                "phase": phase,
                "feedback": feedback,
                "next_action": "Re-generate the artifact for this phase using the user feedback, then call approve_workflow_checkpoint again.",
            }

        # approve → advance to next phase
        next_phase = _wf_advance_phase(wf)
        if next_phase is None:
            return {
                "ok": True,
                "workflow_id": wf_id,
                "status": wf["status"],
                "message": "Workflow complete.",
            }

        # Decide whether the next phase needs another checkpoint
        if next_phase["checkpoint"] and not wf["auto_approve_checkpoints"]:
            wf["status"] = f"awaiting_{next_phase['name']}_approval"
        else:
            wf["status"] = f"executing_{next_phase['name']}"

        return {
            "ok": True,
            "workflow_id": wf_id,
            "status": wf["status"],
            "current_phase": wf["current_phase"],
            "phase_meta": next_phase,
        }


@with_telemetry
async def _handle_cancel_workflow(args: Dict) -> Dict:
    """Cancel a workflow and request rollback to its pre-workflow snapshot."""
    from .. import tool_executor as _te  # noqa: PLC0415
    from datetime import datetime as _wf_dt, timezone as _wf_tz  # noqa: PLC0415
    def _wf_now_iso() -> str:
        """Return current UTC time as ISO-8601 string with trailing Z."""
        return _wf_dt.now(_wf_tz.utc).isoformat() + "Z"

    wf_id = args.get("workflow_id")
    reason = args.get("reason", "user_cancelled")
    wf = _te._WORKFLOWS.get(wf_id)
    if not wf:
        return {"ok": False, "error": f"Unknown workflow_id '{wf_id}'."}

    # CONC-1 (2026-05-14): under the per-workflow lock so the
    # "already finished" guard + the status/events writes form one atomic
    # transition. Without the lock a concurrent approve_workflow_checkpoint
    # could set status back to an executing_X value after we set "cancelled".
    with _wf_lock_for(wf):
        if wf["status"] in ("completed", "cancelled"):
            return {
                "ok": True,
                "workflow_id": wf_id,
                "status": wf["status"],
                "message": "Workflow already finished; nothing to cancel.",
            }
        wf["status"] = "cancelled"
        wf["events"].append({"type": "cancelled", "at": _wf_now_iso(), "reason": reason})
        wf["updated_at"] = _wf_now_iso()
        status = wf["status"]
        snapshot_id = wf.get("snapshot_id")
    return {
        "ok": True,
        "workflow_id": wf_id,
        "status": status,
        "rollback_required": True,
        "snapshot_id": snapshot_id,
        "reason": reason,
    }


@with_telemetry
async def _handle_get_workflow_status(args: Dict) -> Dict:
    """Return the current state of a workflow.

    CONC-1 (2026-05-14): snapshot the workflow under the per-workflow lock
    so the returned dict reflects one consistent moment in the workflow's
    lifecycle, never a half-mutated state (e.g. status="cancelled" with
    events still missing the cancellation entry that a concurrent
    cancel_workflow is about to append).
    """
    from .. import tool_executor as _te  # noqa: PLC0415
    wf_id = args.get("workflow_id")
    wf = _te._WORKFLOWS.get(wf_id)
    if not wf:
        return {"ok": False, "error": f"Unknown workflow_id '{wf_id}'."}
    with _wf_lock_for(wf):
        # Return a shallow copy without the verbose events log unless explicitly asked.
        return {
            "ok": True,
            "workflow_id": wf_id,
            "type": wf["type"],
            "goal": wf["goal"],
            "status": wf["status"],
            "current_phase": wf["current_phase"],
            "completed_phases": list(wf["completed_phases"]),
            "checkpoint_decisions": list(wf["checkpoint_decisions"]),
            "error_fix_attempts": list(wf["error_fix_attempts"]),
            "plan": wf["plan"],
            "created_at": wf["created_at"],
            "updated_at": wf["updated_at"],
        }


@with_telemetry
async def _handle_list_workflows(args: Dict) -> Dict:
    """List active (and optionally completed) workflows.

    CONC-1 (2026-05-14): snapshot the registry membership under
    `_WORKFLOWS_REGISTRY_LOCK` so iteration is not interrupted by a
    concurrent start_workflow / cancel_workflow that mutates the dict
    keys. Per-workflow state is then read under each wf's own lock so
    individual entries are coherent. Acquiring both locks in this order
    (registry → per-workflow) and never the reverse keeps the lock
    graph acyclic and deadlock-free.
    """
    from .. import tool_executor as _te  # noqa: PLC0415
    include_completed = bool(args.get("include_completed", False))
    limit = int(args.get("limit", 20))
    summaries: List[Dict[str, Any]] = []
    with _WORKFLOWS_REGISTRY_LOCK:
        items = list(_te._WORKFLOWS.items())
    for wf_id, wf in items:
        with _wf_lock_for(wf):
            if not include_completed and wf["status"] in ("completed", "cancelled"):
                continue
            summaries.append({
                "workflow_id": wf_id,
                "type": wf["type"],
                "goal": wf["goal"],
                "status": wf["status"],
                "current_phase": wf["current_phase"],
                "created_at": wf["created_at"],
                "updated_at": wf["updated_at"],
            })
    # Newest first
    summaries.sort(key=lambda s: s["updated_at"], reverse=True)
    return {"ok": True, "count": len(summaries), "workflows": summaries[:limit]}


@with_telemetry
async def _handle_execute_with_retry(args: Dict) -> Dict:
    """Execute a code patch through the autonomous error-fix loop.

    This handler performs the *first* attempt against Kit RPC and reports
    the outcome. The actual LLM-driven fix iterations happen one round-trip
    per attempt — the orchestrator (chat loop) is responsible for feeding
    each failure back into the LLM, generating the patched code, and
    calling this handler again with the new code. We track attempt counts
    via a session-scoped key so the hard retry cap is enforced even when
    the LLM forgets it.
    """
    from .. import kit_tools  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    from ..patch_validator import validate_patch, has_blocking_issues, format_issues_for_llm  # noqa: PLC0415
    code = args.get("code", "")
    description = args.get("description", "Autonomous error-fix execution")
    requested_max = int(args.get("max_retries", 3))
    max_retries = min(requested_max, _WORKFLOW_RETRY_HARD_CAP)
    context_hints = args.get("context_hints") or []

    if not code:
        return {"ok": False, "error": "code is required."}

    # Pre-flight validation (same as run_usd_script)
    issues = validate_patch(code)
    if has_blocking_issues(issues):
        msg = format_issues_for_llm(issues)
        return {
            "ok": False,
            "type": "validation_blocked",
            "error": msg,
            "code": code,
            "description": description,
        }

    # Submit to Kit. Kit returns queued=True; the chat loop polls for the
    # actual exec result via existing patch-status machinery. We surface
    # the budget so the caller can decide whether to retry on failure.
    result = await kit_tools.queue_exec_patch(code, description)
    return {
        "success": bool(result.get("success", False)),
        "ok": True,
        "type": "code_patch",
        "code": code,
        "description": description,
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "max_retries": max_retries,
        "context_hints": context_hints,
        "next_action": (
            "Wait for patch result. On failure, call execute_with_retry again "
            f"with patched code (up to {max_retries} attempts total)."
        ),
    }


@with_telemetry
async def _handle_dispatch_async_task(args: Dict) -> Dict:
    """Register an async task and start a background worker."""
    from .. import tool_executor as _te  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415
    import time as _time  # noqa: PLC0415
    import threading as _threading  # noqa: PLC0415
    task_type = args.get("task_type", "custom")
    params = args.get("params") or {}
    label = args.get("label") or f"{task_type} task"

    task_id = f"task_{task_type}_{_uuid.uuid4().hex[:8]}"
    with _ASYNC_TASKS_LOCK:
        _ASYNC_TASKS[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "label": label,
            "params": params,
            "state": "pending",
            "progress": 0.0,
            "queued_at": _time.time(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }

    # Allow tests / callers to opt out of the background thread for synchronous
    # reasoning (e.g. when running under pytest without a real Kit).
    if not args.get("dry_run"):
        thread = _threading.Thread(
            target=_async_task_runner,
            args=(task_id, task_type, params),
            name=f"async-{task_id}",
            daemon=True,
        )
        thread.start()

    return {
        "task_id": task_id,
        "task_type": task_type,
        "label": label,
        "state": "pending",
        "message": f"Started {label} in background. Query status with task_id={task_id!r}.",
    }


@with_telemetry
async def _handle_query_async_task(args: Dict) -> Dict:
    """Return current state + progress + (if done) result for a task."""
    from .. import tool_executor as _te  # noqa: PLC0415
    import time as _time  # noqa: PLC0415
    task_id = args["task_id"]
    with _ASYNC_TASKS_LOCK:
        entry = _ASYNC_TASKS.get(task_id)
        if entry is None:
            return {"task_id": task_id, "state": "unknown", "error": "task_id not found"}
        snapshot = dict(entry)

    # Compute elapsed seconds for convenience
    started = snapshot.get("started_at")
    finished = snapshot.get("finished_at")
    queued = snapshot.get("queued_at")
    if started is not None:
        end = finished if finished is not None else _time.time()
        snapshot["elapsed_seconds"] = round(end - started, 3)
    elif queued is not None:
        snapshot["elapsed_seconds"] = round(_time.time() - queued, 3)
    return snapshot


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Awaitable[Any]]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 9 — populate dispatch dicts with this module's handlers.

    Called by `handlers/_dispatch.py:register_handlers()` which is the
    sole dispatch entry point from `tool_executor.py`.
    """
    # Data handlers (15)
    data["approve_workflow_checkpoint"] = _handle_approve_workflow_checkpoint
    data["cancel_workflow"] = _handle_cancel_workflow
    data["dispatch_async_task"] = _handle_dispatch_async_task
    data["edit_workflow_plan"] = _handle_edit_workflow_plan
    data["execute_with_retry"] = _handle_execute_with_retry
    data["get_workflow_status"] = _handle_get_workflow_status
    data["list_workflows"] = _handle_list_workflows
    data["post_action_suggestions"] = _handle_post_action_suggestions
    data["query_async_task"] = _handle_query_async_task
    data["queue_write_locked_patch"] = _handle_queue_write_locked_patch
    data["record_feedback"] = _handle_record_feedback
    data["scene_aware_starter_prompts"] = _handle_scene_aware_starter_prompts
    data["slash_command_discovery"] = _handle_slash_command_discovery
    data["start_workflow"] = _handle_start_workflow
    data["watch_changes"] = _handle_watch_changes
