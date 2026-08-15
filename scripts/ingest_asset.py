#!/usr/bin/env python3
"""Ingest an asset file for sim2real verification and queue it for human review.

Runs the same `ingest_asset_report` check codegen the chat tool uses, but
headlessly (needs `pxr` importable — e.g. an OpenUSD build on PYTHONPATH).
Writes the report to workspace/review_queue/<asset_id>.json, where the
asset review hub (scripts/asset_review_hub.py) picks it up.

Usage:
    python scripts/ingest_asset.py /path/to/asset.usdz [--class-hint pan] [--id my_asset]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"
sys.path.insert(0, str(REPO))


def optional_nvidia_validation(file_path: str) -> dict | None:
    """Run NVIDIA validation when its sidecar is installed or explicitly set.

    ``NVIDIA_USD_VALIDATION_ON_INGEST=0`` disables the hook.  In the default
    ``auto`` mode, ingest remains unchanged when the dedicated executable is
    absent; no Isaac Sim or Newton environment is modified.
    """
    mode = os.environ.get("NVIDIA_USD_VALIDATION_ON_INGEST", "auto").lower()
    if mode in {"0", "false", "no", "off"}:
        return None
    from service.isaac_assist_service.analysis.validators.nvidia_usd_validation import (
        findings_record,
        resolve_validator_command,
        validate_asset,
    )

    command = resolve_validator_command()
    if not command and mode == "auto":
        return None
    return findings_record(validate_asset(file_path, command=command))


def run_report(file_path: str, class_hint: str | None) -> dict:
    """Execute the ingest-report generated code locally and return the report."""
    from service.isaac_assist_service.chat.tools import kit_tools
    from service.isaac_assist_service.chat.tools.handlers.physics import (
        _handle_ingest_asset_report,
    )

    captured = {}

    async def grab(code, description="", timeout=600):
        captured["code"] = code
        return {}

    kit_tools.queue_exec_patch = grab
    args = {"file_path": file_path}
    if class_hint:
        args["class_hint"] = class_hint
    asyncio.run(_handle_ingest_asset_report(args))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(captured["code"], "<ingest>", "exec"), {"__builtins__": __builtins__})
    return json.loads(out.getvalue().strip().splitlines()[-1])


def propose_category(report: dict) -> str:
    """Category proposal from the report — final say belongs to the reviewer.

    An articulable-class asset with separate parts but no joints yet proposes
    'articulated_unverified' (its destination after articulate_asset), never
    'rigid' — the object is not rigid just because nobody authored its joints.
    """
    callouts = report.get("callouts", [])
    if report.get("skeleton"):
        # UsdSkel rig: a character is a kinematic animated collider,
        # never a dynamic rigid body
        return "character_rigged"
    if _deformable_type(report) and not report.get("structure", {}).get("joints"):
        # soft-body class: rigid categories would be a physics lie. But
        # AUTHORED JOINTS outrank the class keyword — a jointed cable
        # assembly is an articulation, not cloth (the composed corded
        # tool matched 'cord' -> rope_cable and mis-routed).
        return "deformable_unverified"
    if any(c["check"] == "articulation" and "baked" in c["message"] for c in callouts):
        return "rigid_only_baked"
    if report.get("structure", {}).get("joints"):
        return "articulated_unverified"
    if any(c["check"] == "articulation" and "articulate_asset" in c["message"]
           for c in callouts):
        return "articulated_unverified"
    return "rigid_unverified"


def scan_scene_features(file_path: str) -> dict:
    """Non-geometry features the report codegen doesn't know about:
    UsdSkel rigs (characters) and UsdLux lights. Cheap single traversal."""
    out = {}
    try:
        from pxr import Usd, UsdLux, UsdSkel
        stage = Usd.Stage.Open(file_path)
        skels = anims = skinned = lights = 0
        joints = 0
        for prim in stage.Traverse():
            if prim.IsA(UsdSkel.Skeleton):
                skels += 1
                j = UsdSkel.Skeleton(prim).GetJointsAttr().Get()
                joints = max(joints, len(j or []))
            elif prim.IsA(UsdSkel.Animation):
                anims += 1
            elif prim.HasAPI(UsdSkel.BindingAPI) and prim.GetTypeName() == "Mesh":
                skinned += 1
            elif prim.HasAPI(UsdLux.LightAPI):
                lights += 1
        if skels:
            out["skeleton"] = {"skeletons": skels, "joints": joints,
                               "animations": anims,
                               "skinned_meshes": skinned}
        if lights:
            out["lights"] = lights
    except Exception:
        pass
    return out


def _deformable_type(report: dict) -> str | None:
    """Deformable preset/type for the report's class, or None (rigid)."""
    cls = report.get("matched_class")
    if not cls:
        return None
    priors_path = REPO / "workspace" / "knowledge" / "asset_class_priors.json"
    try:
        prior = json.loads(priors_path.read_text())["classes"].get(cls, {})
    except (OSError, json.JSONDecodeError):
        return None
    return prior.get("deformable")


