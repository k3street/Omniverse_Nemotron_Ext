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
    try:
        from service.isaac_assist_service.analysis.validators.nvidia_usd_validation import (
            findings_record,
            resolve_validator_command,
            validate_asset,
        )
    except ImportError:
        # The validators package needs the service's dependencies (pydantic),
        # which a bare OpenUSD interpreter does not have. "auto" means
        # optional; an explicit request should still fail loudly.
        if mode == "auto":
            return None
        raise

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


BACKDROP_FLATNESS = 0.001  # thinnest / largest: a single quad, not a thin panel (a door)
BACKDROP_SPREAD = 2.5      # a backdrop dwarfs everything else this many times


def find_backdrops(file_path: str) -> list[str]:
    """Ground planes and backdrops the model was presented on (a Sketchfab
    scene's floor): flat meshes far wider than everything else, lying at
    the bottom or back of it. They are not part of the object, and they
    make it measure as large as the plane."""
    try:
        from pxr import Usd, UsdGeom
    except ImportError:
        return []
    stage = Usd.Stage.Open(file_path)
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    boxes = {}
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            r = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if not r.IsEmpty():
                boxes[str(prim.GetPath())] = (list(r.GetMin()), list(r.GetMax()))
    if len(boxes) < 2:
        return []
    out = []
    for path, (lo, hi) in boxes.items():
        size = [hi[k] - lo[k] for k in range(3)]
        thin = min(range(3), key=lambda k: size[k])
        if size[thin] > BACKDROP_FLATNESS * max(size):
            continue
        rest = [b for q, b in boxes.items() if q != path and q not in out]
        if not rest:
            continue
        r_lo = [min(b[0][k] for b in rest) for k in range(3)]
        r_hi = [max(b[1][k] for b in rest) for k in range(3)]
        r_size = [r_hi[k] - r_lo[k] for k in range(3)]
        spread = all(size[k] >= BACKDROP_SPREAD * max(r_size[k], 1e-9) for k in range(3) if k != thin)
        # it sits at one end of the rest along its thin axis, not through the middle
        tol = 0.05 * max(r_size)
        at_end = lo[thin] <= r_lo[thin] + tol or hi[thin] >= r_hi[thin] - tol
        if spread and at_end:
            out.append(path)
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
    # only characters USD takes in a prim name: Python's \w lets through '²'
    # ("..._4096px².usdz"), which USD refuses - and that refusal ended a run
    try:
        from pxr import Sdf
        name = "".join(c for c in name if Sdf.Path.IsValidIdentifier("A" + c)) or "Asset"
    except ImportError:
        name = re.sub(r"[^0-9A-Za-z_]", "", name) or "Asset"
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
    src_root = str(default.GetPath()) if default else ""
    for path in entry.get("backdrops", []):
        if src_root and path.startswith(src_root + "/"):
            stage.OverridePrim(str(asset.GetPath()) + path[len(src_root):]).SetActive(False)
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
    """The review thumbnail: one three-quarter view, framed and lit like the
    orbit views. The reviewer (and the VLM) must see WHAT the object is —
    the filename may have nothing to do with it."""
    import shutil

    frames = _orbit_render(file_path, f"{asset_id}__hero", [THREE_QUARTER_AZ_DEG])
    if not frames:
        return None
    out = QUEUE_DIR / "thumbs" / f"{asset_id}.png"
    shutil.move(frames[0], out)
    return str(out)


def render_views(file_path: str, asset_id: str, n_views: int = 4,
                 width: int = 512) -> list[str]:
    """Orbit renders for visual QA. Integrity judgment needs more than a
    front view: a missing back face, hollow interior, or untextured patch
    hides from a single frame."""
    return _orbit_render(file_path, f"{asset_id}__view",
                         [THREE_QUARTER_AZ_DEG + 360.0 * i / n_views for i in range(n_views)], width)


THREE_QUARTER_AZ_DEG = 35.0
ELEVATION_DEG = 25.0
FRAME_MARGIN = 1.15


