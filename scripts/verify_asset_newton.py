#!/usr/bin/env python3
"""Headless physics verification with NVIDIA Newton (Warp) — no Isaac, no Kit.

Two tests the PhysX live path can't cover:

  rigid <id...>       Cross-engine drop test on REGISTRY assets: the same
                      USD that passed the live PhysX drop is re-run in
                      Newton. Agreement between two engines is a far
                      stronger sim2real claim than either alone; evidence
                      lands in verification.newton_1_5 on the registry entry.

  cable <path|id>     Dynamic-cord test with Newton rods + VBD (Vertex
                      Block Descent, SIGGRAPH 2024). Runs the cord at its
                      PHYSICAL linear-density mass — PhysX needed a 15 g
                      link floor and still coiled when slack. Criteria:
                      pulls straight under a 100 g load, and holds its bow
                      when slack instead of collapsing to a hairpin.

  drape <id...>       Soft-body drape test for QUEUE deformables: the
                      asset's mesh is dropped as a Newton cloth onto the
                      ground. Real cloth collapses (final height-extent a
                      fraction of initial); a rigid shell would not.
                      Evidence lands on the versioned queue entry for the
                      human reviewer — deformable sign-off stays human.

Run with the Newton venv:
    .venv-newton/bin/python scripts/verify_asset_newton.py rigid beer_bottle
    .venv-newton/bin/python scripts/verify_asset_newton.py drape disposable_medical_masks

Set NEWTON_FULL_SURFACE_CONTACT=1 to A/B Newton 1.5's opt-in VBD edge/face
contact generation in cloth_grasp. Baseline runs keep particle contact.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from newton_runtime import (collision_pipeline, require_newton_15,
                            runtime_label, runtime_metadata)

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"
REGISTRY = REPO / "workspace" / "knowledge" / "sim_ready_assets.json"

FRAME_DT = 1.0 / 60.0
SUBSTEPS = 10
RUBBER_DENSITY = 1200.0   # kg/m3, cable jacket (matches make_cable)
# Cloth damping, swept for the grasp test (see cloth_grasp): tri_kd is the
# only internal damping Newton's cloth exposes, and it is the difference
# between a garment that hangs and one that swings like a pendulum for the
# whole run. Overridable so the sweep is reproducible.
CLOTH_TRI_KD = float(os.environ.get("CLOTH_TRI_KD", "1.0e-1"))
# Viscous velocity damping (1/s) applied per frame. tri_kd is the only
# internal damping Newton cloth exposes and it detonates above ~0.1 at
# any substep count we can afford, so energy is bled off directly.
# This models fabric hysteresis + air resistance; it is what turns a
# garment that swings for the whole run into one that hangs.
CLOTH_DAMP_HZ = float(os.environ.get("CLOTH_DAMP_HZ", "2.0"))
CLOTH_TRI_DRAG = float(os.environ.get("CLOTH_TRI_DRAG", "5.0e-1"))
# tri_kd is EXPLICIT damping: it is stable only while dt < 2*m/kd, and a
# cloth particle weighs ~0.4 g. Raising kd without shrinking dt does not
# damp the swing, it detonates it. These two move together.
CLOTH_SUBSTEPS = int(os.environ.get("CLOTH_SUBSTEPS", "20"))


def _device():
    """Prefer CUDA — VBD is a GPU-parallel method (graph-coloured blocks),
    and Newton disables its tiled solve entirely on CPU. Falls back to CPU
    when the GPU is unavailable or already claimed (Isaac holds a primary
    context; that is what forced CPU earlier). Override: NEWTON_DEVICE.
    """
    import os

    _, wp = require_newton_15()

    want = os.environ.get("NEWTON_DEVICE")
    if want:
        wp.set_device(want)
        return want
    try:
        if wp.get_cuda_device_count() > 0:
            wp.set_device("cuda:0")
            wp.zeros(1, dtype=float)      # force context creation now
            return "cuda:0"
    except Exception:
        pass
    wp.set_device("cpu")
    return "cpu"


def _sim(model, solver, seconds: float, on_frame=None):
    import warp as wp  # noqa: F401

    pipeline, contacts = collision_pipeline(model)
    state0, state1 = model.state(), model.state()
    control = model.control()
    dt = FRAME_DT / SUBSTEPS
    frames = int(seconds / FRAME_DT)
    for f in range(frames):
        for _ in range(SUBSTEPS):
            state0.clear_forces()
            pipeline.collide(state0, contacts)
            solver.step(state0, state1, control, contacts, dt)
            state0, state1 = state1, state0
        if on_frame:
            on_frame(f, state0)
    return state0


def rigid_drop(asset_id: str) -> str:
    import newton
    import warp as wp

    reg = json.loads(REGISTRY.read_text())
    asset = next((a for a in reg["assets"] if a["asset_id"] == asset_id), None)
    if not asset:
        return f"{asset_id}: not in registry"
    usd_file = asset["file"]
    if not Path(usd_file).exists():
        return f"{asset_id}: file missing {usd_file}"

    drop_h = 0.1
    # spawn with the asset's BBOX BOTTOM drop_h above ground — an asset
    # whose geometry extends below its origin would otherwise spawn
    # intersecting the plane and XPBD ejects it violently
    from pxr import Usd, UsdGeom
    _st = Usd.Stage.Open(usd_file)
    _rng = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
    ).ComputeWorldBound(_st.GetPseudoRoot()).ComputeAlignedRange()
    z_bottom = float(_rng.GetMin()[2]) if not _rng.IsEmpty() else 0.0
    del _st
    spawn_z = drop_h - z_bottom

    builder = newton.ModelBuilder()
    builder.add_usd(
        usd_file,
        xform=wp.transform(wp.vec3(0.0, 0.0, spawn_z), wp.quat_identity()),
        collapse_fixed_joints=True,
        # Newton 1.5 imports static visual-only shapes by default. The drop
        # verifier needs collision geometry only; retaining render meshes
        # across a multi-asset batch can exhaust/crash the USD importer.
        load_visual_shapes=False,
    )
    if builder.body_count == 0:
        return f"{asset_id}: no rigid bodies parsed from USD"
    builder.add_ground_plane()
    model = builder.finalize()
    solver = newton.solvers.SolverXPBD(model, iterations=10)

    z0 = float(model.body_q.numpy()[0][2])
    trace = []
    state = _sim(model, solver, 4.0,
                 on_frame=lambda f, s: trace.append(
                     float(s.body_q.numpy()[0][2])))
    zf = trace[-1]
    vz = abs(float(state.body_qd.numpy()[0][5]))
    dropped = z0 - zf
    last_second = trace[-60:]
    settled = max(last_second) - min(last_second) < 0.01
    # rest-on-ground criterion tolerating settle-tipping AND rolling
    # (a beer can rolls at constant height forever — that IS resting).
    # Settling alone rules out tunneling and explosion: a body that fell
    # through the plane or got ejected never holds a stable height.
    rests = settled and dropped > 0.03
    evidence = {
        "date": date.today().isoformat(),
        "method": "headless_newton_drop_test",
        "engine": runtime_label(
            f"SolverXPBD, headless on {wp.get_device().name}"),
        **runtime_metadata(),
        "cross_engine_ok": rests,
        "drop": {"drop_measured_m": round(dropped, 4),
                 "drop_predicted_m": drop_h,
                 "settled_last_second": settled,
                 "final_vz": round(vz, 4),
                 "rests_after_s": 4.0},
    }
    verdict = "PASS" if rests else "FAIL"
    # Failure evidence is as important as a pass during an engine migration;
    # keep it under the versioned key without overwriting the Newton 0.2 run.
    asset.setdefault("verification", {})
    asset["verification"]["newton_1_5"] = evidence
    REGISTRY.write_text(json.dumps(reg, indent=1))
    return (f"{verdict} {asset_id}: dropped {dropped:.4f} m "
            f"(predicted {drop_h}), settled={settled}, vz={vz:.4f}"
            + " — cross-engine evidence written")


def _world_mesh(usd_file: str):
    """ALL meshes under the stage merged, points in world space (meters) —
    the first mesh alone can be a trivial part (the bandage's is a
    4-vertex plate)."""
    import numpy as np
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(usd_file)
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    cache = UsdGeom.XformCache()
    all_pts, all_tris = [], []
    offset = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():
            continue
        mesh = UsdGeom.Mesh(prim)
        raw = mesh.GetPointsAttr().Get()
        if not raw:
            continue
        pts = np.array(raw, dtype=float)
        m = np.array(cache.GetLocalToWorldTransform(prim), dtype=float)
        world = (pts @ m[:3, :3] + m[3, :3]) * mpu
        counts = list(mesh.GetFaceVertexCountsAttr().Get())
        idx = list(mesh.GetFaceVertexIndicesAttr().Get())
        tris = []
        k = 0
        for c in counts:  # fan-triangulate
            for j in range(1, c - 1):
                tris += [idx[k] + offset, idx[k + j] + offset,
                         idx[k + j + 1] + offset]
            k += c
        all_pts.append(world)
        all_tris += tris
        offset += len(world)
    if not all_pts:
        return None, None
    points = np.concatenate(all_pts)
    # scan exports duplicate vertices per strip — unwelded, the cloth
    # solver sees zero-length edges and degenerate triangles and NaNs
    # (same pathology as the 8193-false-island wheelchair tire)
    uniq, inverse = np.unique(points.round(6), axis=0, return_inverse=True)
    tris = []
    seen = set()
    for i in range(0, len(all_tris), 3):
        a, b, c = (int(inverse[all_tris[i]]), int(inverse[all_tris[i + 1]]),
                   int(inverse[all_tris[i + 2]]))
        key = frozenset((a, b, c))
        # degenerate faces and double-sided duplicates (scan meshes author
        # both windings) corrupt the bending-edge graph and blow up VBD
        if len(key) == 3 and key not in seen:
            seen.add(key)
            tris += [a, b, c]
    if not tris:
        return None, None
    # keep only the LARGEST connected component: welded scan accessories
    # (ear loops, straps) create non-manifold edges that destabilize the
    # solver, and the drape question is about the main cloth body
    parent = list(range(len(uniq)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(0, len(tris), 3):
        a, b, c = find(tris[i]), find(tris[i + 1]), find(tris[i + 2])
        parent[b] = a
        parent[c] = a
    from collections import Counter
    comp = Counter(find(v) for v in range(len(uniq)))
    main = comp.most_common(1)[0][0]
    keep = [i for i in range(0, len(tris), 3) if find(tris[i]) == main]
    used = sorted({tris[i + j] for i in keep for j in range(3)})
    remap = {old_i: new_i for new_i, old_i in enumerate(used)}
    new_tris = [remap[tris[i + j]] for i in keep for j in range(3)]
    return uniq[used], new_tris


def drape(asset_id: str) -> str:
    import newton
    import warp as wp

    _device()

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    if not entry.get("deformable"):
        return f"{asset_id}: not a deformable entry"
    points, tris = _world_mesh(entry["file"])
    if points is None:
        return f"{asset_id}: no mesh found"
    if len(points) > 50000:
        return f"{asset_id}: {len(points)} vertices — too dense for drape test"

    points = points - points.mean(axis=0)
    extents = points.max(axis=0) - points.min(axis=0)
    planar_m = float(max(extents[0], extents[1]))
    # normalize to unit planar size: drape ("does this topology collapse
    # like cloth?") is scale-invariant, and cm-scale triangles with the
    # solver's meter-scale stiffness defaults explode numerically
    points = points / max(planar_m, 1e-6)
    z_extent0 = float(points[:, 2].max() - points[:, 2].min())
    extents = points.max(axis=0) - points.min(axis=0)
    size = float(max(extents))
    planar = float(max(extents[0], extents[1]))  # == 1.0
    drop_h = 0.75 * size

    builder = newton.ModelBuilder()
    # default particle radius (0.1) would float the cloth 10 cm above the
    # ground plane in normalized units
    builder.default_particle_radius = 0.01
    builder.add_cloth_mesh(
        pos=wp.vec3(0.0, 0.0, drop_h),
        rot=wp.quat_identity(),
        scale=1.0,
        vertices=[wp.vec3(*p) for p in points],
        indices=tris,
        vel=wp.vec3(0.0, 0.0, 0.0),
        density=0.2,
        tri_ke=5.0e1, tri_ka=5.0e1, tri_kd=1.0e-1,
        edge_ke=1.0e1, edge_kd=1.0e0,
    )
    builder.color(include_bending=True)
    builder.add_ground_plane()
    model = builder.finalize()
    model.soft_contact_ke = 1.0e2
    model.soft_contact_kd = 1.0e0
    model.soft_contact_mu = 0.5
    # self-contact defaults assume meter-scale cloth; on a centimeter-scale
    # object the 0.2 m radius swallows the whole mesh and the solve NaNs
    # Explicit, reusable contact storage is the Newton 1.5 ownership model.
    pipeline, contacts = collision_pipeline(model)
    solver = newton.solvers.SolverVBD(
        model, iterations=10, particle_enable_self_contact=False)
    state0, state1 = model.state(), model.state()
    control = model.control()
    substeps = 20
    dt = FRAME_DT / substeps
    for _ in range(int(3.0 / FRAME_DT)):
        for _ in range(substeps):
            state0.clear_forces()
            pipeline.collide(state0, contacts)
            solver.step(state0, state1, control, contacts, dt)
            state0, state1 = state1, state0
    state = state0
    import math

    pq = state.particle_q.numpy()
    if not math.isfinite(float(pq.sum())):
        return f"ERROR {asset_id}: solve diverged (NaN particles)"
    z_extent = float(pq[:, 2].max() - pq[:, 2].min())
    if z_extent > 100.0:
        # particles blasted away: degenerate scan topology the solver
        # cannot integrate — an asset callout, not a measurement
        entry["drape_test_newton_1_5"] = {
            "date": date.today().isoformat(),
            "method": "headless_newton_drape_test",
            "verdict": "unstable",
            "note": "cloth solve diverged — degenerate/non-manifold scan "
                    "topology; needs mesh repair before soft-body use",
        }
        qf.write_text(json.dumps(entry, indent=1))
        return (f"FAIL {asset_id}: cloth solve diverged — degenerate scan "
                f"topology (needs mesh repair before soft-body use)")
    z_min = float(pq[:, 2].min())
    on_ground = z_min < 0.05  # normalized units + contact radius margin
    # real cloth ends FLAT: final height a small fraction of its planar
    # size, resting on the ground. (Initial z-extent is useless for
    # already-flat objects like a mask.)
    flatness = z_extent / planar if planar > 1e-6 else 1.0
    draped = on_ground and flatness < 0.35
    evidence = {
        "date": date.today().isoformat(),
        "method": "headless_newton_drape_test",
        "engine": runtime_label(
            f"SolverVBD, headless on {wp.get_device().name}"),
        **runtime_metadata(),
        "particles": int(len(pq)),
        "normalized_to_planar_m": round(planar_m, 4),
        "initial_z_extent_norm": round(z_extent0, 4),
        "final_z_extent_norm": round(z_extent, 4),
        "final_flatness": round(flatness, 3),
        "rests_on_ground": on_ground,
        "drapes_like_cloth": draped,
    }
    entry["drape_test_newton_1_5"] = evidence
    qf.write_text(json.dumps(entry, indent=1))
    return (f"{'PASS' if draped else 'FAIL'} {asset_id}: final flatness "
            f"{flatness:.3f} (z {z_extent0:.3f} -> {z_extent:.3f} m over "
            f"{planar:.3f} m), on_ground={on_ground}, {len(pq)} particles")




def fold(asset_id: str) -> str:
    """Fold-persistence test: the mesh is folded in half GEOMETRICALLY at
    spawn (one half reflected over the midline, one layer's thickness
    apart), dropped a few cm, and settled. Real cloth STAYS folded — a
    springy shell or rigid sheet springs open or balloons. This is the
    material property a laundry-folding robot depends on, measured with
    zero actuation (moving pinned particles is not a supported VBD
    pattern in this Newton build; actuated grasping is a separate
    workstream on the robot side)."""
    import math

    import newton
    import numpy as np
    import warp as wp

    _device()

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    if not entry.get("deformable"):
        return f"{asset_id}: not a deformable entry"
    points, tris = _world_mesh(entry["file"])
    if points is None:
        return f"{asset_id}: no mesh found"

    points = points - points.mean(axis=0)
    extents = points.max(axis=0) - points.min(axis=0)
    planar_m = float(max(extents[0], extents[1]))
    points = points / max(planar_m, 1e-6)
    ext = points.max(axis=0) - points.min(axis=0)
    if ext[0] > ext[1]:  # fold along the LONG axis -> put it on Y
        points = points[:, [1, 0, 2]].copy()
    y_min, y_max = points[:, 1].min(), points[:, 1].max()
    y_len0 = float(y_max - y_min)
    mid = 0.5 * (y_min + y_max)
    layer_gap = 0.03
    folded_half = points[:, 1] > mid
    points[folded_half, 1] = 2.0 * mid - points[folded_half, 1]
    points[folded_half, 2] += layer_gap

    builder = newton.ModelBuilder()
    builder.default_particle_radius = 0.01
    builder.add_cloth_mesh(
        pos=wp.vec3(0.0, 0.0, 0.05), rot=wp.quat_identity(), scale=1.0,
        vertices=[wp.vec3(*p) for p in points], indices=tris,
        vel=wp.vec3(0.0, 0.0, 0.0), density=0.2,
        # fabric-realistic bending: the cotton preset's bend stiffness is
        # ~0.02 — stiff bending pops the fold open (spring-steel, not
        # cloth) and detonates the creased line's stored energy
        tri_ke=5.0e1, tri_ka=5.0e1, tri_kd=1.0e-1,
        edge_ke=5.0e-2, edge_kd=1.0e-2,
    )
    builder.color(include_bending=True)
    builder.add_ground_plane()
    model = builder.finalize()
    model.soft_contact_ke = 1.0e2
    model.soft_contact_kd = 1.0e0
    model.soft_contact_mu = 0.7
    pipeline, contacts = collision_pipeline(model)
    solver = newton.solvers.SolverVBD(
        model, iterations=10, particle_enable_self_contact=False)

    state0, state1 = model.state(), model.state()
    control = model.control()
    substeps = 20
    dt = FRAME_DT / substeps
    for _ in range(int(3.0 / FRAME_DT)):
        for _ in range(substeps):
            state0.clear_forces()
            pipeline.collide(state0, contacts)
            solver.step(state0, state1, control, contacts, dt)
            state0, state1 = state1, state0
    pq = state0.particle_q.numpy()
    if not math.isfinite(float(pq.sum())):
        return f"ERROR {asset_id}: fold solve diverged"
    y_len = float(pq[:, 1].max() - pq[:, 1].min())
    z_ext = float(pq[:, 2].max() - pq[:, 2].min())
    ratio = y_len / y_len0 if y_len0 > 1e-6 else 1.0
    # stays folded: about half length (cloth may slump slightly wider),
    # flat two-layer stack, resting on ground
    on_ground = float(pq[:, 2].min()) < 0.05
    folded = 0.4 <= ratio <= 0.75 and z_ext < 0.12 and on_ground
    entry["fold_test_newton_1_5"] = {
        "date": date.today().isoformat(),
        "method": "headless_newton_fold_persistence_test",
        "engine": runtime_label(
            f"SolverVBD, headless on {wp.get_device().name}"),
        **runtime_metadata(),
        "fold_axis_len_ratio": round(ratio, 3),
        "final_z_extent_norm": round(z_ext, 4),
        "rests_on_ground": on_ground,
        "stays_folded": folded,
    }
    qf.write_text(json.dumps(entry, indent=1))
    return (f"{'PASS' if folded else 'FAIL'} {asset_id}: settled at "
            f"{ratio:.2f} of unfolded length (want ~0.5), z-extent "
            f"{z_ext:.3f} -> {'stays folded' if folded else 'did not stay folded'}")


def squish(asset_id: str) -> str:
    """Volumetric material test (foam/sponge/rubber/silicone/gel): a soft
    FEM block with the asset's dimensions and its PRESET's actual material
    parameters (Young's modulus / Poisson -> Lame) is dropped and settled.
    Foam stays compressed with a dead landing; rubber lands lively and
    keeps its height; gel damps out. Evidence: restitution proxy,
    compression ratio, settle stability."""
    import math

    import newton
    import numpy as np
    import warp as wp

    _device()

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    dtype = entry.get("deformable")
    if not dtype:
        return f"{asset_id}: not a deformable entry"
    presets = json.loads((REPO / "workspace" / "knowledge" /
                          "deformable_presets.json").read_text())["presets"]
    alias = {"sponge": "sponge_soft", "rubber": "rubber_soft",
             "gel": "gel_soft"}
    preset_key = alias.get(dtype, dtype)
    preset = presets.get(preset_key)
    if not preset or "Body" not in preset.get("api", ""):
        return (f"{asset_id}: '{dtype}' is not a volumetric preset "
                f"(use drape/fold for shells)")
    params = preset.get("params", {})
    E = float(params.get("youngs_modulus", 1e4))
    nu = min(0.48, float(params.get("poissons_ratio", 0.3)))
    density = float(preset.get("density_kg_m3", 500))
    k_mu = E / (2.0 * (1.0 + nu))
    k_lambda = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
    k_damp = float(params.get("damping", 0.05)) * 10.0

    dims = _measure_dims_local(entry.get("file")) or [0.1, 0.1, 0.1]
    dims = [max(0.02, min(d, 0.5)) for d in dims]
    cells = [max(2, min(6, round(d / 0.03))) for d in dims]
    drop_h = 0.15

    builder = newton.ModelBuilder()
    builder.default_particle_radius = 0.005  # cm-scale grids float/explode at the 0.1 default
    builder.add_soft_grid(
        pos=wp.vec3(-dims[0] / 2, -dims[1] / 2, drop_h),
        rot=wp.quat_identity(), vel=wp.vec3(0.0, 0.0, 0.0),
        dim_x=cells[0], dim_y=cells[1], dim_z=cells[2],
        cell_x=dims[0] / cells[0], cell_y=dims[1] / cells[1],
        cell_z=dims[2] / cells[2],
        density=density, k_mu=k_mu, k_lambda=k_lambda, k_damp=k_damp,
        # surface skin triangles must be soft — default membrane
        # stiffness on tiny cell masses explodes (per the diffsim example)
        tri_ke=1e-4, tri_ka=1e-4, tri_kd=1e-4, tri_drag=0.0, tri_lift=0.0,
    )
    builder.add_ground_plane()
    model = builder.finalize()
    model.soft_contact_ke = 1.0e3
    model.soft_contact_kd = 1.0e1
    model.soft_contact_mu = 0.6
    # XPBD: explicit SemiImplicit is knife-edged on tiny cell masses
    # (undamped it bounces forever, damped it overshoots the stability
    # limit); XPBD's constraint projection handles tets robustly
    solver = newton.solvers.SolverXPBD(model, iterations=10)
    pipeline, contacts = collision_pipeline(
        model, soft_contact_margin=0.001)

    state0, state1 = model.state(), model.state()
    control = model.control()
    substeps = 20
    dt = FRAME_DT / substeps
    h0 = dims[2]
    com_z, heights = [], []
    for _ in range(int(2.5 / FRAME_DT)):
        for _ in range(substeps):
            state0.clear_forces()
            pipeline.collide(state0, contacts)
            solver.step(state0, state1, control, contacts, dt)
            state0, state1 = state1, state0
        pq = state0.particle_q.numpy()
        com_z.append(float(pq[:, 2].mean()))
        heights.append(float(pq[:, 2].max() - max(0.0, pq[:, 2].min())))
    pq = state0.particle_q.numpy()
    if not math.isfinite(float(pq.sum())):
        return f"ERROR {asset_id}: squish solve diverged"
    # restitution proxy: highest COM rebound after the first minimum
    first_min = int(np.argmin(com_z[: len(com_z) // 2]))
    rebound = max(com_z[first_min:]) - com_z[first_min]
    restitution = max(0.0, min(1.0, rebound / max(drop_h, 1e-6)))
    height_ratio = heights[-1] / h0 if h0 > 1e-6 else 1.0
    last = com_z[-30:]
    settled = max(last) - min(last) < 0.005
    on_ground = float(pq[:, 2].min()) < 0.02
    plausible = (settled and on_ground
                 and 0.25 <= height_ratio <= 1.15
                 and restitution <= 0.9)
    entry["squish_test_newton_1_5"] = {
        "date": date.today().isoformat(),
        "method": "headless_newton_squish_test",
        "engine": runtime_label(
            f"SolverXPBD FEM, headless on {wp.get_device().name}"),
        **runtime_metadata(),
        "preset": preset_key,
        "youngs_modulus": E, "poissons_ratio": nu,
        "restitution_proxy": round(restitution, 3),
        "height_ratio": round(height_ratio, 3),
        "settled": settled,
        "viscous_damping_hz": CLOTH_DAMP_HZ,
        "behaves_like_soft_body": plausible,
    }
    qf.write_text(json.dumps(entry, indent=1))
    return (f"{'PASS' if plausible else 'FAIL'} {asset_id} [{preset_key}]: "
            f"restitution {restitution:.2f}, height ratio {height_ratio:.2f}, "
            f"settled={settled}")


def _measure_dims_local(file_path):
    """Bbox dims in meters via pxr (None when unavailable)."""
    if not file_path or not Path(file_path).exists():
        return None
    try:
        from pxr import Usd, UsdGeom
        st = Usd.Stage.Open(file_path)
        rng = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        ).ComputeWorldBound(st.GetPseudoRoot()).ComputeAlignedRange()
        if rng.IsEmpty():
            return None
        mpu = UsdGeom.GetStageMetersPerUnit(st)
        s = rng.GetSize()
        return [s[0] * mpu, s[1] * mpu, s[2] * mpu]
    except Exception:
        return None


def cable(target: str) -> str:
    """Dynamic-cord verification with Newton rods + VBD.

    Vertex Block Descent (Chen/Liu/Yang/Yuksel, SIGGRAPH 2024) is what
    SolverVBD implements; its robustness at extreme mass ratios is why a
    cord can be simulated at its PHYSICAL linear-density mass here, where
    PhysX needed link mass floored at 15 g and still coiled when slack.

    Two criteria, both measured:
      hang  — pinned at one end with a 100 g tool on the other, the cord
              must pull straight (end-to-end / arc > 0.95)
      slack — the cord's full length across a 55% span with both ends
              pinned must HOLD ITS BOW, not collapse into a hairpin
              (PhysX collapsed 1.0 m to a 0.19 m span)

    `target` is a corded assembly / cable USD (its customLayerData cord
    block gives length and radius) or a queue asset_id.
    """
    import math

    import newton
    import numpy as np
    import warp as wp
    from pxr import Usd

    _device()

    path = target
    entry, qf = None, QUEUE_DIR / f"{target}.json"
    if qf.exists():
        entry = json.loads(qf.read_text())
        path = entry.get("file", target)
    if not Path(path).exists():
        return f"{target}: file not found ({path})"
    # hold the STAGE: Usd.Stage.Open(p).GetRootLayer() lets the stage be
    # collected immediately and the layer handle expires (Boost.Python
    # ArgumentError on the next access)
    _stage = Usd.Stage.Open(path)
    _lyr = _stage.GetRootLayer()
    data = dict(_lyr.customLayerData) if _lyr.customLayerData else {}
    # a bare cord stores its params under "cable", an assembly under "cord"
    meta = dict(data.get("cord") or data.get("cable") or {})
    L = float(meta.get("length_m", 1.0))
    R = float(meta.get("radius_m", 0.004))
    N = max(8, int(meta.get("links", 20)) + 1)
    seg = L / (N - 1)
    seg_mass = RUBBER_DENSITY * math.pi * R ** 2 * seg

    def _q_to(d):
        d = np.asarray(d, float)
        d = d / np.linalg.norm(d)
        z = np.array([0.0, 0.0, 1.0])
        c = float(np.dot(z, d))
        if c > 1 - 1e-9:
            return wp.quat_identity()
        if c < -1 + 1e-9:
            return wp.quat_from_axis_angle(wp.vec3(1.0, 0.0, 0.0), math.pi)
        ax = np.cross(z, d)
        ax = ax / np.linalg.norm(ax)
        return wp.quat_from_axis_angle(wp.vec3(*ax), math.acos(c))

    def _run(hang: bool, tool_kg: float = 0.0):
        b = newton.ModelBuilder()
        newton.solvers.SolverVBD.register_custom_attributes(
            b, dahl_defaults_enabled=False)
        if hang:
            pos = [wp.vec3(0.0, 0.0, 1.2 - i * seg) for i in range(N)]
        else:
            chord = 0.55 * L
            lo, hi = chord / 2 + 1e-6, 5.0
            for _ in range(60):        # radius of a circular arc of length L
                rad = 0.5 * (lo + hi)
                a = 2 * rad * math.asin(min(1.0, chord / (2 * rad)))
                lo, hi = (rad, hi) if a > L else (lo, rad)
            half = math.asin(min(1.0, chord / (2 * rad)))
            pos = []
            for i in range(N):
                t = -half + 2 * half * i / (N - 1)
                pos.append(wp.vec3(rad * math.sin(t) + chord / 2,
                                   rad * (math.cos(t) - math.cos(half)),
                                   R + 0.002))
        quats = [_q_to(np.array(pos[i + 1]) - np.array(pos[i]))
                 for i in range(N - 1)]
        b.add_rod(positions=pos, quaternions=quats, radius=R,
                  stretch_stiffness=1.0e5, stretch_damping=1.0e-2,
                  bend_stiffness=5.0e-3, bend_damping=1.0e-4,
                  body_frame_origin="start")
        nb = b.body_count
        for i in range(nb):
            b.body_mass[i] = seg_mass          # PHYSICAL mass, no floor
            b.body_inv_mass[i] = 1.0 / seg_mass
        if tool_kg:
            b.body_mass[nb - 1] = tool_kg
            b.body_inv_mass[nb - 1] = 1.0 / tool_kg
        b.body_mass[0] = 0.0                   # pin by zero inverse mass
        b.body_inv_mass[0] = 0.0
        if not hang:
            b.body_mass[nb - 1] = 0.0
            b.body_inv_mass[nb - 1] = 0.0
            b.add_ground_plane()
        b.color()                              # VBD graph colouring
        model = b.finalize()
        pipeline, contacts = collision_pipeline(model)
        solver = newton.solvers.SolverVBD(model, iterations=10)
        s0, s1 = model.state(), model.state()
        ctrl = model.control()
        dt = FRAME_DT / 8
        for _ in range(int(4.0 / FRAME_DT)):
            for _ in range(8):
                s0.clear_forces()
                pipeline.collide(s0, contacts)
                solver.step(s0, s1, ctrl, contacts, dt)
                s0, s1 = s1, s0
        q = s0.body_q.numpy()[:, :3]
        if not np.isfinite(q).all():
            return None
        arc = sum(float(np.linalg.norm(q[i + 1] - q[i]))
                  for i in range(len(q) - 1))
        return {"arc_m": round(arc, 4),
                "span_m": round(float(np.linalg.norm(q[-1] - q[0])), 4),
                "z_min": round(float(q[:, 2].min()), 4),
                "z_max": round(float(q[:, 2].max()), 4)}

    hang = _run(True, tool_kg=0.1)
    slack = _run(False)
    if hang is None or slack is None:
        return f"FAIL {target}: solve diverged"
    straight = hang["span_m"] / max(hang["arc_m"], 1e-6)
    intended_span = 0.55 * L
    held = slack["span_m"] / intended_span
    ok = straight > 0.95 and 0.9 <= held <= 1.1 and slack["z_max"] < 0.2 * L
    evidence = {
        "date": date.today().isoformat(),
        "method": "headless_newton_vbd_cable_test",
        "engine": runtime_label("SolverVBD — Vertex Block Descent"),
        **runtime_metadata(),
        "segment_mass_kg": round(seg_mass, 6),
        "physx_floor_kg": 0.015,
        "hang": hang, "slack": slack,
        "straightness_under_load": round(straight, 3),
        "slack_span_ratio": round(held, 3),
        "behaves_like_cable": ok,
    }
    if entry is not None:
        entry["cable_test_newton_1_5"] = evidence
        qf.write_text(json.dumps(entry, indent=1))
    return (f"{'PASS' if ok else 'FAIL'} {target}: hang straightness "
            f"{straight:.3f}, slack span {slack['span_m']:.3f} m of "
            f"{intended_span:.3f} intended (ratio {held:.2f}), "
            f"segment mass {seg_mass*1000:.2f} g (PhysX floor 15 g)")


def grasp(target: str) -> str:
    """Actuated cable test: a gripper GRASPS the cord and moves it.

    The last open case for both cable and cloth. A kinematic gripper body
    (zero inverse mass, pose driven each substep) holds one end of the
    cord while the other end stays anchored — the plugged-in end. Passing
    means the cord follows the hand without stretching or exploding, and
    the anchored end holds.

    Measured:
      follows      — the grasped end tracks the commanded path (error
                     small relative to the move)
      inextensible — arc length stays within 10% of rest (a cable that
                     stretches to reach is not a cable)
      anchored     — the pinned end does not move
      stable       — no divergence over the whole motion
    """
    import math

    import newton
    import numpy as np
    import warp as wp
    from pxr import Usd

    _device()

    path = target
    entry, qf = None, QUEUE_DIR / f"{target}.json"
    if qf.exists():
        entry = json.loads(qf.read_text())
        path = entry.get("file", target)
    if not Path(path).exists():
        return f"{target}: file not found ({path})"
    _stage = Usd.Stage.Open(path)
    data = dict(_stage.GetRootLayer().customLayerData or {})
    meta = dict(data.get("cord") or data.get("cable") or {})
    L = float(meta.get("length_m", 1.0))
    R = float(meta.get("radius_m", 0.004))
    N = max(8, int(meta.get("links", 20)) + 1)
    seg = L / (N - 1)
    seg_mass = RUBBER_DENSITY * math.pi * R ** 2 * seg

    b = newton.ModelBuilder()
    newton.solvers.SolverVBD.register_custom_attributes(
        b, dahl_defaults_enabled=False)
    pos = [wp.vec3(i * seg, 0.0, R + 0.002) for i in range(N)]   # laid out flat
    quats = [wp.quat_from_axis_angle(wp.vec3(0.0, 1.0, 0.0), math.pi / 2)
             for _ in range(N - 1)]                              # +Z -> +X
    b.add_rod(positions=pos, quaternions=quats, radius=R,
              stretch_stiffness=1.0e5, stretch_damping=1.0e-2,
              bend_stiffness=5.0e-3, bend_damping=1.0e-4,
              body_frame_origin="start")
    nb = b.body_count
    for i in range(nb):
        b.body_mass[i] = seg_mass
        b.body_inv_mass[i] = 1.0 / seg_mass
    b.body_mass[0] = 0.0            # anchored end (the plug)
    b.body_inv_mass[0] = 0.0
    b.body_mass[nb - 1] = 0.0       # the GRIPPER: kinematic, pose-driven
    b.body_inv_mass[nb - 1] = 0.0
    b.add_ground_plane()
    b.color()
    model = b.finalize()
    pipeline, contacts = collision_pipeline(model)
    solver = newton.solvers.SolverVBD(model, iterations=10)

    s0, s1 = model.state(), model.state()
    ctrl = model.control()
    start = np.array(s0.body_q.numpy()[nb - 1][:3], dtype=float)
    # lift 0.25 m and swing 0.20 m back over the cord — a real pick-up
    goal = start + np.array([-0.20, 0.10, 0.25])
    lift_s, hold_s = 2.0, 1.0
    dt = FRAME_DT / 8
    frames = int((lift_s + hold_s) / FRAME_DT)
    for f in range(frames):
        t = min(1.0, f / (lift_s / FRAME_DT))
        smooth = t * t * (3 - 2 * t)                 # ease in/out
        want = start + (goal - start) * smooth
        for _ in range(8):
            s0.clear_forces()
            # write THROUGH: wp.array.numpy() is a view on CPU but a COPY
            # on CUDA — mutating it silently does nothing on the GPU
            q = s0.body_q.numpy().copy()
            q[nb - 1][:3] = want                     # drive the gripper
            s0.body_q.assign(q)
            pipeline.collide(s0, contacts)
            solver.step(s0, s1, ctrl, contacts, dt)
            s0, s1 = s1, s0
    q = s0.body_q.numpy()[:, :3]
    if not np.isfinite(q).all():
        return f"FAIL {target}: solve diverged during the grasp"
    arc = sum(float(np.linalg.norm(q[i + 1] - q[i])) for i in range(len(q) - 1))
    rest_arc = seg * (nb - 1)
    stretch = arc / rest_arc
    follow_err = float(np.linalg.norm(q[nb - 1] - goal))
    anchor_drift = float(np.linalg.norm(q[0] - np.array(pos[0], dtype=float)))
    move_len = float(np.linalg.norm(goal - start))
    ok = (follow_err < 0.02 and abs(stretch - 1.0) < 0.10
          and anchor_drift < 0.01)
    evidence = {
        "date": date.today().isoformat(),
        "method": "headless_newton_vbd_grasp_test",
        "engine": runtime_label("SolverVBD — Vertex Block Descent"),
        **runtime_metadata(),
        "segment_mass_kg": round(seg_mass, 6),
        "commanded_move_m": round(move_len, 4),
        "gripper_follow_error_m": round(follow_err, 5),
        "arc_stretch_ratio": round(stretch, 3),
        "anchor_drift_m": round(anchor_drift, 5),
        "cable_survives_manipulation": ok,
    }
    if entry is not None:
        entry["grasp_test_newton_1_5"] = evidence
        qf.write_text(json.dumps(entry, indent=1))
    return (f"{'PASS' if ok else 'FAIL'} {target}: gripper moved "
            f"{move_len:.3f} m (follow error {follow_err*1000:.1f} mm), "
            f"arc stretch {stretch:.3f}, anchor drift "
            f"{anchor_drift*1000:.2f} mm")


def _export_cloth_usd(out_path: str, pts, tris, finger_xforms, half=(0.03, 0.03, 0.008)):
    """Write a solved cloth state to USD so it can be rendered by the same
    usdrecord path that produces every other asset view. The grasp runs
    headless in Newton; without this its result is only ever a number.
    """
    from pxr import Gf, Usd, UsdGeom

    stage = Usd.Stage.CreateNew(out_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Grasp")
    stage.SetDefaultPrim(root.GetPrim())

    mesh = UsdGeom.Mesh.Define(stage, "/Grasp/Cloth")
    mesh.CreatePointsAttr([Gf.Vec3f(*map(float, p)) for p in pts])
    mesh.CreateFaceVertexIndicesAttr([int(i) for i in tris])
    mesh.CreateFaceVertexCountsAttr([3] * (len(tris) // 3))
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreateDisplayColorAttr([Gf.Vec3f(0.72, 0.74, 0.80)])

    for i, xf in enumerate(finger_xforms):
        cube = UsdGeom.Cube.Define(stage, f"/Grasp/Finger_{i}")
        cube.CreateSizeAttr(2.0)          # unit cube scaled to the half-extents
        cube.CreateDisplayColorAttr([Gf.Vec3f(0.25, 0.28, 0.34)])
        x = UsdGeom.Xformable(cube)
        x.AddTranslateOp().Set(Gf.Vec3d(*map(float, xf)))
        x.AddScaleOp().Set(Gf.Vec3f(*half))
    stage.GetRootLayer().Save()
    return out_path


def cloth_grasp(target: str) -> str:
    """THE LAUNDRY GRASP: two fingers pinch a garment corner and lift it.

    Newton attaches cloth to a gripper the way a real gripper does — by
    CONTACT with friction (its own example_cloth_franka sets
    robot_friction=1.0 and no attachment); equality constraints are
    body-to-body only, and driving pinned cloth PARTICLES explodes. So
    this drives two kinematic finger bodies that close on a corner and
    lift, and asks whether the cloth comes with them.

    Measured:
      held    — the pinched corner tracks the fingers (mm, not cm)
      lifted  — the garment's centroid rises with the hand
      hangs   — the free part stays BELOW the grip (it drapes, not floats)
      stable  — no divergence through close + lift
    """
    import math

    import newton
    import numpy as np
    import warp as wp

    _device()

    @wp.kernel
    def _damp(qd: wp.array(dtype=wp.vec3), s: float):
        qd[wp.tid()] = qd[wp.tid()] * s

    path = target
    entry, qf = None, QUEUE_DIR / f"{target}.json"
    if qf.exists():
        entry = json.loads(qf.read_text())
        path = entry.get("file", target)
    if not Path(path).exists():
        return f"{target}: file not found ({path})"
    points, tris = _world_mesh(path)
    if points is None or not tris:
        return f"{target}: no usable mesh"

    points = points - points.mean(axis=0)
    extents = points.max(axis=0) - points.min(axis=0)
    planar = float(max(extents[0], extents[1])) or 1.0
    points = points / planar                      # unit-planar, as drape/fold
    start_z = 0.6
    points[:, 2] += start_z

    corner = int(np.argmin(points[:, 0] + points[:, 1]))   # a corner vertex
    grip = np.array(points[corner], dtype=float)

    full_surface_contact = (
        os.environ.get("NEWTON_FULL_SURFACE_CONTACT", "0") == "1")
    b = newton.ModelBuilder()
    b.default_particle_radius = 0.01
    b.add_cloth_mesh(pos=wp.vec3(0.0, 0.0, 0.0), rot=wp.quat_identity(),
                     scale=1.0, vertices=[wp.vec3(*p) for p in points],
                     indices=tris, vel=wp.vec3(0.0, 0.0, 0.0), density=0.2,
                     tri_ke=5.0e1, tri_ka=5.0e1, tri_kd=CLOTH_TRI_KD,
                     edge_ke=5.0e-2, edge_kd=1.0e-2,
                     # aerodynamic drag, not stiffness damping: raising
                     # tri_kd to 2.0 made the damping force itself
                     # explosive (the corner flew 100 m). Drag bleeds the
                     # pendulum swing off without touching the solve.
                     tri_drag=CLOTH_TRI_DRAG, tri_lift=0.0)
    # two fingers straddling the corner: kinematic (zero inverse mass),
    # pose-driven — the pattern that works for rigid bodies under VBD
    fingers = []
    for dz in (+0.035, -0.035):
        body = b.add_body(xform=wp.transform(
            wp.vec3(*(grip + np.array([0.0, 0.0, dz]))), wp.quat_identity()),
            mass=0.0)
        b.add_shape_box(body=body, hx=0.03, hy=0.03, hz=0.008)
        fingers.append(body)
    b.color(include_bending=True)   # AFTER the bodies: VBD colours both
    model = b.finalize()
    model.soft_contact_ke = 1.0e3
    model.soft_contact_kd = 1.0e1
    model.soft_contact_mu = 1.0          # robot_friction, per the example
    # The particle-contact baseline follows example_cloth_franka and tells
    # VBD that rigid bodies are advanced outside it. Newton 1.5's full-surface
    # gripper examples instead let VBD own the kinematic fingers; the solver
    # switch below follows the selected contact mode. edge_rest_angle is
    # zeroed per the example's VBD-bending workaround.
    model.edge_rest_angle.zero_()
    pipeline, contacts = collision_pipeline(
        model,
        enable_rigid_soft_full_surface_contact=full_surface_contact,
    )
    solver = newton.solvers.SolverVBD(
        model, iterations=10,
        # Newton's 1.5 full-surface examples let VBD own the kinematic
        # fingers; the legacy particle path needs the historical external
        # rigid coupling switch. Mixing both modes ejects the cloth.
        integrate_with_external_rigid_solver=not full_surface_contact,
        particle_enable_self_contact=False,
        rigid_body_contact_buffer_size=512,
        rigid_body_particle_contact_buffer_size=512)

    s0, s1 = model.state(), model.state()
    ctrl = model.control()
    dt = FRAME_DT / CLOTH_SUBSTEPS
    settle_s, lift_s, hold_s = 0.6, 2.0, 3.0
    lift_to = np.array([0.0, 0.0, 0.35])          # raise the hand 35 cm
    nsettle, nlift = int(settle_s / FRAME_DT), int(lift_s / FRAME_DT)
    nhold = int(hold_s / FRAME_DT)
    pinch = 0.013                                  # finger half-gap, closed
    mean_z_trace = []
    # The grasp is CLOSED from t=0. Closing over 0.6 s let the cloth
    # free-fall 33 m before the fingers met — nothing was holding it.
    for f in range(nsettle + nlift + nhold):
        gap = pinch
        if f < nsettle:
            hand = grip.copy()                     # let the cloth hang first
        elif f < nsettle + nlift:
            t = (f - nsettle) / max(nlift - 1, 1)
            hand = grip + lift_to * (t * t * (3 - 2 * t))
        else:
            hand = grip + lift_to                  # hold: let the swing die
        for _ in range(CLOTH_SUBSTEPS):
            s0.clear_forces()
            q = s0.body_q.numpy().copy()   # copy on CUDA — must assign back
            q[fingers[0]][:3] = hand + np.array([0.0, 0.0, +gap])
            q[fingers[1]][:3] = hand + np.array([0.0, 0.0, -gap])
            s0.body_q.assign(q)
            pipeline.collide(s0, contacts)
            solver.step(s0, s1, ctrl, contacts, dt)
            s0, s1 = s1, s0
        wp.launch(_damp, dim=len(s0.particle_qd),
                  inputs=[s0.particle_qd, math.exp(-CLOTH_DAMP_HZ * FRAME_DT)])
        mean_z_trace.append(float(s0.particle_q.numpy()[:, 2].mean()))
        if os.environ.get("GRASP_DEBUG") and f % 20 == 0:
            d = s0.particle_q.numpy()
            print(f"  f{f:4d} hand_z={hand[2]:.3f} corner_z={d[corner][2]:.3f} "
                  f"min_z={d[:, 2].min():.3f} max_z={d[:, 2].max():.3f} "
                  f"mean_z={d[:, 2].mean():.3f}", flush=True)

    pq = s0.particle_q.numpy()
    if not np.isfinite(pq.sum()):
        return f"FAIL {target}: cloth solve diverged during the grasp"
    hand_final = grip + lift_to
    held_err = float(np.linalg.norm(pq[corner] - hand_final))
    # A garment held by ONE corner and lifted from flat must lose centroid
    # height — it stops being a flat sheet and becomes a hanging one. The
    # centroid drops by roughly half its own reach no matter how well it is
    # held, so "centroid rose" is not a grasp test; it fails a perfect
    # grasp. What distinguishes held from dropped is whether the garment is
    # still WITHIN ITS OWN REACH of the fingers.
    reach = float(np.linalg.norm(points - points[corner], axis=1).max())
    span = float(np.linalg.norm(pq - pq[corner], axis=1).max())
    suspended = span < reach * 1.25          # nothing tore loose or fell away
    drape = float(pq[corner][2] - pq[:, 2].min())
    # A garment held at one corner hangs BELOW it. A mid-swing snapshot can
    # show a large drape while cloth is flung above the hand, so require
    # both: it reaches down, and nothing is left up above the fingers.
    above = float(pq[:, 2].max() - pq[corner][2])
    hangs = drape > 0.05 and above < 0.05
    lift_ok = float(pq[corner][2] - grip[2]) > 0.9 * float(lift_to[2])
    tail = mean_z_trace[-30:]                # last half second
    swing = float(max(tail) - min(tail)) if tail else 9.9
    settled = swing < 0.05                   # hanging, not still swinging
    centroid_rise = float(pq[:, 2].mean() - start_z)
    ok = held_err < 0.05 and suspended and hangs and lift_ok and settled
    evidence = {
        "date": date.today().isoformat(),
        "method": "headless_newton_vbd_cloth_grasp",
        "engine": runtime_label("SolverVBD — friction grasp, mu=1.0"),
        **runtime_metadata(),
        "device": _device(),
        "full_surface_contact": full_surface_contact,
        "commanded_lift_m": round(float(lift_to[2]), 3),
        "grasp_slip_m": round(held_err, 4),
        "corner_rose_m": round(float(pq[corner][2] - grip[2]), 4),
        "drape_depth_m": round(drape, 4),
        "above_grip_m": round(above, 4),
        "swing_last_half_s_m": round(swing, 4),
        "settled": settled,
        "viscous_damping_hz": CLOTH_DAMP_HZ,
        "span_m": round(span, 4),
        "rest_reach_m": round(reach, 4),
        "suspended": suspended,
        "centroid_rise_m": round(centroid_rise, 4),
        "particles": int(len(pq)),
        "garment_is_graspable": ok,
    }
    if entry is not None:
        suffix = "full_surface" if full_surface_contact else "particle"
        entry[f"cloth_grasp_test_newton_1_5_{suffix}"] = evidence
        qf.write_text(json.dumps(entry, indent=1))
    dump = os.environ.get("CLOTH_EXPORT_USD")
    if dump:
        fq = s0.body_q.numpy()
        _export_cloth_usd(dump, pq, tris,
                          [fq[fingers[0]][:3], fq[fingers[1]][:3]])
        print(f"  wrote {dump}", flush=True)
    return (f"{'PASS' if ok else 'FAIL'} {target}: lifted "
            f"{lift_to[2]:.2f} m, slip {held_err*1000:.0f} mm, corner rose "
            f"{(pq[corner][2]-grip[2])*100:.0f} cm, drapes {drape*100:.0f} cm, "
            f"span {span:.2f}/{reach:.2f} m, swing {swing*100:.1f} cm, "
            f"suspended={suspended} settled={settled}")


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in ("rigid", "drape", "fold",
                                                 "squish", "cable",
                                                 "grasp", "cloth_grasp"):
        print(__doc__)
        return 1
    require_newton_15()
    if len(sys.argv) > 3:
        # Newton's USD importer retains native state across models and can
        # segfault a long mixed-asset batch. One process per asset also makes
        # a native crash an attributable failure instead of losing the rest
        # of the gate. Warp's compiled-kernel cache is shared between them.
        import subprocess

        child_failures = 0
        for target in sys.argv[2:]:
            child_failures += bool(subprocess.run(
                [sys.executable, str(Path(__file__).resolve()),
                 sys.argv[1], target],
                check=False,
            ).returncode)
        return int(child_failures > 0)
    fn = {"rigid": rigid_drop, "drape": drape, "fold": fold,
          "squish": squish, "cable": cable,
          "grasp": grasp, "cloth_grasp": cloth_grasp}[sys.argv[1]]
    failures = 0
    for asset_id in sys.argv[2:]:
        try:
            line = fn(asset_id)
            print(line, flush=True)
            failures += 0 if line.startswith("PASS") else 1
        except Exception as e:
            failures += 1
            print(f"ERROR {asset_id}: {str(e)[:300]}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
