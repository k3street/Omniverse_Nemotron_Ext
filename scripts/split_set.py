#!/usr/bin/env python3
"""A file holding several copies of an articulable object becomes one asset
per copy (pxr only).

A Sketchfab "combination lock" held two padlocks; surveyed and jointed as
one asset, its shackle hinges were drafted across both and the critic
could not tell which lock it was looking at. A set of free bodies (chess
pieces) stays one asset (set_bodies); a set of objects that each move
(padlocks, pliers in a pair, a row of drawers' knobs) does not: each copy
gets its own derivative, with the other copies switched off, its own
survey (the parent's, kept to its prims), its own queue entry and ledger,
and goes through articulate, verify, critic and approve on its own. The
parent is marked split and processed no further.

    python scripts/split_set.py <asset_id>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"
FIXED = REPO / "workspace" / "assets_fixed"

MOVING = ("hinge", "slide", "spin", "press", "turn", "swing", "rotate", "lift")


def groups_of(stage, root: str) -> list[list[str]]:
    """The separate objects in the file, as lists of prim paths."""
    from ingest_asset import object_groups

    return [[str(m.GetPath()) for m in g] for g in object_groups(stage, root)]


def splittable(entry: dict, stage=None) -> list[list[str]]:
    """The groups to split into, or []: an object set whose survey finds
    parts that move, with two or more objects that each hold one."""
    if (entry.get("vlm") or {}).get("content_kind") != "object_set":
        return []
    survey = entry.get("part_survey") or {}
    movers = [m for p in survey.get("parts", []) if p.get("motion") in MOVING
              for m in [p.get("path")] + list(p.get("members") or []) + list(p.get("copies") or []) if m]
    if not movers or not survey.get("root"):
        return []
    if stage is None:
        from pxr import Usd
        stage = Usd.Stage.Open(entry["file"])
    groups = groups_of(stage, survey["root"])
    if len(groups) < 2:
        return []

    def holds(group, path):
        return any(path == g or path.startswith(g + "/") for g in group)

    with_movers = [g for g in groups if any(holds(g, m) for m in movers)]
    return groups if len(with_movers) >= 2 else []


def child_id(asset_id: str, i: int) -> str:
    return f"{asset_id}__{i}"


def split_entry(asset_id: str) -> list[str]:
    """Write one derivative and queue entry per object; returns the new ids
    (empty when there is nothing to split)."""
    from pxr import Sdf, Usd

    from asset_review_hub import _camel, save_queue_entry
    from ingest_asset import refresh_renders, run_report
    from processing import owned, record

    qf = QUEUE / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    if not owned(entry["file"]):
        return []
    stage = Usd.Stage.Open(entry["file"])
    groups = splittable(entry, stage)
    if not groups:
        return []
    survey = entry["part_survey"]
    root = survey["root"]
    layer = stage.GetRootLayer()
    children = []
    for i, group in enumerate(groups, 1):
        cid = child_id(asset_id, i)
        new_root = f"/World/{_camel(cid)}"
        out = FIXED / f"{cid}_simready.usda"
        existing = Sdf.Layer.Find(str(out))
        if existing:
            existing.Clear()
        layer.Export(str(out))
        nl = Sdf.Layer.FindOrOpen(str(out))
        nl.Reload()
        spec = nl.GetPrimAtPath(root)
        if spec is None:
            raise RuntimeError(f"{asset_id}: no {root} in its derivative")
        spec.name = new_root.rsplit("/", 1)[1]
        # the parent's joints and physics go: the child is jointed on its own
        for scope in ("Joints", "Mechanisms"):
            if scope in spec.nameChildren:
                del spec.nameChildren[scope]
        nl.Save()
        cst = Usd.Stage.Open(str(out))

        def moved(path: str) -> str:
            return new_root + path[len(root):] if path == root or path.startswith(root + "/") else path

        for other in groups:
            if other is group:
                continue
            for m in other:
                cst.OverridePrim(moved(m)).SetActive(False)
        # every physics API the parent authored, off (unarticulate strips the
        # same list on a derivative; here the child's own file)
        from set_bodies import strip_physics
        strip_physics(cst, new_root)
        cst.GetRootLayer().Save()
        del cst

        def mine(path: str) -> bool:
            return any(path == g or path.startswith(g + "/") for g in group)

        parts = []
        for p in survey.get("parts", []):
            members = [moved(m) for m in (p.get("members") or [p["path"]]) if mine(m)]
            if not members:
                continue
            copies = [moved(m) for m in (p.get("copies") or []) if mine(m)]
            q = {**p, "path": members[0], "members": members, "copies": copies if len(copies) > 1 else []}
            for key in ("split",):
                q.pop(key, None)
            parts.append(q)
        child = {k: v for k, v in entry.items() if k not in (
            "articulation_draft", "motion_qa", "critic_flags", "critic_corrections", "critic_retry", "auto_approval",
            "set_members", "set_bodies", "behavior_check", "chess", "chess_play", "flipped_joints", "pruned_joints",
            "mixed_body", "soft_body_parts", "split_into")}
        child.update({
            "asset_id": cid, "file": str(out), "status": "pending_review",
            "split_from": asset_id, "split_index": i, "split_of": len(groups),
            "part_survey": {**survey, "root": new_root, "parts": parts},
            "vlm": {**(entry.get("vlm") or {}), "content_kind": "single",
                    "object_name": re.sub(r"\s*\((pair|set|two|three|four|\d+)[^)]*\)", "", (entry.get("vlm") or {}).get("object_name") or "")},
            "applied_fixes": [f"split from {asset_id}: object {i} of {len(groups)} ({', '.join(Path(g).name for g in group)}); "
                              "the parent's joints and physics taken off"],
            "processing": {k: v for k, v in (entry.get("processing") or {}).items()
                           if k in ("ingest", "classify", "survey", "file", "materials", "rig")},
        })
        child["report"] = run_report(str(out), child.get("class_hint"))
        try:
            refresh_renders(child)
        except Exception:  # noqa: BLE001 - renders are a convenience here
            pass
        record(child, "ingest", verdict=child["report"].get("verdict"), split_from=asset_id)
        save_queue_entry(child)
        # the parent's physics materials were bound under the parent's root
        # name: the child binds its own, from the survey, the class's on the rest
        try:
            from part_materials import apply as bind_materials, ensure_physics
            bind_materials(cid)
            ensure_physics(cid)
            child = json.loads((QUEUE / f"{cid}.json").read_text())
            child["report"] = run_report(str(out), child.get("class_hint"))
            save_queue_entry(child)
        except Exception as ex:  # noqa: BLE001 - a child without materials is caught by the approval gate
            child.setdefault("applied_fixes", []).append(f"materials not bound after the split: {str(ex)[:100]}")
            save_queue_entry(child)
        children.append(cid)
    entry = json.loads(qf.read_text())
    entry["status"] = "split"
    entry["split_into"] = children
    entry.setdefault("applied_fixes", []).append(
        f"split into {len(children)} assets, one per object: {', '.join(children)}")
    save_queue_entry(entry)
    return children


if __name__ == "__main__":
    for a in sys.argv[1:]:
        kids = split_entry(a)
        print(f"{a}: " + (f"split into {', '.join(kids)}" if kids else "nothing to split"))