def _orbit_render(file_path: str, prefix: str, azimuths: list[float],
                  width: int = 512) -> list[str]:
    """One usdrecord (Storm) run with a time-sampled camera circling the
    asset: <prefix>.<n>.png per azimuth. The camera frames the bounding
    sphere in the narrower (vertical) field of view, so nothing is cropped
    and the object fills the frame; a dome and a key light are added when
    the asset brings none (unlit metal renders as a black silhouette)."""
    import math
    import subprocess

    usdrecord = _find_usdrecord()
    if not usdrecord:
        return []
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux

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
    radius = 0.5 * rng.GetSize().GetLength() or 0.5
    z_up = str(UsdGeom.GetStageUpAxis(src)).upper() == "Z"
    has_lights = any(p.HasAPI(UsdLux.LightAPI) for p in src.Traverse())
    default = src.GetDefaultPrim()
    default_path = str(default.GetPath()) if default else None
    del src

    thumbs = QUEUE_DIR / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    for stale in thumbs.glob(f"{prefix}.*.png"):
        stale.unlink()
    orbit = thumbs / f"{prefix}__orbit.usda"
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
    stage.SetEndTimeCode(len(azimuths))
    asset = stage.DefinePrim("/Asset", "Xform")
    if default_path:
        asset.GetReferences().AddReference(file_path, default_path)
    else:
        asset.GetReferences().AddReference(file_path)
    cam = UsdGeom.Camera.Define(stage, "/OrbitCam")
    # usdrecord keeps the aperture's aspect: the vertical view is the narrow one
    v_half = math.atan(0.5 * cam.GetVerticalApertureAttr().Get() / cam.GetFocalLengthAttr().Get())
    dist = FRAME_MARGIN * radius / math.sin(v_half)
    cam.GetClippingRangeAttr().Set(Gf.Vec2f(dist * 0.01, dist * 10.0))
    up = Gf.Vec3d(0, 0, 1) if z_up else Gf.Vec3d(0, 1, 0)
    if not has_lights:
        UsdLux.DomeLight.Define(stage, "/Lights/Dome").CreateIntensityAttr(0.6)
        key = UsdLux.DistantLight.Define(stage, "/Lights/Key")
        key.CreateIntensityAttr(1.5)
        key.CreateAngleAttr(1.0)
        UsdGeom.XformCommonAPI(key).SetRotate(Gf.Vec3f(-45, 0, -30) if z_up else Gf.Vec3f(-45, -30, 0))
    xf = cam.AddTransformOp()
    elev = math.radians(ELEVATION_DEG)
    for i, az_deg in enumerate(azimuths):
        az = math.radians(az_deg)
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
             "--camera", "/OrbitCam", "--frames", f"1:{len(azimuths)}",
             str(orbit), str(thumbs / f"{prefix}.#.png")],
            capture_output=True, timeout=300, check=False)
    except Exception:
        return []
    return sorted(str(p) for p in thumbs.glob(f"{prefix}.*.png"))


def refresh_renders(entry: dict) -> None:
    """(Re)render the hero thumbnail and orbit views from the entry's
    CURRENT file. Must run after every corrective action — a judge (human
    or model) approving from a pre-fix render approves the wrong asset."""
    entry["thumbnail"] = render_thumbnail(entry["file"], entry["asset_id"])
    entry["views"] = render_views(entry["file"], entry["asset_id"])


SMALL_PART_M = 0.03  # below this, PhysX's default contact offset is a sizeable fraction of the part