def needs_articulation(report: dict) -> bool:
    """True when the class should articulate but no joints are authored yet."""
    return any(c["check"] == "articulation" and "articulate_asset" in c["message"]
               for c in report.get("callouts", []))


_USD_EXTS = {".usd", ".usda", ".usdc", ".usdz"}
FIXED_DIR = REPO / "workspace" / "assets_fixed"


def _asset_id_for(file_path: str) -> str:
    # keep unicode word chars (Cyrillic filenames etc.); ascii-only ids
    # would collapse to "" for e.g. Кресло-коляска_*.usdz
    slug = re.sub(r"[\W_]+", "_", Path(file_path).stem.lower(), flags=re.UNICODE).strip("_")
    if not slug:
        import hashlib
        slug = "asset_" + hashlib.md5(Path(file_path).name.encode()).hexdigest()[:8]
    return slug


def _camel(s: str) -> str:
    name = "".join(w.capitalize() for w in s.split("_")) or "Asset"
    # USD prim names cannot start with a digit (e.g. asset '2011_aston_...')
    return name if name[0].isalpha() else f"Asset_{name}"


def build_wrapper(entry: dict, size_factor: float | None) -> str:
    """(Re)create the sim-ready derivative: a meters/Z-up wrapper stage
    referencing the original source, with unit conversion, up-axis
    correction, and optional real-world size correction applied.

    Scale bookkeeping: the wrapper's cumulative scale is tracked on the
    entry (`wrapper_scale`). A size_factor measured against the CURRENT
    state (source or an earlier derivative) multiplies onto it — never
    trust a derivative report's meters_per_unit (it is 1.0 by
    construction and mis-multiplies the correction)."""
    from pxr import Gf, Usd, UsdGeom

    src = entry.get("original_file") or entry["file"]
    src_probe = Usd.Stage.Open(src)
    src_mpu = float(UsdGeom.GetStageMetersPerUnit(src_probe))
    src_up = str(UsdGeom.GetStageUpAxis(src_probe)).upper()
    del src_probe
    base = float(entry.get("wrapper_scale", src_mpu))
    factor = base * (size_factor if size_factor else 1.0)
    from pxr import Sdf

    FIXED_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXED_DIR / f"{entry['asset_id']}_simready.usda"
    # the layer may still be registered from an earlier build in this
    # process — clear and reuse it (CreateNew collides with a live layer)
    existing = Sdf.Layer.Find(str(out))
    if existing:
        existing.Clear()
        stage = Usd.Stage.Open(existing)
    else:
        if out.exists():
            out.unlink()
        stage = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    asset = stage.DefinePrim(f"/World/{_camel(entry['asset_id'])}", "Xform")
    src_stage = Usd.Stage.Open(src)
    default = src_stage.GetDefaultPrim()
    if default:
        asset.GetReferences().AddReference(src, str(default.GetPath()))
    else:
        asset.GetReferences().AddReference(src)
    xf = UsdGeom.XformCommonAPI(asset)
    if src_up == "Y":
        xf.SetRotate(Gf.Vec3f(90, 0, 0))
    xf.SetScale(Gf.Vec3f(factor, factor, factor))
    stage.GetRootLayer().Save()
    entry["wrapper_scale"] = factor
    return str(out)


def _find_usdrecord() -> str | None:
    import shutil
    found = shutil.which("usdrecord")
    if found:
        return found
    try:
        import pxr
        cand = Path(pxr.__file__).resolve().parents[3] / "bin" / "usdrecord"
        return str(cand) if cand.exists() else None
    except ImportError:
        return None


