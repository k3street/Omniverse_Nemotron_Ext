"""Run one multi-turn QA session: persona (Claude Code subprocess) ↔ Isaac Assist (FastAPI).

Usage:
    python -m scripts.qa.multi_turn_session --persona 01_maya --task M-01

Writes JSONL transcript to workspace/qa_runs/<run_id>/<persona>__<task>.jsonl.
Each message (persona out, assistant back) is a JSONL event. End conditions:
  - persona emits a give-up phrase
  - MAX_TURNS hit
  - persona subprocess fails repeatedly
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from scripts.qa.build_session_prompt import (
    REPO_ROOT,
    build_session_prompt,
    random_modifiers,
    Modifiers,
)

ISAAC_ASSIST_URL = "http://127.0.0.1:8000/api/v1/chat/message"
KIT_RPC_EXEC = "http://127.0.0.1:8001/exec_sync"
MAX_TURNS = 20
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")


_SNAPSHOT_CODE = """
import omni.usd, json as _json
from pxr import Usd, UsdGeom, UsdPhysics

ctx = omni.usd.get_context()
stage = ctx.get_stage()
if stage is None:
    print(_json.dumps({"error": "no_stage"}))
else:
    prims_info = []
    joints_info = {}
    transforms_info = {}
    rotations_info = {}
    scales_info = {}
    geom_info = {}
    for p in stage.Traverse():
        path = str(p.GetPath())
        if path.startswith(("/Render", "/OmniverseKit", "/OmniKit_Environment")):
            continue
        ptype = str(p.GetTypeName())
        entry = {"path": path, "type": ptype}
        # API schemas applied (Collision, RigidBody, Articulation, Mass, etc.)
        try:
            schemas = [s for s in (p.GetAppliedSchemas() or []) if s]
            if schemas:
                entry["apis"] = schemas
        except Exception:
            pass
        # Visibility — token 'invisible' / 'inherited'. Only record when it's
        # been authored (default is 'inherited'); visibility is a common
        # success-criterion attribute (e.g. FX-03 bulk-hide).
        try:
            vis_attr = p.GetAttribute('visibility')
            if vis_attr and vis_attr.IsValid() and vis_attr.IsAuthored():
                entry['visibility'] = str(vis_attr.Get())
        except Exception:
            pass
        # Named physics / drive attributes — the prim-type + api list surfaces
        # "this has PhysxSceneAPI" but the attribute VALUES (solverType,
        # timeStepsPerSecond, etc.) only land in the snapshot if we read them
        # explicitly. Without this, tasks like "configure deterministic mode"
        # produce a stage-identical snapshot from the judge's perspective
        # even when the tool authored the right attributes (verified on
        # /World/PhysicsScene after enable_deterministic_mode).
        try:
            _named = []
            if ptype == 'PhysicsScene':
                _named = [
                    'physxScene:solverType',
                    'physxScene:enableGPUDynamics',
                    'physxScene:enableCCD',
                    'physxScene:timeStepsPerSecond',
                    'physics:gravityMagnitude',
                    'physics:gravityDirection',
                ]
            elif ptype in ('PhysicsRevoluteJoint', 'PhysicsPrismaticJoint'):
                _named = [
                    'physics:lowerLimit',
                    'physics:upperLimit',
                    'drive:angular:physics:stiffness',
                    'drive:angular:physics:damping',
                    'drive:angular:physics:targetPosition',
                    'drive:linear:physics:stiffness',
                    'drive:linear:physics:damping',
                ]
            # MassAPI attrs — captured on any prim with the API applied,
            # not gated by prim type (can live on Cube, Mesh, Xform).
            # Separate list because it applies broadly.
            _mass_named = []
            if 'PhysicsMassAPI' in (schemas or []):
                _mass_named = [
                    'physics:mass',
                    'physics:density',
                    'physics:centerOfMass',
                    'physics:diagonalInertia',
                ]
            # Merge the type-keyed + api-keyed lists
            _named = list(_named) + _mass_named
            if _named:
                extras = {}
                for name in _named:
                    try:
                        a = p.GetAttribute(name)
                        if a and a.IsValid() and a.IsAuthored():
                            v = a.Get()
                            # Serialize Gf types (Vec3f, etc.) to list for JSON
                            if hasattr(v, '__len__') and not isinstance(v, str):
                                v = [round(float(x), 4) for x in v]
                            elif isinstance(v, float):
                                v = round(v, 4)
                            extras[name] = v
                    except Exception:
                        continue
                if extras:
                    entry['attrs'] = extras
        except Exception:
            pass
        # Material binding — captured for any prim that has a direct
        # material:binding relationship. Surfaces WHICH material each
        # prim is bound to so tasks like P-12 (per-apple material
        # instancing) can be stage-verified without sampling via
        # list_relationships. Only the default-purpose binding is
        # captured; full-inheritance chain is out of scope.
        try:
            from pxr import UsdShade
            binding_api = UsdShade.MaterialBindingAPI(p)
            if binding_api:
                mat = binding_api.ComputeBoundMaterial()[0]
                if mat:
                    mat_path = str(mat.GetPath())
                    if mat_path:
                        entry['material_binding'] = mat_path
        except Exception:
            pass
        # Variant selections — { variant_set: active_variant } dict.
        # AD-22 ("is variant 'red' active on /World/Car?") becomes
        # snapshot-verifiable without relying on judge reading list_variants
        # output in the reply prose.
        try:
            vsets = p.GetVariantSets()
            if vsets and vsets.GetNames():
                _vmap = {}
                for vname in vsets.GetNames():
                    try:
                        vs = vsets.GetVariantSet(vname)
                        _vmap[vname] = str(vs.GetVariantSelection())
                    except Exception:
                        pass
                if _vmap:
                    entry['variants'] = _vmap
        except Exception:
            pass
        # Semantic label via Semantics.SemanticsAPI. AD-20 ("is /World/X
        # tagged with 'obstacle'?") becomes snapshot-verifiable.
        try:
            from pxr import Semantics
            _sem_data = None
            try:
                instances = Semantics.SemanticsAPI.GetAll(p) if hasattr(
                    Semantics.SemanticsAPI, 'GetAll'
                ) else []
            except Exception:
                instances = []
            for inst in instances or []:
                try:
                    d = inst.GetSemanticDataAttr().Get()
                    if d:
                        _sem_data = str(d)
                        break
                except Exception:
                    continue
            if _sem_data:
                entry['semantic_class'] = _sem_data
        except Exception:
            pass
        # References — paths of authored references on the prim. Lets
        # the judge verify "is /World/X referenced from Y?" claims
        # (AD-23) directly from snapshot rather than needing
        # list_references probes.
        try:
            refs = p.GetReferences()
            if refs and p.HasAuthoredReferences():
                # GetMetadata returns the referenceList op; iterate
                # authored items for their asset paths.
                _ref_list = []
                try:
                    meta = p.GetMetadata('references')
                    if meta:
                        for item in (meta.GetAddedOrExplicitItems() or []):
                            ap = str(item.assetPath) if hasattr(item, 'assetPath') else str(item)
                            if ap:
                                _ref_list.append(ap)
                except Exception:
                    pass
                if _ref_list:
                    entry['references'] = _ref_list[:5]  # cap for compactness
                else:
                    # Even if we can't enumerate exactly, record the
                    # boolean HasAuthoredReferences so the judge can
                    # distinguish "no refs" from "unknown refs".
                    entry['has_refs'] = True
        except Exception:
            pass
        prims_info.append(entry)

        # World transform for all Xformables (position + rotation + scale ground-truth)
        try:
            xf = UsdGeom.Xformable(p)
            if xf:
                wt = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                tr = wt.ExtractTranslation()
                transforms_info[path] = [round(float(tr[0]),4), round(float(tr[1]),4), round(float(tr[2]),4)]
                # Extract scale from row lengths FIRST so we can strip it out
                # before reading rotation. ExtractRotationQuat on a matrix that
                # still contains non-uniform scale produces a non-unit quaternion
                # that looks like a mutated rotation — verified: for a Cylinder
                # with scale=(0.2,0.2,0.5) + rotate=(-90,0,0), the un-normalized
                # quaternion read was [0.55, -0.32, 0, 0] instead of the correct
                # [0.707, -0.707, 0, 0].
                try:
                    sx = wt.GetRow(0).GetLength()
                    sy = wt.GetRow(1).GetLength()
                    sz = wt.GetRow(2).GetLength()
                    if abs(sx-1.0) > 1e-4 or abs(sy-1.0) > 1e-4 or abs(sz-1.0) > 1e-4:
                        scales_info[path] = [round(float(sx),4), round(float(sy),4), round(float(sz),4)]
                except Exception:
                    sx = sy = sz = 1.0

                try:
                    # Orthonormalize the matrix before extracting rotation —
                    # GetOrthonormalized returns a pure-rotation copy, stripping
                    # out scale. ExtractRotationQuat on the raw scaled matrix
                    # returns a non-unit quaternion that misrepresents the
                    # actual rotation.
                    _R = wt.GetOrthonormalized()
                    q = _R.ExtractRotationQuat()
                    rotations_info[path] = [round(float(q.GetReal()), 4),
                                            round(float(q.GetImaginary()[0]), 4),
                                            round(float(q.GetImaginary()[1]), 4),
                                            round(float(q.GetImaginary()[2]), 4)]
                except Exception:
                    pass
        except Exception:
            pass

        # Type-specific geometric attributes for primitive shapes.
        # Includes both the raw USD attribute AND the effective (scale-adjusted)
        # dimension, because agents often use scale=0.2 to make a 0.2m sphere
        # rather than setting the radius attribute. Judges need the effective
        # value to avoid false-fabrication rulings.
        try:
            _scale_vec = scales_info.get(path) or [1.0, 1.0, 1.0]
            _sx, _sy, _sz = _scale_vec
            if p.IsA(UsdGeom.Cube):
                v = UsdGeom.Cube(p).GetSizeAttr().Get()
                if v is not None:
                    # Cube's 'size' is edge length; effective edge = size*scale
                    # (use mean when scales are non-uniform so users get one number)
                    geom_info[path] = {
                        "size": round(float(v), 4),
                        "effective_size_xyz": [round(float(v) * _sx, 4),
                                               round(float(v) * _sy, 4),
                                               round(float(v) * _sz, 4)],
                    }
            elif p.IsA(UsdGeom.Sphere):
                v = UsdGeom.Sphere(p).GetRadiusAttr().Get()
                if v is not None:
                    # Sphere.radius is uniform; under non-uniform scale the shape
                    # is an ellipsoid and judges should compare all three axes.
                    geom_info[path] = {
                        "radius": round(float(v), 4),
                        "effective_radius_xyz": [round(float(v) * _sx, 4),
                                                 round(float(v) * _sy, 4),
                                                 round(float(v) * _sz, 4)],
                    }
            elif p.IsA(UsdGeom.Cylinder):
                c = UsdGeom.Cylinder(p)
                r = float(c.GetRadiusAttr().Get() or 0.0)
                h = float(c.GetHeightAttr().Get() or 0.0)
                axis = str(c.GetAxisAttr().Get() or "Z")
                # Effective radius uses the scale of the two non-axis components;
                # height uses the scale along the axis direction.
                s_by_axis = {"X": _sx, "Y": _sy, "Z": _sz}
                eff_h = h * s_by_axis.get(axis, _sz)
                eff_r_a, eff_r_b = (
                    (r * _sy, r * _sz) if axis == "X"
                    else (r * _sx, r * _sz) if axis == "Y"
                    else (r * _sx, r * _sy)
                )
                geom_info[path] = {
                    "radius": round(r, 4),
                    "height": round(h, 4),
                    "axis": axis,
                    "effective_radius": [round(eff_r_a, 4), round(eff_r_b, 4)],
                    "effective_height": round(eff_h, 4),
                }
            elif p.IsA(UsdGeom.Cone):
                c = UsdGeom.Cone(p)
                r = float(c.GetRadiusAttr().Get() or 0.0)
                h = float(c.GetHeightAttr().Get() or 0.0)
                geom_info[path] = {
                    "radius": round(r, 4),
                    "height": round(h, 4),
                    "effective_radius_xy": [round(r * _sx, 4), round(r * _sy, 4)],
                    "effective_height": round(h * _sz, 4),
                }
            elif p.IsA(UsdGeom.Capsule):
                c = UsdGeom.Capsule(p)
                r = float(c.GetRadiusAttr().Get() or 0.0)
                h = float(c.GetHeightAttr().Get() or 0.0)
                geom_info[path] = {
                    "radius": round(r, 4),
                    "height": round(h, 4),
                    "effective_radius_xy": [round(r * _sx, 4), round(r * _sy, 4)],
                    "effective_height": round(h * _sz, 4),
                }
        except Exception:
            pass

        # Joint positions (Physics*Joint with physics:jointPosition attr)
        if p.IsA(UsdPhysics.RevoluteJoint) or p.IsA(UsdPhysics.PrismaticJoint) or p.IsA(UsdPhysics.Joint):
            try:
                attr = p.GetAttribute("physics:jointPosition")
                if attr and attr.IsAuthored():
                    joints_info[path] = round(float(attr.Get()), 4)
            except Exception:
                pass

    # Timeline state (playing? current frame?)
    timeline = {}
    try:
        import omni.timeline
        t = omni.timeline.get_timeline_interface()
        timeline = {
            "playing": t.is_playing(),
            "stopped": t.is_stopped(),
            "current_time": round(float(t.get_current_time()), 3),
            "start_time": round(float(t.get_start_time()), 3),
            "end_time": round(float(t.get_end_time()), 3),
        }
    except Exception as e:
        timeline = {"error": str(e)}

    # Active viewport + its camera
    cam = None
    try:
        import omni.kit.viewport.utility as _vp
        vp = _vp.get_active_viewport()
        cam = str(vp.camera_path) if vp else None
    except Exception:
        pass

    # Recent console errors (from Kit's message bus)
    errors = []
    try:
        import omni.log as _ol
        # Not all Kit builds expose log history; best-effort
        errors = []
    except Exception:
        pass

    print(_json.dumps({
        "prim_count": len(prims_info),
        "prims": prims_info[:80],
        "joint_positions": joints_info,
        "world_translations": transforms_info,
        "world_rotations_quat_wxyz": rotations_info,
        "world_scales": scales_info,
        "geometry": geom_info,
        "timeline": timeline,
        "active_camera": cam,
        "errors": errors,
    }, default=str))