def _tune_small_colliders(stage, root_path: str) -> int:
    """Tighter contact and rest offsets for colliders smaller than SMALL_PART_M.

    PhysX's default contact offset scales with the scene, not the shape; on a
    2 cm ring or a watch pin it is a large fraction of the part, so contacts
    start early and small parts hover or jitter. Authored as raw
    PhysxCollisionAPI attributes (PhysxSchema is not in a bare OpenUSD build).
    """
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    tuned = 0
    for prim in Usd.PrimRange(stage.GetPrimAtPath(root_path)):
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        size = cache.ComputeWorldBound(prim).ComputeAlignedRange().GetSize()
        if max(size) * UsdGeom.GetStageMetersPerUnit(stage) >= SMALL_PART_M:
            continue
        offset = max(0.0005, 0.05 * min(s for s in size if s > 0) * UsdGeom.GetStageMetersPerUnit(stage))
        prim.AddAppliedSchema("PhysxCollisionAPI")
        prim.CreateAttribute("physxCollision:contactOffset", Sdf.ValueTypeNames.Float).Set(float(offset))
        prim.CreateAttribute("physxCollision:restOffset", Sdf.ValueTypeNames.Float).Set(0.0)
        tuned += 1
    return tuned


def apply_rigid_physics(entry: dict, provisional: bool = False) -> str | None:
    """Author rigid physics on the entry's derivative (deterministic:
    collision + class material + class-plausible mass). Returns a note, or
    None when not applicable. Not applied to articulable assets — their
    joints come first — unless provisional: one rigid body until a reviewer
    articulates it (articulate_asset re-authors the bodies), so the asset
    simulates as a solid object meanwhile instead of not at all."""
    import contextlib
    import io
    import types

    report = entry.get("report", {})
    if report.get("skeleton"):
        return ("rigged character (UsdSkel): kinematic animated collider — "
                "no dynamic rigid body authored")
    if needs_articulation(report) and not provisional:
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
    if prior.get("multi_body"):
        note = apply_set_physics(stage, f"/World/{_camel(entry['asset_id'])}", prior)
        stage.GetRootLayer().Save()
        del stage
        return note
    mats = prior.get("typical_materials") or []
    mass_range = prior.get("mass_kg")
    profile = ("furniture" if cls in ("table", "cabinet", "door",
                                      "appliance_large", "medical_furniture")
               else "manipulable")
    args = {"prim_path": f"/World/{_camel(entry['asset_id'])}", "profile": profile}
    if prior.get("collision_approximation"):
        # a ring's convex hull fills its hole: concave classes say so
        args["approximation"] = prior["collision_approximation"]
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
    # the VLM saw several objects in the file: a body each, not one welded lump
    if (entry.get("vlm") or {}).get("content_kind") == "object_set" and profile == "manipulable" \
            and len(set_members(stage, args["prim_path"])) > 1:
        note = apply_group_physics(stage, args["prim_path"], prior,
                                   args.get("mass_kg") or sum(mass_range or [0.1, 0.1]) / 2)
        stage.GetRootLayer().Save()
        del stage
        return note
    code = _gen_make_sim_ready(args)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<auto-physics>", "exec"), {"__builtins__": __builtins__})
    small = _tune_small_colliders(stage, args["prim_path"])
    stage.GetRootLayer().Save()
    del stage
    return (f"auto physics: {profile}, {mats[0] if mats else 'no material (no class)'}"
            + (f", collider {args['approximation']}" if args.get("approximation") else "")
            + (f", small-part contact offsets on {small} colliders" if small else "")
            + (f", {args['mass_kg']} kg" if args.get("mass_kg") else " (bbox mass)"))


def set_members(stage, asset_root: str) -> list:
    """The separate objects of a set: the children of the meshes' deepest
    common ancestor that hold meshes (descending through wrapper chains)."""
    from pxr import Usd, UsdGeom

    paths = [p.GetPath() for p in Usd.PrimRange(stage.GetPrimAtPath(asset_root))
             if p.IsA(UsdGeom.Mesh) and p.IsActive()]
    if not paths:
        return []
    common = paths[0].GetParentPath()
    while not all(q.HasPrefix(common) for q in paths):
        common = common.GetParentPath()
    while True:
        members = [c for c in stage.GetPrimAtPath(common).GetChildren()
                   if any(q.HasPrefix(c.GetPath()) for q in paths)]
        if len(members) != 1:
            return members
        common = members[0].GetPath()