def render_thumbnail(file_path: str, asset_id: str) -> str | None:
    """Render a review thumbnail with usdrecord (Storm). The reviewer must
    see WHAT the object is — the filename may have nothing to do with it."""
    import subprocess
    usdrecord = _find_usdrecord()
    if not usdrecord:
        return None
    thumbs = QUEUE_DIR / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    out = thumbs / f"{asset_id}.png"
    try:
        subprocess.run(
            [usdrecord, "--renderer", "Storm", "--imageWidth", "512",
             file_path, str(out)],
            capture_output=True, timeout=180, check=False)
    except Exception:
        return None
    return str(out) if out.exists() else None


def render_views(file_path: str, asset_id: str, n_views: int = 4,
                 width: int = 512) -> list[str]:
    """Orbit renders for visual QA — one usdrecord run with a time-sampled
    camera circling the asset. Integrity judgment needs more than a front
    view: a missing back face, hollow interior, or untextured patch hides
    from a single frame."""
    import math
    import subprocess

    usdrecord = _find_usdrecord()
    if not usdrecord:
        return []
    from pxr import Gf, Sdf, Usd, UsdGeom

    src = Usd.Stage.Open(file_path)
    if not src:
        return []
    bbox = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
    ).ComputeWorldBound(src.GetPseudoRoot())
    rng = bbox.ComputeAlignedRange()
    if rng.IsEmpty():
        return []
    center = Gf.Vec3d(rng.GetMidpoint())
    # 3.2x the largest dimension keeps the whole object in frame at the
    # default 50mm-equivalent lens (~24 deg FOV)
    dist = (3.2 * max(rng.GetSize())) or 1.0
    z_up = str(UsdGeom.GetStageUpAxis(src)).upper() == "Z"
    default = src.GetDefaultPrim()
    default_path = str(default.GetPath()) if default else None
    del src

    thumbs = QUEUE_DIR / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    for stale in thumbs.glob(f"{asset_id}__view.*.png"):
        stale.unlink()
    orbit = thumbs / f"{asset_id}__orbit.usda"
    layer = Sdf.Layer.Find(str(orbit))
    if layer:
        layer.Clear()
        stage = Usd.Stage.Open(layer)
    else:
        if orbit.exists():
            orbit.unlink()
        stage = Usd.Stage.CreateNew(str(orbit))
    UsdGeom.SetStageUpAxis(
        stage, UsdGeom.Tokens.z if z_up else UsdGeom.Tokens.y)
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(n_views)
    asset = stage.DefinePrim("/Asset", "Xform")
    if default_path:
        asset.GetReferences().AddReference(file_path, default_path)
    else:
        asset.GetReferences().AddReference(file_path)
    cam = UsdGeom.Camera.Define(stage, "/OrbitCam")
    cam.GetClippingRangeAttr().Set(Gf.Vec2f(dist * 0.01, dist * 10.0))
    up = Gf.Vec3d(0, 0, 1) if z_up else Gf.Vec3d(0, 1, 0)
    xf = cam.AddTransformOp()
    elev = math.radians(20.0)
    for i in range(n_views):
        az = 2.0 * math.pi * i / n_views
        if z_up:
            off = Gf.Vec3d(math.sin(az) * math.cos(elev),
                           -math.cos(az) * math.cos(elev),
                           math.sin(elev))
        else:
            off = Gf.Vec3d(math.sin(az) * math.cos(elev),
                           math.sin(elev),
                           math.cos(az) * math.cos(elev))
        view = Gf.Matrix4d()
        view.SetLookAt(center + dist * off, center, up)
        xf.Set(view.GetInverse(), Usd.TimeCode(i + 1))
    stage.GetRootLayer().Save()
    del stage
    try:
        subprocess.run(
            [usdrecord, "--renderer", "Storm", "--imageWidth", str(width),
             "--camera", "/OrbitCam", "--frames", f"1:{n_views}",
             str(orbit), str(thumbs / f"{asset_id}__view.#.png")],
            capture_output=True, timeout=300, check=False)
    except Exception:
        return []
    return sorted(str(p) for p in thumbs.glob(f"{asset_id}__view.*.png"))


