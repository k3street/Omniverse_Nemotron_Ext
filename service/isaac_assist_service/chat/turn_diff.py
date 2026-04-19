"""Compute a structured diff between the pre-turn snapshot and the live stage.

This is the generalizable replacement for a per-prim-type verify-contract
(2026-04-19: a kub-specific "did cubes really get scaled?" check was the
obvious patch, but multi-step tasks come in many flavors — conveyors,
robots, lights, materials, arenas — and each type would need its own
checker. A single stage-diff primitive covers them all.)

How it works:
  1. The orchestrator auto-captures the root layer's USDA text BEFORE
     each stage-mutating turn. See turn_snapshot.py.
  2. After the turn, call ``compute_diff(session_id)``:
      - Load the most-recent snapshot USDA into an anonymous Sdf.Layer
      - Read the current root layer's USDA via kit_tools.exec_sync
      - Walk both layers' prim specs, record added / removed / modified
        paths and per-path changed attributes.
  3. The orchestrator's verify-contract then cross-checks mutation claims
     in the agent's reply against this diff. If the reply says "cubes
     scaled and placed" but no /World/Cube* appears in the diff, flag it.

The diff is prim-type agnostic, attribute-agnostic, and language-agnostic
(the claim-extractor sits on top). Adding a new mutation kind to catch
requires zero changes to this module — only the extractor grows.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TurnDiff:
    """Structured diff between the pre-turn snapshot and the current stage.

    ``added`` / ``removed`` are full prim paths. ``modified`` maps each
    modified prim path to the list of attribute names whose values or
    metadata differ. ``ok`` is False when the diff could not be computed —
    callers should treat that as "no evidence either way", not "nothing
    changed".
    """
    ok: bool = True
    added: Set[str] = field(default_factory=set)
    removed: Set[str] = field(default_factory=set)
    modified: Dict[str, List[str]] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    def paths_under(self, prefix: str) -> List[str]:
        """All diffed paths that sit under a given prefix (e.g. ``/World/Cube``)."""
        hits = []
        for p in list(self.added) + list(self.removed) + list(self.modified):
            if p == prefix or p.startswith(prefix):
                hits.append(p)
        return hits

    def paths_matching(self, pattern: str) -> List[str]:
        """All diffed paths matching a regex (e.g. ``r'/World/Cube\\d+'``)."""
        rx = re.compile(pattern)
        return [p for p in list(self.added) | self.removed | set(self.modified)
                if rx.search(p)]


async def compute_diff(session_id: str) -> TurnDiff:
    """Diff the latest turn_snapshot against the current stage.

    The parse runs INSIDE Kit because pxr.Sdf is only available there —
    the service process doesn't have pxr bindings. Previously this fell
    back to a regex parser that stripped the /World parent path from
    nested prims, causing false-positive "path not in diff" warnings on
    every /World/... path (2026-04-19).

    Returns TurnDiff with full-qualified paths. The caller decides how to
    act on it — typically the orchestrator's verify-contract uses
    ``diff.paths_under("/World/Cube")`` or ``diff.total_changes`` to
    validate mutation claims in the reply.
    """
    try:
        from .turn_snapshot import _session_dir
    except Exception as e:
        return TurnDiff(ok=False, error=f"turn_snapshot import: {e}")

    snapshots = sorted(_session_dir(session_id).glob("*.usda"))
    if not snapshots:
        return TurnDiff(ok=False, error="no snapshots for session")

    try:
        before_text = snapshots[-1].read_text(encoding="utf-8")
    except Exception as e:
        return TurnDiff(ok=False, error=f"snapshot read: {e}")

    # Run the whole diff computation inside Kit. Kit has pxr.Sdf and
    # omni.usd; it parses the snapshot USDA into an anonymous layer and
    # walks both it and the live root layer, emitting the flat prim-spec
    # maps as JSON. We then compute the set-diff in Python.
    try:
        from .tools import kit_tools
    except Exception as e:
        return TurnDiff(ok=False, error=f"kit_tools import: {e}")

    script = f"""
import json
from pxr import Sdf
import omni.usd

