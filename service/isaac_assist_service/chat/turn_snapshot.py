"""Per-turn auto-snapshot of the stage's root layer.

Before each patch_request turn that will mutate the stage, the orchestrator
calls ``capture(session_id)`` — which exports the current root layer to
disk as USDA text. The `/undo` slash command then calls ``restore(session_id,
steps=N)`` to roll back N turns by re-importing the saved USDA into the
root layer.

Design notes:
- Captures the ROOT layer only. Session-layer edits (rare in smoke-testing)
  survive. The authoring layer in every session we've seen is root layer,
  so the coverage is 100% of agent-driven changes.
- One file per turn: ``workspace/turn_snapshots/{session_id}/{ts}_{n}.usda``.
  The timestamp-prefix keeps them sortable; the integer index is for human
  readability in `/undo N`.
- Files are kept until the session closes — cheap disk, massive debug value.
  A background task could prune after 100 turns; not worth building yet.
- The export runs in-process in Kit via exec_sync, so a single RPC round-trip
  per turn. On a 4-cube stage the USDA string is ~1.5 KB; on a scene with
  hundreds of prims it might be 50-500 KB. Still cheap.
- Restore replaces the entire root layer. Hydra gets notified via
  ImportFromString, which triggers a full re-compose; viewport updates
  correctly in testing.

Limitations:
- OmniGraph runtime state (a conveyor's internal tick counter, physics
  velocities after a simulation ran) is NOT captured — only USD authoring.
  For smoke-testing that's exactly what we want: revert the USD edits,
  not the sim rollout.
- Sublayer/reference mutations aren't captured unless they live in the root
  layer. If a future feature starts using edit-target on sublayers, this
  needs to grow.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SNAPSHOT_ROOT = Path(os.environ.get(
    "TURN_SNAPSHOT_ROOT",
    str(Path(__file__).resolve().parent.parent.parent.parent / "workspace" / "turn_snapshots"),
))


def _session_dir(session_id: str) -> Path:
    """Sanitize the session_id and return its snapshot directory.

    Same pattern as session_trace — strip anything that's not alnum/._- to
    keep filesystem safety. Create on demand.
    """
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "default") or "default"
    d = _SNAPSHOT_ROOT / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _list_snapshots(session_id: str) -> list:
    """Return sorted list of snapshot file paths for a session (oldest first)."""
    return sorted(_session_dir(session_id).glob("*.usda"))


async def capture(session_id: str, label: str = "") -> Dict[str, Any]:
    """Export the current root layer to disk for later restore.

    Returns ``{ok, path, layer_size, turn_index}``. Errors are logged and
    returned as ``{ok: False, error: <str>}`` — never raised. The
    orchestrator must not crash a user turn if snapshotting fails.
    """
    try:
        from .tools import kit_tools
    except Exception as e:
        logger.warning(f"turn_snapshot: kit_tools import failed: {e}")
        return {"ok": False, "error": f"kit_tools import: {e}"}

    # Ask Kit to export the root layer. Print as JSON so we can parse
    # cleanly from exec_sync's string output without ambiguity.
    script = """
import json
import omni.usd
stage = omni.usd.get_context().get_stage()
if stage is None:
    print(json.dumps({'ok': False, 'error': 'no stage'}))