def apply_set_physics(stage, asset_root: str, prior: dict) -> str:
    """One rigid body per member of a set (chess pieces, dominoes), so they
    move independently; one body over the whole file would weld them."""
    import contextlib
    import io

    from pxr import Usd, UsdGeom

    from service.isaac_assist_service.chat.tools.handlers.physics import (
        _gen_make_sim_ready,
        _load_asset_priors,
        _load_physics_materials,
    )

    mb = prior["multi_body"]
    classes = _load_asset_priors().get("classes", {})
    member = classes.get(mb.get("member_class", ""), {})
    m_mats = member.get("typical_materials") or prior.get("typical_materials") or []
    s_mats = prior.get("typical_materials") or m_mats
    m_range = member.get("mass_kg") or [0.01, 1.0]
    density = _load_physics_materials()["materials"].get(m_mats[0] if m_mats else "", {}).get(
        "density_kg_m3", 1000.0)
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    members = set_members(stage, asset_root)
    surfaces, total = 0, 0.0
    for prim in members:
        surface = any(k in prim.GetName().lower() for k in mb.get("surface_keywords", []))
        if surface:
            mats, mass = s_mats, float(mb.get("surface_mass_kg", 1.0))
            surfaces += 1
        else:
            size = cache.ComputeWorldBound(prim).ComputeAlignedRange().GetSize()
            vol = abs(size[0] * size[1] * size[2]) * mpu ** 3
            mass = min(max(vol * density * float(mb.get("member_fill", 0.45)), m_range[0]), m_range[1])
            mats = m_mats
        args = {"prim_path": str(prim.GetPath()), "profile": "manipulable", "mass_kg": round(mass, 4)}
        if surface and mb.get("surface_approximation"):
            args["approximation"] = mb["surface_approximation"]
        if mats:
            args["material"] = mats[0]
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(_gen_make_sim_ready(args), "<set-physics>", "exec"),
                 {"__builtins__": __builtins__})
        total += mass
    return (f"set physics: {len(members) - surfaces} separate bodies + {surfaces} surface, "
            f"{round(total, 3)} kg total")


