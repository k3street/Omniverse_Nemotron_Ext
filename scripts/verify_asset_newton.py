#!/usr/bin/env python3
"""Headless physics verification with NVIDIA Newton (Warp) — no Isaac, no Kit.

Two tests the PhysX live path can't cover:

  rigid <id...>       Cross-engine drop test on REGISTRY assets: the same
                      USD that passed the live PhysX drop is re-run in
                      Newton. Agreement between two engines is a far
                      stronger sim2real claim than either alone; evidence
                      lands in verification.newton on the registry entry.

  drape <id...>       Soft-body drape test for QUEUE deformables: the
                      asset's mesh is dropped as a Newton cloth onto the
                      ground. Real cloth collapses (final height-extent a
                      fraction of initial); a rigid shell would not.
                      Evidence lands on the queue entry (drape_test) for
                      the human reviewer — deformable sign-off stays human.

Run with the Newton venv:
    /home/kimate/newton/.venv/bin/python scripts/verify_asset_newton.py rigid beer_bottle
    /home/kimate/newton/.venv/bin/python scripts/verify_asset_newton.py drape disposable_medical_masks
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"
REGISTRY = REPO / "workspace" / "knowledge" / "sim_ready_assets.json"

FRAME_DT = 1.0 / 60.0
SUBSTEPS = 10


def _sim(model, solver, seconds: float, on_frame=None):
    import warp as wp  # noqa: F401

    state0, state1 = model.state(), model.state()
    control = model.control()
    dt = FRAME_DT / SUBSTEPS
    frames = int(seconds / FRAME_DT)
    for f in range(frames):
        for _ in range(SUBSTEPS):
            state0.clear_forces()
            contacts = model.collide(state0)
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
        "engine": f"Newton (warp) SolverXPBD, headless on "
                  f"{wp.get_device().name}",
        "drop": {"drop_measured_m": round(dropped, 4),
                 "drop_predicted_m": drop_h,
                 "settled_last_second": settled,
                 "final_vz": round(vz, 4),
                 "rests_after_s": 4.0},
    }
    verdict = "PASS" if rests else "FAIL"
    if verdict == "PASS":
        asset.setdefault("verification", {})
        asset["verification"]["newton"] = evidence
        REGISTRY.write_text(json.dumps(reg, indent=1))
    return (f"{verdict} {asset_id}: dropped {dropped:.4f} m "
            f"(predicted {drop_h}), settled={settled}, vz={vz:.4f}"
            + (" — cross-engine evidence written" if verdict == "PASS" else ""))


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

    # small particle counts; CPU is deterministic and immune to GPU
    # contention with the vLLM judge sharing the device
    wp.set_device("cpu")

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
    solver = newton.solvers.SolverVBD(model, 10,
                                      particle_enable_self_contact=False)

    # particle-vs-shape contacts need the unified pipeline (the default
    # model.collide path is rigid-only — the cloth would never see ground)
    pipeline = newton.CollisionPipelineUnified.from_model(model)
    state0, state1 = model.state(), model.state()
    control = model.control()
    substeps = 20
    dt = FRAME_DT / substeps
    for _ in range(int(3.0 / FRAME_DT)):
        for _ in range(substeps):
            state0.clear_forces()
            contacts = pipeline.collide(model, state0)
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
        entry["drape_test"] = {
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
        "engine": f"Newton (warp) SolverVBD, headless on "
                  f"{wp.get_device().name}",
        "particles": int(len(pq)),
        "normalized_to_planar_m": round(planar_m, 4),
        "initial_z_extent_norm": round(z_extent0, 4),
        "final_z_extent_norm": round(z_extent, 4),
        "final_flatness": round(flatness, 3),
        "rests_on_ground": on_ground,
        "drapes_like_cloth": draped,
    }
    entry["drape_test"] = evidence
    qf.write_text(json.dumps(entry, indent=1))
    return (f"{'PASS' if draped else 'FAIL'} {asset_id}: final flatness "
            f"{flatness:.3f} (z {z_extent0:.3f} -> {z_extent:.3f} m over "
            f"{planar:.3f} m), on_ground={on_ground}, {len(pq)} particles")


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in ("rigid", "drape"):
        print(__doc__)
        return 1
    fn = rigid_drop if sys.argv[1] == "rigid" else drape
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