# Embed the snapshot USDA literally via a JSON string literal — survives
# triple quotes and backslashes in the USDA.
snap_text = json.loads({json.dumps(json.dumps(before_text))})

# Tag the anonymous layer with a .usda tag so ImportFromString
# parses as USDA text (otherwise Sdf tries the native .sdf format
# and rejects '#usda 1.0' as an unexpected magic cookie).
anon = Sdf.Layer.CreateAnonymous('.usda')
if not anon.ImportFromString(snap_text):
    print(json.dumps({{'ok': False, 'error': 'snapshot import failed'}}))
else:
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({{'ok': False, 'error': 'no stage'}}))
    else:
        current_layer = stage.GetRootLayer()

        def walk(layer, path_obj, out):
            spec = layer.GetPrimAtPath(path_obj)
            if spec is None:
                return
            key = str(path_obj)
            attrs = {{
                '__typeName__': str(spec.typeName or ''),
                '__specifier__': str(spec.specifier),
                '__active__': str(spec.active) if spec.HasInfo('active') else 'inherit',
            }}
            for prop_name, prop_spec in spec.properties.items():
                try:
                    val = prop_spec.default
                except Exception:
                    val = None
                attrs[prop_name] = repr(val)
            out[key] = attrs
            for child_name in spec.nameChildren.keys():
                child_path = path_obj.AppendChild(child_name)
                walk(layer, child_path, out)

        before = {{}}
        walk(anon, Sdf.Path('/'), before)

        after = {{}}
        walk(current_layer, Sdf.Path('/'), after)

        print(json.dumps({{'ok': True, 'before': before, 'after': after}}))
"""
    rpc = await kit_tools.exec_sync(script, timeout=30)
    if not rpc.get("success"):
        return TurnDiff(ok=False, error=f"kit exec failed: {rpc.get('output', '')[:200]}")

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
        return TurnDiff(ok=False, error=f"parse failed: {err}")

    before_map = payload.get("before") or {}
    after_map = payload.get("after") or {}
    before_paths = set(before_map)
    after_paths = set(after_map)

    added = after_paths - before_paths
    removed = before_paths - after_paths

    modified: Dict[str, List[str]] = {}
    for path in before_paths & after_paths:
        b = before_map[path]
        a = after_map[path]
        changed_attrs: List[str] = []
        all_keys = set(b) | set(a)
        for k in all_keys:
            if b.get(k) != a.get(k):
                changed_attrs.append(k)
        if changed_attrs:
            modified[path] = sorted(changed_attrs)

    return TurnDiff(
        ok=True,
        added=added,
        removed=removed,
        modified=modified,
    )


# ───────────────────────────────────────────────────────────────────────
# Structural cross-check. Language-agnostic by design.
#
# The check is intentionally narrow: given a set of paths the reply
# mentions (extracted elsewhere via /World/... regex or the existing
# bare-name extractor), which ones ARE in the diff? Those are the
# substantiated claims. The rest — paths mentioned but not in diff —
# are the fabrication candidates.
#
# No verb detection, no noun-class classification, no language-specific
# stems. The only assumption is "paths in URLs look like /World/X", which
# is a USD invariant, not a language choice.
#
# The caller (orchestrator's verify-contract) is responsible for:
#   - extracting paths from the reply (existing /World/... regex)
#   - deciding whether this turn is a mutation turn (intent_router result)
#   - calling this function only when both conditions are met
# ───────────────────────────────────────────────────────────────────────


def unsubstantiated_paths(mentioned_paths: Set[str], diff: TurnDiff) -> List[str]:
    """Return paths the reply mentioned but that were not added, modified,
    or removed this turn. Empty list = every claim is substantiated by
    actual stage changes.

    ``mentioned_paths`` must be extracted by the caller — this function
    makes no assumption about how they were found.

    Paths in ``diff.removed`` count as substantiated (valid "deleted X"
    claim). Paths in ``diff.added`` or ``diff.modified`` also substantiate.
    Anything else is a mismatch.
    """
    if not diff.ok:
        return []  # no evidence either way — don't cry wolf
    evidence = set(diff.added) | set(diff.modified) | set(diff.removed)
    return sorted(p for p in mentioned_paths if p not in evidence)