def object_groups(stage, asset_root: str, touch: bool = True) -> list[list]:
    """The separate objects in a file that holds several (a bottle and its
    dropper lying beside it; a screw and a loose washer): its members joined
    when one sits within the other's box (protruding less than half its own
    size) or their surfaces touch. A
    bottle modelled as glass, liquid and label is one object; a pestle
    resting in its mortar is two."""
    import numpy as np
    from pxr import Gf, Usd, UsdGeom

    members = set_members(stage, asset_root)
    if len(members) < 2:
        return [members]
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    boxes = [cache.ComputeWorldBound(m).ComputeAlignedRange() for m in members]
    span = max(max(b.GetMax()[k] for b in boxes) - min(b.GetMin()[k] for b in boxes) for k in range(3))
    rng = np.random.default_rng(0)

    def points(prim):
        pts = []
        for p in Usd.PrimRange(prim):
            if p.IsA(UsdGeom.Mesh):
                m = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0)
                pts += [list(m.Transform(Gf.Vec3d(*v))) for v in UsdGeom.Mesh(p).GetPointsAttr().Get()]
        a = np.array(pts)
        return a[rng.choice(len(a), min(1500, len(a)), replace=False)] if len(a) else a

    pts = [points(m) for m in members]
    parent = list(range(len(members)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            a, b = boxes[i], boxes[j]
            if a.IsEmpty() or b.IsEmpty():
                continue
            # one sitting within the other is one object (liquid in its
            # glass, a bulb on its dropper); one resting in it and standing
            # well out of it is not (a pestle in its mortar, tubes in a rack)
            def held(inner, outer):
                if not outer.Contains(inner.GetMidpoint()):
                    return False
                lo_i, hi_i, lo_o, hi_o = inner.GetMin(), inner.GetMax(), outer.GetMin(), outer.GetMax()
                for k in range(3):
                    ext = max(hi_i[k] - lo_i[k], 1e-12)
                    out = max(0.0, lo_o[k] - lo_i[k]) + max(0.0, hi_i[k] - hi_o[k])
                    if out > 0.5 * ext:
                        return False
                return True

            joined = held(b, a) or held(a, b)
            if touch and not joined and not Gf.Range3d.GetIntersection(a, b).IsEmpty() and len(pts[i]) and len(pts[j]):
                gap = min(float(np.linalg.norm(pts[j] - x, axis=1).min()) for x in pts[i])
                joined = gap < 0.002 * span
            if joined:
                parent[find(i)] = find(j)
    groups = {}
    for i, m in enumerate(members):
        groups.setdefault(find(i), []).append((m, boxes[i]))
    vol = lambda b: max(1e-18, b.GetSize()[0] * b.GetSize()[1] * b.GetSize()[2])  # noqa: E731
    return [[m for m, _ in sorted(g, key=lambda t: -vol(t[1]))] for g in groups.values()]


def apply_group_physics(stage, asset_root: str, prior: dict, total_mass: float) -> str:
    """A rigid body per object in a set file; an object's other meshes are
    fixed to its largest, and the mass is shared by bounding volume."""
    import contextlib
    import io

    from pxr import Usd, UsdGeom, UsdPhysics

    from service.isaac_assist_service.chat.tools.handlers.physics import _gen_make_sim_ready

    groups = object_groups(stage, asset_root)
    if len(groups) == 1 and len(groups[0]) > 1:
        # the VLM sees separate objects that touch (a pestle resting on its
        # bowl): contact cannot tell resting from attached, so count only
        # parts held inside another as one object
        groups = object_groups(stage, asset_root, touch=False)
    if len(groups) == 1 and len(groups[0]) > 1:
        # still one: a pestle lying across its bowl, tubes standing in their
        # rack. The VLM sees separate objects, so its members are they
        groups = [[m] for m in groups[0]]
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])

    def vol(prim):
        s = cache.ComputeWorldBound(prim).ComputeAlignedRange().GetSize()
        return max(1e-18, abs(s[0] * s[1] * s[2]))

    vols = {str(m.GetPath()): vol(m) for g in groups for m in g}
    whole = sum(vols.values())
    mats = prior.get("typical_materials") or []
    UsdGeom.Scope.Define(stage, f"{asset_root}/Joints")
    n_joints = 0
    for gi, group in enumerate(groups):
        for m in group:
            args = {"prim_path": str(m.GetPath()), "profile": "manipulable",
                    "mass_kg": round(max(1e-4, total_mass * vols[str(m.GetPath())] / whole), 5),
                    # objects in a set rest in each other (tubes in a rack, a
                    # pestle in its bowl): a convex hull fills the holes they
                    # sit in and throws them out at the first step
                    "approximation": prior.get("collision_approximation") or "convexDecomposition"}
            if mats:
                args["material"] = mats[0]
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(_gen_make_sim_ready(args), "<group-physics>", "exec"),
                     {"__builtins__": __builtins__})
        # nested meshes of one object (liquid inside its glass) overlap: their
        # colliders would push apart against the joints holding them
        for a in group:
            fp = UsdPhysics.FilteredPairsAPI.Apply(a)
            for b in group:
                if b != a:
                    fp.CreateFilteredPairsRel().AddTarget(b.GetPath())
        if len(group) > 1:
            # an articulation per object: its fixed joints are exact, where
            # joints between free bodies flex on impact (5 mm when a dropper
            # with a light bulb fell 8 cm)
            UsdPhysics.ArticulationRootAPI.Apply(group[0])
            group[0].AddAppliedSchema("PhysxArticulationAPI")
            from pxr import Sdf
            group[0].CreateAttribute("physxArticulation:enabledSelfCollisions",
                                     Sdf.ValueTypeNames.Bool).Set(False)
        for m in group[1:]:
            j = UsdPhysics.FixedJoint.Define(stage, f"{asset_root}/Joints/object{gi}_{m.GetName()}")
            j.CreateBody0Rel().SetTargets([group[0].GetPath()])
            j.CreateBody1Rel().SetTargets([m.GetPath()])
            from add_mechanism import _joint_frames
            xf = UsdGeom.XformCache()
            anchor = cache.ComputeWorldBound(m).ComputeAlignedRange().GetMidpoint()
            _joint_frames(j, xf.GetLocalToWorldTransform(group[0]), xf.GetLocalToWorldTransform(m), anchor)
            n_joints += 1
    _tune_small_colliders(stage, asset_root)
    return (f"set physics: {len(groups)} separate objects "
            f"({', '.join(str(len(g)) for g in groups)} meshes), {n_joints} fixed joints, {round(total_mass, 4)} kg")


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
        "source_sha1": _file_sha1(file_path),
        "class_hint": class_hint,
        "source_mtime": Path(file_path).stat().st_mtime,
        "queued": date.today().isoformat(),
        "proposed_category": propose_category(report),
        "status": "pending_review",
        "report": report,
    }
    backdrops = find_backdrops(file_path)
    if auto_fix_scale and backdrops:
        # measure the object without the floor it was shown on
        entry["backdrops"] = backdrops
        entry["original_file"] = file_path
        entry["file"] = build_wrapper(entry, None)
        entry["applied_fixes"] = [f"backdrop removed: {', '.join(p.rsplit('/', 1)[-1] for p in backdrops)} "
                                  "(a flat plane far wider than the object)"]
        report = run_report(entry["file"], class_hint)
        report.update(features)
        entry["report"] = report
        entry["proposed_category"] = propose_category(report)
    factor = report.get("suggested_scale_correction")
    if auto_fix_scale and factor:
        entry.setdefault("original_file", file_path)
        entry["file"] = build_wrapper(entry, float(factor))
        entry.setdefault("applied_fixes", []).append(
            f"auto scale x{factor} (source units x{report.get('meters_per_unit')}, "
            f"up-axis {report.get('up_axis')} -> Z)")
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