def refresh_renders(entry: dict) -> None:
    """(Re)render the hero thumbnail and orbit views from the entry's
    CURRENT file. Must run after every corrective action — a judge (human
    or model) approving from a pre-fix render approves the wrong asset."""
    entry["thumbnail"] = render_thumbnail(entry["file"], entry["asset_id"])
    entry["views"] = render_views(entry["file"], entry["asset_id"])


def apply_rigid_physics(entry: dict) -> str | None:
    """Author rigid physics on the entry's derivative (deterministic:
    collision + class material + class-plausible mass). Returns a note, or
    None when not applicable. Never applied to articulable assets — their
    joints must be authored first."""
    import contextlib
    import io
    import types

    report = entry.get("report", {})
    if report.get("skeleton"):
        return ("rigged character (UsdSkel): kinematic animated collider — "
                "no dynamic rigid body authored")
    if needs_articulation(report):
        return None
    dtype = _deformable_type(report)
    if dtype:
        # a rigid shell on a soft body is a physics lie — deformable APIs
        # are PhysX-only (unavailable headless), so authoring happens live
        # via create_deformable_mesh at scene build / verification
        entry["deformable"] = dtype
        return (f"deformable class ({dtype}): rigid physics skipped; "
                f"soft-body authored live via create_deformable_mesh")
    if report.get("structure", {}).get("rigid_bodies"):
        return None
    from pxr import Usd

    from service.isaac_assist_service.chat.tools.handlers.physics import (
        _gen_make_sim_ready,
        _load_asset_priors,
    )

    if "original_file" not in entry or not str(entry["file"]).endswith(".usda"):
        entry.setdefault("original_file", entry["file"])
        entry["file"] = build_wrapper(entry, None)
    stage = Usd.Stage.Open(entry["file"])
    omni = types.ModuleType("omni")
    omni_usd = types.ModuleType("omni.usd")
    ctx = type("Ctx", (), {"get_stage": lambda self: stage})()
    omni_usd.get_context = lambda: ctx
    omni.usd = omni_usd
    sys.modules["omni"] = omni
    sys.modules["omni.usd"] = omni_usd

    cls = report.get("matched_class")
    prior = _load_asset_priors().get("classes", {}).get(cls or "", {})
    mats = prior.get("typical_materials") or []
    mass_range = prior.get("mass_kg")
    profile = ("furniture" if cls in ("table", "cabinet", "door",
                                      "appliance_large", "medical_furniture")
               else "manipulable")
    args = {"prim_path": f"/World/{_camel(entry['asset_id'])}", "profile": profile}
    if mats:
        args["material"] = mats[0]
    if mass_range and profile == "manipulable":
        # bbox volume x material density x hollow-fill, CLAMPED into the
        # class range — a bare class midpoint ignores size (it gave a
        # computer mouse 1.26 kg and a crayon box 10 kg)
        est = None
        try:
            from pxr import UsdGeom
            rng = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            ).ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
            if not rng.IsEmpty():
                s = rng.GetSize()
                mpu = UsdGeom.GetStageMetersPerUnit(stage)
                vol = abs(s[0] * s[1] * s[2]) * mpu ** 3
                from service.isaac_assist_service.chat.tools.handlers.physics import (  # noqa: E501
                    _load_physics_materials,
                )
                db = _load_physics_materials()["materials"]
                density = (db.get(mats[0], {}).get("density_kg_m3", 1000.0)
                           if mats else 1000.0)
                est = vol * density * 0.3
        except Exception:
            est = None
        if est is None:
            est = (mass_range[0] + mass_range[1]) / 2.0
        args["mass_kg"] = round(
            min(max(est, mass_range[0]), mass_range[1]), 4)
    code = _gen_make_sim_ready(args)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<auto-physics>", "exec"), {"__builtins__": __builtins__})
    stage.GetRootLayer().Save()
    del stage
    return (f"auto physics: {profile}, {mats[0] if mats else 'no material (no class)'}"
            + (f", {args['mass_kg']} kg" if args.get("mass_kg") else " (bbox mass)"))


