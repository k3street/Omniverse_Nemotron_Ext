#!/usr/bin/env python3
"""Promote an approved asset to the finished library (BACKLOG #1).

"Approved" must mean "finished and sim-usable". Promotion:
  1. ensures physics is authored on the derivative — rigid categories get
     make_sim_ready (class material + plausible mass); articulated assets
     must already have joints (authored via the hub's draft/apply flow) and
     get physics materials bound + per-link masses split by bbox volume;
  2. lands the asset in ONE canonical, self-contained library:
     workspace/asset_library/<asset_id>/<asset_id>.usda with the SOURCE
     COPIED IN and the reference rewritten to a relative path (the library
     folder is portable);
  3. stamps customData['simReady'] on the asset prim;
  4. re-audits and refuses to promote if error callouts remain;
  5. points the registry entry's `file` at the library copy.

Usage:
    python scripts/promote_asset.py <asset_id> [<asset_id> ...]
    python scripts/promote_asset.py --all-registry
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import types
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_asset import (  # noqa: E402
    QUEUE_DIR,
    _camel,
    build_wrapper,
    run_report,
)

LIBRARY = REPO / "workspace" / "asset_library"
REGISTRY = REPO / "workspace" / "knowledge" / "sim_ready_assets.json"
SCHEMA = REPO / "workspace" / "knowledge" / "sim_ready_asset_registry.schema.json"


def _stub_omni(stage) -> None:
    omni = types.ModuleType("omni")
    omni_usd = types.ModuleType("omni.usd")
    ctx = type("Ctx", (), {"get_stage": lambda self: stage})()
    omni_usd.get_context = lambda: ctx
    omni.usd = omni_usd
    sys.modules["omni"] = omni
    sys.modules["omni.usd"] = omni_usd


def _run_codegen(stage, code: str) -> str:
    _stub_omni(stage)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<promote>", "exec"), {"__builtins__": __builtins__})
    return out.getvalue()


def _load_entry(asset_id: str) -> dict | None:
    f = QUEUE_DIR / f"{asset_id}.json"
    return json.loads(f.read_text()) if f.exists() else None


def _registry() -> dict:
    return json.loads(REGISTRY.read_text())


def _class_prior(report: dict) -> dict:
    from service.isaac_assist_service.chat.tools.handlers.physics import (
        _load_asset_priors,
    )
    cls = report.get("matched_class")
    return _load_asset_priors().get("classes", {}).get(cls or "", {})


def ensure_physics(entry: dict, category: str) -> list[str]:
    """Author missing physics on the entry's derivative. Returns notes."""
    from pxr import Usd, UsdGeom, UsdPhysics

    from service.isaac_assist_service.chat.tools.handlers.physics import (
        _gen_apply_physics_material,
        _gen_make_sim_ready,
    )

    notes = []
    # build a derivative only when the entry still points at the raw source
    # (never rebuild an authored .usda — that would discard its physics)
    is_raw = (entry["file"] == entry.get("original_file")
              or entry["file"].lower().endswith(".usdz"))
    if is_raw:
        entry.setdefault("original_file", entry["file"])
        entry["file"] = build_wrapper(entry, None)
        entry["report"] = run_report(entry["file"], entry.get("class_hint"))
        notes.append("built derivative wrapper")

    stage = Usd.Stage.Open(entry["file"])
    rigid = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    joints = [p for p in stage.Traverse() if p.IsA(UsdPhysics.Joint)]
    prior = _class_prior(entry.get("report", {}))
    mats = prior.get("typical_materials") or []

    if category.startswith("articulated"):
        if not joints:
            raise RuntimeError(
                "articulated category but no joints authored — use the hub's "
                "'Draft articulation spec' flow first")
        # bind class material + volume-proportional masses on each link
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                                  [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        vols = {}
        for p in rigid:
            r = cache.ComputeWorldBound(p).ComputeAlignedRange()
            s = r.GetSize() if not r.IsEmpty() else None
            vols[p] = max(1e-9, s[0] * s[1] * s[2]) if s else 1e-9
        total_v = sum(vols.values())
        mass_range = prior.get("mass_kg")
        total_mass = (mass_range[0] + mass_range[1]) / 2.0 if mass_range else None
        for p in rigid:
            rel = p.GetRelationship("physics:materialBinding")
            if mats and not (rel and rel.GetTargets()):
                _run_codegen(stage, _gen_apply_physics_material(
                    {"prim_path": str(p.GetPath()), "material_name": mats[0]}))
            if total_mass and not p.HasAPI(UsdPhysics.MassAPI):
                UsdPhysics.MassAPI.Apply(p).CreateMassAttr().Set(
                    round(total_mass * vols[p] / total_v, 4))
        if mats:
            notes.append(f"bound {mats[0]} to {len(rigid)} links")
        if total_mass:
            notes.append(f"per-link masses: {round(total_mass, 2)} kg split by volume")
        stage.GetRootLayer().Save()
    elif category.startswith("deformable"):
        # soft body: no rigid authoring — PhysX deformable APIs are
        # Kit-only; the registry's `deformable` type drives
        # create_deformable_mesh live at scene build / verification
        notes.append(f"deformable ({entry.get('deformable', 'cloth')}): "
                     "no rigid physics authored; preset applies live")
    elif not rigid:
        args = {"prim_path": f"/World/{_camel(entry['asset_id'])}",
                "profile": "manipulable"}
        if mats:
            args["material"] = mats[0]
        mass_range = prior.get("mass_kg")
        if mass_range:
            args["mass_kg"] = round((mass_range[0] + mass_range[1]) / 2.0, 3)
        _run_codegen(stage, _gen_make_sim_ready(args))
        stage.GetRootLayer().Save()
        notes.append(f"make_sim_ready ({args.get('material')}, "
                     f"{args.get('mass_kg')} kg)")
    del stage
    entry["report"] = run_report(entry["file"], entry.get("class_hint"))
    return notes


def land_in_library(entry: dict, category: str, reviewer: str) -> str:
    """Copy source + derivative into the library with a relative reference."""
    from pxr import Sdf, Usd

    asset_id = entry["asset_id"]
    src = entry.get("original_file") or entry["file"]
    lib_dir = LIBRARY / asset_id
    lib_dir.mkdir(parents=True, exist_ok=True)
    src_copy = lib_dir / Path(src).name
    if Path(src).resolve() != src_copy.resolve():
        shutil.copy2(src, src_copy)
    lib_file = lib_dir / f"{asset_id}.usda"
    if entry["file"] != str(lib_file):
        shutil.copy2(entry["file"], lib_file)
    layer = Sdf.Layer.FindOrOpen(str(lib_file))
    if layer is None:
        raise RuntimeError(f"cannot open {lib_file}")
    # rewrite source references to relative, in-library paths by walking the
    # prim specs (UpdateExternalReference does not reliably match authored
    # relative paths)
    def _rewrite(spec):
        items = spec.referenceList.GetAddedOrExplicitItems()
        if items:
            new_refs = []
            changed = False
            for r in items:
                if r.assetPath and Path(r.assetPath).name == src_copy.name \
                        and r.assetPath != f"./{src_copy.name}":
                    new_refs.append(Sdf.Reference(f"./{src_copy.name}", r.primPath,
                                                  r.layerOffset, r.customData))
                    changed = True
                else:
                    new_refs.append(r)
            if changed:
                spec.referenceList.ClearEdits()
                for r in new_refs:
                    spec.referenceList.explicitItems.append(r)
        for child in spec.nameChildren:
            _rewrite(child)
    for root_spec in layer.rootPrims:
        _rewrite(root_spec)
    layer.Save()
    stage = Usd.Stage.Open(str(lib_file))
    prim = stage.GetPrimAtPath(f"/World/{_camel(asset_id)}")
    if not prim or not prim.IsValid():
        default = stage.GetDefaultPrim()
        kids = default.GetChildren() if default else []
        prim = kids[0] if kids else default
    if prim and prim.IsValid():
        prim.SetCustomDataByKey("simReady", {
            "category": category,
            "registry": "workspace/knowledge/sim_ready_assets.json",
            "verified": date.today().isoformat(),
            "reviewer": reviewer,
        })
        stage.GetRootLayer().Save()
    # thumbnail alongside
    thumb = entry.get("thumbnail")
    if thumb and Path(thumb).exists():
        shutil.copy2(thumb, lib_dir / "thumbnail.png")
    return str(lib_file)


def promote(asset_id: str, reg: dict | None = None) -> str:
    """Full promotion of one asset. Returns a human-readable summary."""
    reg = reg or _registry()
    reg_entry = next((a for a in reg["assets"] if a["asset_id"] == asset_id), None)
    entry = _load_entry(asset_id)
    if reg_entry is None:
        raise RuntimeError(f"{asset_id} is not in the registry (approve it first)")
    if entry is None:
        # hand-built assets (no queue entry): synthesize one from the
        # registry (report generated after a live file is resolved below)
        entry = {"asset_id": asset_id, "file": reg_entry["file"],
                 "original_file": reg_entry.get("source_file", reg_entry["file"])}
    category = reg_entry["category"]
    reviewer = reg_entry.get("review", {}).get("reviewer", "unknown")

    # resolve a live file: current entry -> assets_fixed derivative ->
    # Desktop hand-built -> raw source (most-authored candidate first)
    asset_fixed = REPO / "workspace" / "assets_fixed" / f"{asset_id}_simready.usda"
    candidates = [entry.get("file"), str(asset_fixed),
                  str(Path.home() / "Desktop" / f"{asset_id}_simready.usda"),
                  entry.get("original_file"), reg_entry.get("source_file")]
    for c in candidates:
        if c and Path(c).exists():
            if entry.get("file") != c or not entry.get("report"):
                entry["file"] = c
                entry["report"] = run_report(c, entry.get("class_hint"))
            break
    else:
        raise RuntimeError("no existing file found for this asset")

    notes = ensure_physics(entry, category)
    report = entry["report"]
    errors = [c for c in report.get("callouts", []) if c["severity"] == "error"]
    if errors and category != "rigid_only_baked":
        raise RuntimeError(
            f"error callouts remain after physics authoring: "
            + "; ".join(c["message"][:70] for c in errors))

    lib_file = land_in_library(entry, category, reviewer)
    reg_entry["file"] = lib_file
    reg_entry.setdefault("source_file", entry.get("original_file") or entry["file"])
    REGISTRY.write_text(json.dumps(reg, indent=1) + "\n")
    entry["file"] = lib_file
    entry["status"] = "promoted"
    if (QUEUE_DIR / f"{asset_id}.json").exists():
        (QUEUE_DIR / f"{asset_id}.json").write_text(json.dumps(entry, indent=1))
    return (f"promoted {asset_id} -> {lib_file}"
            + (f" ({'; '.join(notes)})" if notes else ""))


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    reg = _registry()
    ids = ([a["asset_id"] for a in reg["assets"]]
           if args == ["--all-registry"] else args)
    failures = 0
    for asset_id in ids:
        try:
            print(promote(asset_id, reg))
        except Exception as e:
            failures += 1
            print(f"FAILED {asset_id}: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