"""


def _snapshot_stage() -> Dict[str, Any]:
    """Query Kit RPC for the current stage state (prim tree + active camera)."""
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(KIT_RPC_EXEC, json={"code": _SNAPSHOT_CODE})
            r.raise_for_status()
            data = r.json()
            out = (data.get("output") or "").strip().splitlines()
            # last non-empty line is the JSON payload
            for line in reversed(out):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        return json.loads(line)
                    except Exception:
                        pass
            return {"error": "no_snapshot_line", "raw": (data.get("output") or "")[:200]}
    except Exception as e:
        return {"error": f"snapshot_failed: {e}"}


def _reset_stage() -> Dict[str, Any]:
    """Open a fresh, empty stage in Isaac Sim before starting a session.

    `ctx.new_stage()` alone is not reliable in practice — the stage swap is
    event-driven in Kit, so a tool call on the same RPC channel can still
    observe the previous stage for a handful of ticks. Observed failure
    mode: `stage_reset_prims=11` prints the OLD stage's prim count even
    though the new stage appears empty to the immediate snapshot, and the
    agent's next tool call lands back on the stale stage. Symptom:
    'ghost prims' from a prior run appear in the final snapshot.

    Fix: keep the `new_stage()` call (it's cheap and clears metadata), then
    ALSO iterate and `RemovePrim` on every non-system prim to make the
    reset take effect synchronously in the current tick.
    """
    code = (
        "import omni.usd\n"
        "ctx = omni.usd.get_context()\n"
        "ctx.new_stage()\n"
        "stage = ctx.get_stage()\n"
        "# Belt + suspenders: force-clear everything authored.\n"
        "_removed = 0\n"
        "if stage is not None:\n"
        "    for p in list(stage.Traverse()):\n"
        "        path = str(p.GetPath())\n"
        "        if path in ('/', '/World'):\n"
        "            continue\n"
        "        if path.startswith(('/Render', '/OmniverseKit', '/OmniKit_Environment')):\n"
        "            continue\n"
        "        try:\n"
        "            if stage.RemovePrim(p.GetPath()):\n"
        "                _removed += 1\n"
        "        except Exception:\n"
        "            pass\n"
        "    # Re-assert /World as a plain Xform (some reset flows drop it).\n"
        "    from pxr import UsdGeom\n"
        "    UsdGeom.Xform.Define(stage, '/World')\n"
        "print(f'stage_reset removed={_removed} remaining_prims={len(list(stage.Traverse()))}')\n"
    )
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(KIT_RPC_EXEC, json={"code": code})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"success": False, "output": f"stage_reset_failed: {e}"}


_PRE_SESSION_SETUP_HEADER = "## Pre-session setup"


def _extract_pre_session_code(task_id: str) -> Optional[str]:
    """Read the '## Pre-session setup' fenced python block from the task spec.

    Tasks that need stage seeding (e.g. broken-scene diagnosis, pre-loaded
    robots for graph wiring) declare their setup directly in their .md spec.
    Keeping the fixture next to the spec means no harness change is needed
    when a new task is added — the harness just reads whatever is authored.
    """
    spec_path = REPO_ROOT / "docs" / "qa" / "tasks" / f"{task_id}.md"
    if not spec_path.exists():
        return None
    text = spec_path.read_text()
    if _PRE_SESSION_SETUP_HEADER not in text:
        return None
    tail = text.split(_PRE_SESSION_SETUP_HEADER, 1)[1]
    import re as _pre_re
    m = _pre_re.search(r"```(?:python|py)?\s*\n(.*?)```", tail, _pre_re.S)
    return m.group(1).strip() if m else None


def _apply_pre_session_setup(task_id: str) -> Dict[str, Any]:
    """Run task-declared stage-seeding code via Kit RPC before the session starts."""
    code = _extract_pre_session_code(task_id)
    if not code:
        return {"applied": False, "task_id": task_id}
    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(KIT_RPC_EXEC, json={"code": code})
            r.raise_for_status()
            data = r.json()
            data["applied"] = True
            data["task_id"] = task_id
            return data
    except Exception as e:
        return {"applied": False, "task_id": task_id, "error": str(e)}

# Give-up phrases / stage directions indicating the persona has disengaged.
import re as _qa_re
GIVE_UP_PATTERNS = [
    "i'll try the docs", "i'll try the forum", "i'll try discord",
    "i'll ask a colleague", "this isn't working", "not worth my time",
    "going to the docs", "going to read the docs", "docs it is",
    "pull the 5.x", "i'll pull the",
    "i'm out", "bye.", "walked away", "closing the chat", "no response",
    "session ended", "session's done", "session is done",
    # Success closers — the persona has what they need and is disengaging
    "got what i needed", "that's what i needed",
    # Casual farewells when they clearly close the conversation
    "later 👋", "later.", "cya", "peace out", "thanks bye",
]
# A bracketed or asterisked stage direction alone — e.g. "[session ended]", "*closes tab*"
_STAGE_DIRECTION_RE = _qa_re.compile(r"^\s*[\[\*][^\]\*]{1,200}[\]\*]\s*$")


def _is_give_up(text: str) -> bool:
    t = text.lower().strip()
    if _STAGE_DIRECTION_RE.match(t):
        return True
    # Give-up phrases must appear at the END of the message (the persona is
    # closing out), not mid-sentence. "later." inside "...work later..." is
    # a false positive; "bye." at end of the final message is real.
    tail = t[-80:]  # last ~80 chars only
    return any(p in tail for p in GIVE_UP_PATTERNS)


def _persona_next_message(prompt: str, timeout_s: int = 120) -> Dict[str, Any]:
    """Invoke `claude -p` and return parsed JSON result dict."""
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    try:
        data = json.loads(proc.stdout or "{}")
        return {"ok": proc.returncode == 0, "result": data.get("result", ""), "cost": data.get("total_cost_usd", 0.0), "raw": data}
    except json.JSONDecodeError:
        return {"ok": False, "result": proc.stdout, "cost": 0.0, "raw": None, "error": "json_decode"}


def _ask_isaac_assist(session_id: str, message: str, timeout_s: int = 600) -> Dict[str, Any]:
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(ISAAC_ASSIST_URL, json={"session_id": session_id, "message": message})
        r.raise_for_status()
        return r.json()


def _build_next_persona_prompt(base_prompt: str, conversation: List[Dict[str, str]]) -> str:
    """Append conversation history and request next persona message."""
    lines = [base_prompt, "\n=== Conversation so far ===\n"]
    for turn in conversation:
        role = "You (persona)" if turn["role"] == "user" else "Isaac Assist"
        lines.append(f"{role}: {turn['content']}\n")
    lines.append(
        "\n=== Your next message ===\n"
        "Write your next message to Isaac Assist. Stay in character. One message only. "
        "If the task succeeded or you've given up, emit your final in-character line "
        "(e.g. 'ok this isn't working, I'll try the docs')."
    )
    return "\n".join(lines)


def run_session(persona: str, task: str, runs_dir: Path, seed: Optional[int] = None) -> Dict[str, Any]:
    run_id = datetime.now().strftime("run_%Y%m%dT%H%M%S") + f"_{uuid.uuid4().hex[:6]}"
    out_dir = runs_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / f"{persona}__{task}.jsonl"

    if seed is not None:
        import random as _random
        _random.seed(seed)
    mods = random_modifiers(persona)
    base_prompt = build_session_prompt(persona_id=persona, task_id=task, modifiers=mods)

    def log(event: Dict[str, Any]) -> None:
        event["ts"] = time.time()
        with transcript_path.open("a") as f:
            f.write(json.dumps(event) + "\n")

    reset_result = _reset_stage()
    log({"event": "stage_reset", "result": reset_result})
    pre_setup = _apply_pre_session_setup(task)
    if pre_setup.get("applied"):
        log({"event": "pre_session_setup", "task": task, "result": pre_setup})
    log({"event": "stage_snapshot", "when": "initial", "snapshot": _snapshot_stage()})
    log({"event": "session_start", "persona": persona, "task": task, "modifiers": mods.as_dict()})

    session_id = f"qa_{run_id}"
    conversation: List[Dict[str, str]] = []
    total_cost = 0.0
    turn = 0

    while turn < MAX_TURNS:
        turn += 1
        prompt = base_prompt if turn == 1 else _build_next_persona_prompt(base_prompt, conversation)

        pres = _persona_next_message(prompt)
        total_cost += pres.get("cost", 0.0)
        persona_msg = (pres.get("result") or "").strip()
        log({"event": "persona_message", "turn": turn, "text": persona_msg, "cost": pres.get("cost", 0.0)})

        if not persona_msg:
            log({"event": "abort", "reason": "empty persona message"})
            break

        conversation.append({"role": "user", "content": persona_msg})

        if _is_give_up(persona_msg):
            log({"event": "session_end", "reason": "persona_gave_up", "turn": turn})
            break

        try:
            aa_reply = _ask_isaac_assist(session_id, persona_msg)
        except Exception as e:
            log({"event": "isaac_assist_error", "turn": turn, "error": str(e)})
            break

        content_parts = []
        for msg in aa_reply.get("response_messages", []):
            content_parts.append(msg.get("content", ""))
        assistant_msg = "\n".join(content_parts).strip()

        log({"event": "isaac_assist_reply", "turn": turn, "text": assistant_msg,
             "intent": aa_reply.get("intent"),
             "tool_calls": aa_reply.get("tool_calls", []),
             "actions_to_approve": aa_reply.get("actions_to_approve"),
             "sources_consulted": aa_reply.get("sources_consulted", [])})

        # Ground-truth snapshot AFTER each Assist reply — what actually changed
        # in the stage. Separates "Python ran" from "scene actually changed".
        log({"event": "stage_snapshot", "when": f"after_turn_{turn}",
             "snapshot": _snapshot_stage()})

        conversation.append({"role": "assistant", "content": assistant_msg})

    else:
        log({"event": "session_end", "reason": "max_turns_hit", "turn": turn})

    log({"event": "session_summary", "turns": turn, "total_persona_cost_usd": total_cost,
         "transcript": str(transcript_path)})
    return {"run_id": run_id, "transcript": str(transcript_path), "turns": turn, "cost": total_cost}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--persona", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--runs-dir", default=str(REPO_ROOT / "workspace" / "qa_runs"))
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    res = run_session(args.persona, args.task, Path(args.runs_dir), seed=args.seed)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