def queue_file(file_path: str, class_hint: str | None = None,
               asset_id: str | None = None, auto_fix_scale: bool = True) -> dict:
    """Run the ingest report on one file and write its review-queue entry.

    Deterministic mechanical fixes are applied at ingest, not held for
    review: a scale error with a suggested correction factor automatically
    produces a corrected meters/Z-up derivative, which is re-checked. The
    applied fix is recorded on the entry; the ORIGINAL file is preserved
    and approval still requires a human.
    """
    file_path = str(Path(file_path).resolve())
    features = scan_scene_features(file_path)
    report = run_report(file_path, class_hint)
    report.update(features)
    asset_id = asset_id or _asset_id_for(file_path)
    entry = {
        "asset_id": asset_id,
        "file": file_path,
        "class_hint": class_hint,
        "source_mtime": Path(file_path).stat().st_mtime,
        "queued": date.today().isoformat(),
        "proposed_category": propose_category(report),
        "status": "pending_review",
        "report": report,
    }
    factor = report.get("suggested_scale_correction")
    if auto_fix_scale and factor:
        entry["original_file"] = file_path
        entry["file"] = build_wrapper(entry, float(factor))
        entry["applied_fixes"] = [
            f"auto scale x{factor} (source units x{report.get('meters_per_unit')}, "
            f"up-axis {report.get('up_axis')} -> Z)"]
        entry["report"] = run_report(entry["file"], class_hint)
        entry["report"].update(features)
        entry["proposed_category"] = propose_category(entry["report"])
    # deterministic physics for non-articulable rigids happens at ingest
    # too — no button, no waiting (articulable assets get physics at
    # promote, after their joints are authored)
    if auto_fix_scale:
        note = apply_rigid_physics(entry)
        if note:
            entry.setdefault("applied_fixes", []).append(note)
            entry["report"] = run_report(entry["file"], class_hint)
            entry["report"].update(features)
            entry["proposed_category"] = propose_category(entry["report"])
    # Validate the final derivative, after any scale/physics fixes.  The hook
    # is a no-op unless the isolated NVIDIA sidecar exists (or is forced on).
    nvidia_validation = optional_nvidia_validation(entry["file"])
    if nvidia_validation is not None:
        entry.setdefault("validation", {})["nvidia_usd"] = nvidia_validation
    # the class from name matching is only a guess until a human (or VLM)
    # looks at the object itself
    entry["class_source"] = "hint" if class_hint else "filename_guess"
    refresh_renders(entry)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (QUEUE_DIR / f"{asset_id}.json").write_text(json.dumps(entry, indent=1))
    return entry


def _already_processed(file_path: str) -> str | None:
    """Reason to skip this source file, or None to ingest it."""
    src = str(Path(file_path).resolve())
    asset_id = _asset_id_for(src)
    qf = QUEUE_DIR / f"{asset_id}.json"
    if qf.exists():
        try:
            e = json.loads(qf.read_text())
            same = src in (e.get("file"), e.get("original_file"))
            if not same:
                return f"asset_id collision with {e.get('file')}"
            if e.get("status") in ("approved", "rejected"):
                return f"already reviewed ({e['status']})"
            if e.get("applied_fixes") or e.get("original_file"):
                return "in review (corrective fixes applied)"
            if e.get("source_mtime") in (None, Path(src).stat().st_mtime):
                return f"already queued ({e.get('status', '?')})"
            return None  # source changed on disk — re-ingest
        except (json.JSONDecodeError, OSError):
            return None
    reg_path = REPO / "workspace" / "knowledge" / "sim_ready_assets.json"
    if reg_path.exists():
        try:
            reg = json.loads(reg_path.read_text())
            for a in reg.get("assets", []):
                if src in (a.get("source_file"), a.get("file")):
                    return f"already in registry ({a.get('category')})"
        except (json.JSONDecodeError, OSError):
            pass
    return None