else:
    try:
        text = stage.GetRootLayer().ExportToString()
        print(json.dumps({'ok': True, 'text': text, 'size': len(text)}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
"""
    rpc = await kit_tools.exec_sync(script, timeout=30)
    if not rpc.get("success"):
        return {"ok": False, "error": f"kit exec_sync failed: {rpc.get('output', '')[:200]}"}

    # Parse the printed JSON. The output may contain extra log lines; take
    # the last line that parses as a JSON object with our expected keys.
    out = (rpc.get("output") or "").strip()
    payload = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if "ok" in parsed:
            payload = parsed
            break
    if not payload or not payload.get("ok"):
        err = (payload or {}).get("error") if payload else out[:200]
        return {"ok": False, "error": f"export failed: {err}"}

    text = payload.get("text") or ""
    if not text:
        return {"ok": False, "error": "empty layer export"}

    # Name: epoch ms + zero-padded turn index so ls sorts chronologically
    # and the index gives users a stable handle for `/undo N`.
    existing = _list_snapshots(session_id)
    turn_index = len(existing) + 1
    ts_ms = int(time.time() * 1000)
    fname = f"{ts_ms}_{turn_index:04d}.usda"
    if label:
        fname = f"{ts_ms}_{turn_index:04d}_{re.sub(r'[^A-Za-z0-9._-]', '_', label)[:40]}.usda"
    path = _session_dir(session_id) / fname
    try:
        path.write_text(text, encoding="utf-8")
    except Exception as e:
        logger.warning(f"turn_snapshot: write failed for {session_id}: {e}")
        return {"ok": False, "error": f"write: {e}"}

    logger.info(
        f"[turn_snapshot] captured {session_id} turn {turn_index} "
        f"→ {path.name} ({len(text)} chars)"
    )
    return {
        "ok": True,
        "path": str(path),
        "layer_size": len(text),
        "turn_index": turn_index,
    }


async def restore(session_id: str, steps: int = 1) -> Dict[str, Any]:
    """Restore the stage to N turns ago by re-importing the saved USDA.

    ``steps=1`` (default) reverts the most recent turn — i.e. the
    last-saved snapshot IS the state BEFORE that turn's tools ran, so
    importing it = "undo the turn".
    ``steps=N`` goes back N snapshots.

    Returns ``{ok, path, steps, turn_index}`` or ``{ok: False, error}``.
    The USDA import triggers a full Hydra recompose, so the viewport
    updates correctly.
    """
    snapshots = _list_snapshots(session_id)
    if not snapshots:
        return {"ok": False, "error": "no snapshots in this session"}
    if steps < 1:
        return {"ok": False, "error": f"steps must be >= 1, got {steps}"}
    if steps > len(snapshots):
        return {
            "ok": False,
            "error": f"requested {steps} steps but only {len(snapshots)} snapshots available",
        }

    # The N-th most recent snapshot is at index -(steps).
    target = snapshots[-steps]
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"read failed: {e}"}

    try:
        from .tools import kit_tools
    except Exception as e:
        return {"ok": False, "error": f"kit_tools import: {e}"}

    # Kit-side script: replace the root layer's content with the saved USDA.
    # We embed the text as a JSON literal so quoting stays sound even when
    # the USDA contains triple-quotes or backslashes.
    script = f"""
import json
import omni.usd
stage = omni.usd.get_context().get_stage()
if stage is None:
    print(json.dumps({{'ok': False, 'error': 'no stage'}}))
else:
    layer_text = json.loads({json.dumps(json.dumps(text))})
    try:
        stage.GetRootLayer().ImportFromString(layer_text)
        print(json.dumps({{'ok': True, 'imported_size': len(layer_text)}}))
    except Exception as exc:
        print(json.dumps({{'ok': False, 'error': str(exc)}}))
"""
    rpc = await kit_tools.exec_sync(script, timeout=30)
    if not rpc.get("success"):
        return {"ok": False, "error": f"kit exec_sync failed: {rpc.get('output', '')[:200]}"}

    # Parse payload like in capture.
    out = (rpc.get("output") or "").strip()
    payload = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if "ok" in parsed:
            payload = parsed
            break
    if not payload or not payload.get("ok"):
        err = (payload or {}).get("error") if payload else out[:200]
        return {"ok": False, "error": f"import failed: {err}"}

    # Remove the snapshots AT AND AFTER the restore target — otherwise
    # subsequent `/undo` would revert past the restore point, which is
    # confusing ("I already restored to turn 3, another undo should go to
    # turn 2, not redo the restore").
    for snap in snapshots[-steps:]:
        try:
            snap.unlink()
        except Exception:
            pass

    logger.info(
        f"[turn_snapshot] restored {session_id} {steps} step(s) ← {target.name}"
    )
    return {
        "ok": True,
        "path": str(target),
        "steps": steps,
        "imported_size": payload.get("imported_size", 0),
        "remaining_snapshots": len(snapshots) - steps,
    }


def snapshot_count(session_id: str) -> int:
    """Number of snapshots currently stored for a session."""
    return len(_list_snapshots(session_id))


def clear(session_id: str) -> int:
    """Wipe all snapshots for a session. Returns the count removed.

    Called by ``/undo clear`` if a user wants to reset their rollback
    history, or at end-of-session cleanup.
    """
    files = _list_snapshots(session_id)
    removed = 0
    for f in files:
        try:
            f.unlink()
            removed += 1
        except Exception:
            pass
    return removed