def _file_sha1(path: str) -> str:
    import hashlib

    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_index() -> dict:
    """source content hash -> asset_id, for queue entries that recorded one."""
    index = {}
    for qf in QUEUE_DIR.glob("*.json"):
        try:
            e = json.loads(qf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if e.get("source_sha1"):
            index[e["source_sha1"]] = e.get("asset_id", qf.stem)
    return index


def resolve_identity(file_path: str, index: dict | None = None) -> tuple[str | None, str | None]:
    """(asset_id, skip reason). A file name is not an identity: two different
    files can share one (Remote_Control.usdz from two shops), and one file
    can arrive under two (Vilya.usdz, Vilya(1).usdz). Content decides."""
    src = str(Path(file_path).resolve())
    sha = _file_sha1(src)
    index = content_index() if index is None else index
    base = _asset_id_for(src)
    if sha in index:
        other = json.loads((QUEUE_DIR / f"{index[sha]}.json").read_text())
        if src not in (other.get("file"), other.get("original_file")):
            return None, f"same content as {index[sha]}"
    qf = QUEUE_DIR / f"{base}.json"
    if not qf.exists():
        return base, None
    e = json.loads(qf.read_text())
    if src in (e.get("file"), e.get("original_file")):
        return base, None  # the same file again: the usual re-ingest rules apply
    theirs = e.get("original_file") or e.get("file")
    try:
        # length first (different lengths cannot match), content only when equal
        if theirs and Path(theirs).exists() and Path(theirs).stat().st_size == Path(src).stat().st_size \
                and _file_sha1(theirs) == sha:
            return None, f"same content as {base} ({theirs})"
    except OSError:
        pass
    return f"{base}_{sha[:6]}", None  # same name, different file


def _already_processed(file_path: str, asset_id: str | None = None) -> str | None:
    """Reason to skip this source file, or None to ingest it."""
    src = str(Path(file_path).resolve())
    asset_id = asset_id or _asset_id_for(src)
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


def relink_sources(search_dir: str) -> list[str]:
    """Re-point queue entries whose source file moved.

    Wrappers reference their source by absolute path, so moving a download
    breaks every wrapper built on it. A moved source is found by content:
    an entry's recorded source_sha1 must match. Entries ingested before
    hashes were recorded can only be matched by file name; those relinks say
    so and record the hash from then on.
    """
    root = Path(search_dir).expanduser().resolve()
    by_name: dict[str, list[Path]] = {}
    for p in root.rglob("*"):
        if p.suffix.lower() in _USD_EXTS and p.is_file():
            by_name.setdefault(p.name, []).append(p)
    report = []
    by_hash = None
    for qf in sorted(QUEUE_DIR.glob("*.json")):
        e = json.loads(qf.read_text())
        src = e.get("original_file") or e.get("file")
        if not src or Path(src).exists():
            continue
        candidates = by_name.get(Path(src).name, [])
        want = e.get("source_sha1")
        match, how = None, ""
        for c in candidates:
            if want and _file_sha1(str(c)) == want:
                match, how = c, "content"
                break
        if match is None and not want and len(candidates) == 1:
            match, how = candidates[0], "name only (no recorded content hash)"
        if match is None and want:
            # renamed on the way (a same-named different file was already
            # there): find it by content alone
            if by_hash is None:
                by_hash = {}
                for paths in by_name.values():
                    for c in paths:
                        by_hash.setdefault(_file_sha1(str(c)), c)
            match = by_hash.get(want)
            how = "content (renamed)" if match is not None else how
        if match is None:
            report.append(f"{e['asset_id']}: source {src} missing, no match in {root}")
            continue
        new = str(match.resolve())
        wrapper = e.get("file")
        if wrapper and wrapper != src and Path(wrapper).exists() and wrapper.endswith(".usda"):
            text = Path(wrapper).read_text()
            Path(wrapper).write_text(text.replace(f"@{src}@", f"@{new}@"))
        if e.get("original_file") == src:
            e["original_file"] = new
        if e.get("file") == src:
            e["file"] = new
        e["source_sha1"] = want or _file_sha1(new)
        e.setdefault("applied_fixes", []).append(f"source relinked by {how}: {src} -> {new}")
        qf.write_text(json.dumps(e, indent=1))
        report.append(f"{e['asset_id']}: relinked by {how} -> {new}")
    return report


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
    index = content_index()
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
        asset_id, reason = resolve_identity(str(p), index)
        reason = reason or _already_processed(str(p), asset_id)
        if reason:
            out["skipped"].append((str(p), reason))
            continue
        try:
            entry = queue_file(str(p), asset_id=asset_id)
            if entry.get("source_sha1"):
                index[entry["source_sha1"]] = entry["asset_id"]
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
    ap.add_argument("--relink", metavar="DIR",
                    help="re-point queue entries whose source moved, finding it in DIR by content")
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

    if ns.relink:
        for line in relink_sources(ns.relink):
            print(line)
        return 0
    if not ns.file:
        ap.error("provide a FILE or --scan DIR")
    file_path = str(Path(ns.file).resolve())
    if not Path(file_path).exists():
        print(f"error: no such file: {file_path}", file=sys.stderr)
        return 1
    asset_id = ns.id
    if not asset_id:
        asset_id, reason = resolve_identity(file_path)
        if reason:
            print(f"skipped {Path(file_path).name}: {reason}")
            return 0
    entry = queue_file(file_path, ns.class_hint, asset_id)
    report = entry["report"]
    errors = [c for c in report.get("callouts", []) if c["severity"] == "error"]
    print(f"queued {entry['asset_id']}: {report.get('verdict')}"
          f" ({len(errors)} errors, {len(report.get('callouts', []))} callouts)")
    print(f"  -> {QUEUE_DIR / (entry['asset_id'] + '.json')}")
    print("  review at the asset review hub (launch_review_hub.sh)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