def scan_dir(directory: str, limit: int = 0, max_size_mb: float = 200.0) -> dict:
    """Discover USD assets under a directory and queue new/changed ones.

    Returns {'queued': [...], 'skipped': [(file, reason)], 'errors': [...]}.
    """
    import os as _os
    root = Path(directory).expanduser().resolve()
    out = {"queued": [], "skipped": [], "errors": []}
    exclude = [t.strip() for t in _os.environ.get(
        "ASSET_SCAN_EXCLUDE", "").split(",") if t.strip()]
    candidates = sorted(
        p for p in root.rglob("*")
        if p.suffix.lower() in _USD_EXTS and p.is_file()
        and ".thumb." not in p.name.lower()  # Omniverse preview stages
    )
    # Package awareness: only the TOP-MOST USD in a subtree is an asset.
    # Collected_/Lightwheel/SimReady packages keep their stage at the
    # package root with a payload of component USDs (materials, props,
    # parts) below — wrapping those individually would flood the queue
    # with non-assets. A USD whose ancestor directory (inside the scan
    # root) directly contains another USD is a component of that package.
    dirs_with_usd = {p.parent for p in candidates}

    def _component_of(p: Path) -> Path | None:
        anc = p.parent.parent
        while root in anc.parents or anc == root:
            if anc == root:  # loose files at the root never suppress subdirs
                return None
            if anc in dirs_with_usd:
                return anc
            anc = anc.parent
        return None
    for p in candidates:
        if limit and len(out["queued"]) >= limit:
            out["skipped"].append((str(p), f"scan limit {limit} reached"))
            continue
        rel = str(p.relative_to(root))
        if any(t in rel for t in exclude):
            out["skipped"].append((str(p), "ASSET_SCAN_EXCLUDE match"))
            continue
        pkg = _component_of(p)
        if pkg is not None:
            out["skipped"].append(
                (str(p), f"component of package {pkg.name}"))
            continue
        size_mb = p.stat().st_size / 1e6
        if size_mb > max_size_mb:
            out["skipped"].append((str(p), f"{size_mb:.0f} MB > {max_size_mb:.0f} MB cap"))
            continue
        reason = _already_processed(str(p))
        if reason:
            out["skipped"].append((str(p), reason))
            continue
        try:
            entry = queue_file(str(p))
            out["queued"].append(entry)
        except Exception as e:
            out["errors"].append((str(p), str(e)[:200]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", nargs="?", help="USD/USDZ asset file")
    ap.add_argument("--scan", metavar="DIR",
                    help="recursively queue every new/changed USD asset under DIR")
    ap.add_argument("--limit", type=int, default=0,
                    help="max new assets to queue per scan (0 = no limit)")
    ap.add_argument("--max-size-mb", type=float, default=200.0,
                    help="skip files larger than this (default 200 MB)")
    ap.add_argument("--class-hint", default=None)
    ap.add_argument("--id", default=None, help="asset_id (default: from filename)")
    ns = ap.parse_args()

    try:
        import pxr  # noqa: F401
    except ImportError:
        print("error: pxr not importable — set PYTHONPATH/LD_LIBRARY_PATH to an "
              "OpenUSD build (see launch_review_hub.sh)", file=sys.stderr)
        return 1

    if ns.scan:
        res = scan_dir(ns.scan, limit=ns.limit, max_size_mb=ns.max_size_mb)
        for e in res["queued"]:
            errs = sum(1 for c in e["report"].get("callouts", [])
                       if c["severity"] == "error")
            print(f"queued {e['asset_id']:40s} {e['report'].get('verdict', '')[:40]:42s}"
                  f" ({errs} errors)")
        for f, why in res["errors"]:
            print(f"ERROR  {f}: {why}")
        skipped_counts = {}
        for _, why in res["skipped"]:
            key = why.split("(")[0].strip()
            skipped_counts[key] = skipped_counts.get(key, 0) + 1
        summary = ", ".join(f"{v}x {k}" for k, v in skipped_counts.items())
        print(f"\n{len(res['queued'])} queued, {len(res['skipped'])} skipped"
              + (f" ({summary})" if summary else "")
              + (f", {len(res['errors'])} errors" if res["errors"] else ""))
        print("review at the asset review hub (launch_review_hub.sh)")
        return 0

    if not ns.file:
        ap.error("provide a FILE or --scan DIR")
    file_path = str(Path(ns.file).resolve())
    if not Path(file_path).exists():
        print(f"error: no such file: {file_path}", file=sys.stderr)
        return 1
    entry = queue_file(file_path, ns.class_hint, ns.id)
    report = entry["report"]
    errors = [c for c in report.get("callouts", []) if c["severity"] == "error"]
    print(f"queued {entry['asset_id']}: {report.get('verdict')}"
          f" ({len(errors)} errors, {len(report.get('callouts', []))} callouts)")
    print(f"  -> {QUEUE_DIR / (entry['asset_id'] + '.json')}")
    print("  review at the asset review hub (launch_review_hub.sh)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
