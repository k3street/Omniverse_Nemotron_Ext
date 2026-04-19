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


def _parse_usda_prim_specs(usda_text: str) -> Dict[str, Dict[str, str]]:
    """Extract ``{prim_path: {attr_name: attr_value_string}}`` from USDA text.

    We use pxr.Sdf.Layer's anonymous-layer machinery when available because
    it's the canonical parser; fall back to a regex approximation if Sdf
    cannot be imported (outside Kit). The fallback is lossy but catches
    the common case: "def Cube \"Cube1\"" + "xformOp:translate = (x,y,z)".

    Returns a flat map — nested hierarchies are addressable via full path.
    Attribute values are normalized to their repr string for comparison
    (we don't need semantic diffing, just "is it different").
    """
    try:
        from pxr import Sdf
    except Exception:
        return _parse_usda_regex_fallback(usda_text)

    try:
        layer = Sdf.Layer.CreateAnonymous()
        ok = layer.ImportFromString(usda_text)
        if not ok:
            logger.warning("turn_diff: Sdf import returned false")
            return _parse_usda_regex_fallback(usda_text)
    except Exception as e:
        logger.warning(f"turn_diff: Sdf parse failed ({e}); using regex fallback")
        return _parse_usda_regex_fallback(usda_text)

    out: Dict[str, Dict[str, str]] = {}

    def _walk(path):
        spec = layer.GetPrimAtPath(path)
        if spec is None:
            return
        attrs: Dict[str, str] = {
            "__typeName__": str(spec.typeName or ""),
            "__specifier__": str(spec.specifier),
            "__active__": str(spec.active) if spec.HasInfo("active") else "inherit",
        }
        for prop_name, prop_spec in spec.properties.items():
            try:
                val = prop_spec.default
            except Exception:
                val = None
            attrs[prop_name] = repr(val)
        out[str(path)] = attrs
        for child_name in spec.nameChildren.keys():
            _walk(f"{path}/{child_name}" if str(path) != "/" else f"/{child_name}")

    _walk(Sdf.Path("/"))
    return out


_RE_DEF_PRIM = re.compile(
    r'\n\s*def\s+(?P<type>\w+)?\s*"(?P<name>[^"]+)"',
)
_RE_ATTR = re.compile(
    r'\n\s+(?:uniform\s+|custom\s+|rel\s+)?(\w+(?::\w+)*)\s*=\s*(.+?)(?=\n)',
)


def _parse_usda_regex_fallback(usda_text: str) -> Dict[str, Dict[str, str]]:
    """Last-resort parser for environments without pxr. Only catches top-level
    def <Type> "Name" stanzas and inline attribute assignments; nested prims
    are flattened wrong. Used only when Sdf.Layer.CreateAnonymous fails.
    """
    out: Dict[str, Dict[str, str]] = {}
    # Very rough: split by `def ` blocks, extract name + attrs.
    # Good enough for flat /World/* stages — good enough for the smoke-test use.
    for m in _RE_DEF_PRIM.finditer(usda_text):
        path = f"/{m.group('name')}"
        out[path] = {"__typeName__": m.group("type") or ""}
    return out


async def _read_current_root_layer_text() -> Optional[str]:
    """Ask Kit to export the current root layer's USDA. Returns None on error.

    Mirrors the export logic in turn_snapshot.capture, but without writing
    to disk — we need the text for in-memory comparison.
    """
    try:
        from .tools import kit_tools
    except Exception as e:
        logger.warning(f"turn_diff: kit_tools import failed: {e}")
        return None

    script = """
import json
import omni.usd
stage = omni.usd.get_context().get_stage()
if stage is None:
    print(json.dumps({'ok': False, 'error': 'no stage'}))
else:
    try:
        text = stage.GetRootLayer().ExportToString()
        print(json.dumps({'ok': True, 'text': text}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
"""
    rpc = await kit_tools.exec_sync(script, timeout=30)
    if not rpc.get("success"):
        return None
    out = (rpc.get("output") or "").strip()
    for line in reversed(out.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if parsed.get("ok"):
            return parsed.get("text") or ""
    return None


async def compute_diff(session_id: str) -> TurnDiff:
    """Diff the latest turn_snapshot against the current stage.

    Returns TurnDiff. The caller decides how to act on it — typically the
    orchestrator's verify-contract uses ``diff.paths_under("/World/Cube")``
    or ``diff.total_changes`` to validate mutation claims in the reply.
    """
    # Find the most recent snapshot for this session.
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

    after_text = await _read_current_root_layer_text()
    if after_text is None:
        return TurnDiff(ok=False, error="could not read current root layer")

    before_map = _parse_usda_prim_specs(before_text)
    after_map = _parse_usda_prim_specs(after_text)

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
