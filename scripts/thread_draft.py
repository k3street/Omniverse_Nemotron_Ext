#!/usr/bin/env python3
"""Thread tier of articulation drafting: bolts, screws and their nuts.

A fastener is a shank (with a head at one end) along its axis. The shank's
diameter, measured across slices along the axis, names the nearest ISO
metric size and so its coarse pitch. A ring around the shank thicker than
0.4 diameters is a nut: it gets a helix (add_mechanism.add_helix), so the
bolt turns and advances one pitch per turn, between the nut reaching the
shank's end and the nut meeting the head. A thinner ring is a washer, held
captive on the bolt. A loose bolt with nothing to thread into gets no
joint; its thread is recorded on the asset (customData simReady:thread) for
whatever it is later assembled with.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ISO 261 coarse pitches, nominal diameter -> pitch (mm)
ISO_COARSE_MM = {1.6: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5,
                 12: 1.75, 14: 2.0, 16: 2.0, 20: 2.5, 24: 3.0, 30: 3.5, 36: 4.0}
SLICES = 24
NUT_MIN_THICKNESS = 0.4   # of the diameter; thinner rings are washers


def iso_thread(diameter_m: float) -> tuple[float, float]:
    """Nearest ISO coarse size to a measured diameter: (nominal m, pitch m)."""
    d_mm = diameter_m * 1000.0
    nominal = min(ISO_COARSE_MM, key=lambda n: abs(n - d_mm))
    return nominal / 1000.0, ISO_COARSE_MM[nominal] / 1000.0


def _edge_points(stage, path, step: float) -> np.ndarray:
    """Mesh vertices plus points along every edge at most `step` apart: a
    plain cylinder has vertices only at its two ends, which slices miss."""
    from pxr import Gf, Usd, UsdGeom

    out = []
    for p in Usd.PrimRange(stage.GetPrimAtPath(path)):
        if not p.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(p)
        m = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0)
        v = np.array([list(m.Transform(Gf.Vec3d(*q))) for q in mesh.GetPointsAttr().Get()])
        counts, idx = mesh.GetFaceVertexCountsAttr().Get(), np.array(mesh.GetFaceVertexIndicesAttr().Get())
        a, b, k = [], [], 0
        for c in counts:
            f = idx[k:k + c]
            a.append(f)
            b.append(np.roll(f, -1))
            k += c
        a, b = np.concatenate(a), np.concatenate(b)
        out.append(v)
        length = np.linalg.norm(v[b] - v[a], axis=1)
        long = length > step
        if long.any():
            n = int(np.ceil(length[long].max() / step))
            t = np.linspace(0, 1, n + 1)[1:-1, None, None]
            out.append((v[a[long]][None] * (1 - t) + v[b[long]][None] * t).reshape(-1, 3))
    return np.concatenate(out)


def _profile(pts: np.ndarray, axis: int):
    """Width of the points across the axis, slice by slice along it."""
    other = [k for k in range(3) if k != axis]
    s = pts[:, axis]
    edges = np.linspace(s.min(), s.max(), SLICES + 1)
    rows = []
    for i in range(SLICES):
        m = (s >= edges[i]) & (s <= edges[i + 1])
        if m.sum() < 3:
            continue
        q = pts[m][:, other]
        rows.append((edges[i], edges[i + 1], float((q.max(0) - q.min(0)).max()), (q.max(0) + q.min(0)) / 2))
    return rows


def propose_thread(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    # zero-thickness parts are backdrops (a ground plane under the model), not metal
    parts = [p for p in collect_parts(stage, asset_root) if min(p["size"]) > 1e-5]
    if not parts:
        raise RuntimeError("no solid parts")
    main = max(parts, key=lambda p: p["volume"])
    axis = template.get("axis")
    axis = "XYZ".index(axis) if axis else int(np.argmax(main["size"]))
    other = [k for k in range(3) if k != axis]
    rows = _profile(_edge_points(stage, main["path"], main["size"][axis] / (2 * SLICES)), axis)
    widths = np.array([r[2] for r in rows])
    d = float(np.median(widths))
    shank_rows = [r for r in rows if r[2] <= 1.1 * d]
    centre = np.median(np.array([r[3] for r in shank_rows]), axis=0)
    shank = (min(r[0] for r in shank_rows), max(r[1] for r in shank_rows))
    nominal, pitch = iso_thread(d)
    if template.get("pitch_m"):
        pitch = float(template["pitch_m"])
    # the head is the wide end: which side of the shank the wide slices are
    wide = [r for r in rows if r[2] > 1.1 * d]
    head_end = None
    if wide:
        head_mid = np.mean([(r[0] + r[1]) / 2 for r in wide])
        head_end = "+" if head_mid > sum(shank) / 2 else "-"

    def hollow(p) -> bool:
        """A ring: nothing of it within the shank's core."""
        q = _edge_points(stage, p["path"], max(p["size"]) / 16)
        r = np.linalg.norm(q[:, other] - centre, axis=1)
        return float((r < 0.35 * d).mean()) < 0.02

    rng = np.random.default_rng(0)

    def sample(path, n=1500):
        q = _edge_points(stage, path, d / 4)
        return q[rng.choice(len(q), min(n, len(q)), replace=False)]

    main_pts = sample(main["path"])

    def touches(p) -> bool:
        """Within a tenth of the diameter of the bolt itself (boxes overlap
        for a washer lying beside the tip)."""
        q = sample(p["path"])
        gap = min(float(np.linalg.norm(main_pts - x, axis=1).min()) for x in q)
        return gap < 0.1 * d

    nuts, washers, bolt_parts, separate = [], [], [main], []
    for p in parts:
        if p is main:
            continue
        on_axis = all(abs(p["centroid"][k] - c) < 0.5 * d for k, c in zip(other, centre))
        across = min(p["size"][k] for k in other)
        if on_axis and across > 1.3 * d and p["size"][axis] < 0.8 * main["size"][axis] and hollow(p):
            (nuts if p["size"][axis] >= NUT_MIN_THICKNESS * d else washers).append(p)
        elif touches(p):
            # heads, inserts, a second copy of the same screw
            bolt_parts.append(p)
        else:
            separate.append(p)

    a = "XYZ"[axis]
    thread = {"nominal_m": round(nominal, 4), "pitch_m": round(pitch, 5), "axis": a,
              "measured_diameter_m": round(d, 5), "head_end": head_end}
    notes = [f"M{nominal * 1000:g}x{pitch * 1000:g} along {a} (shank {d * 1000:.2f} mm across)"]
    joints = [{"name": f"part_{i:02d}", "joint_type": "fixed", "parent_prim": main["path"], "child_prim": p["path"]}
              for i, p in enumerate(bolt_parts[1:])]
    joints += [{"name": f"washer_{i:02d}", "joint_type": "fixed", "parent_prim": main["path"],
                "child_prim": w["path"]} for i, w in enumerate(washers)]
    if washers:
        notes.append(f"{len(washers)} washer(s) held captive on the bolt")
    if separate:
        notes.append(f"{', '.join(Path(p['path']).name for p in separate)} lie apart from the bolt: "
                     "separate objects, not jointed (ingest them as a set)")
    mechanisms = []
    if nuts:
        nut = nuts[0]
        n0, n1 = nut["min"][axis], nut["max"][axis]
        # bolt displacement keeping the nut on the threaded shank: shank end
        # to the head. A right-hand thread advances the bolt +axis per +turn.
        lo_d, hi_d = n1 - shank[1], n0 - shank[0]
        if not lo_d <= 0 <= hi_d:
            notes.append(f"nut {Path(nut['path']).name} is not on the shank — no thread joint")
        else:
            anchor = [0.0, 0.0, 0.0]
            for k, c in zip(other, centre):
                anchor[k] = float(c)
            anchor[axis] = float((n0 + n1) / 2)
            turns = [round(lo_d / pitch, 2), round(hi_d / pitch, 2)]
            # a placeholder the helix replaces with its two coupled joints
            joints.append({"name": "thread", "joint_type": "revolute", "parent_prim": nut["path"],
                           "child_prim": main["path"], "axis": a, "lower_limit": 360 * turns[0],
                           "upper_limit": 360 * turns[1], "anchor": anchor})
            # running torque grows with size: ~0.1 N m for an M8 turned by hand
            run = float(template.get("run_torque_nm", round(0.1 * nominal / 0.008, 4)))
            mechanisms.append({"type": "helix", "nut": nut["path"], "bolt": main["path"], "axis": a,
                               "anchor": [round(v, 6) for v in anchor], "pitch_m": pitch, "turns": turns,
                               "run_torque_nm": run})
            notes.append(f"nut {Path(nut['path']).name}: {turns[0]:g} to {turns[1]:g} turns on the shank")
        for extra in nuts[1:]:
            joints.append({"name": f"nut_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": nut["path"], "child_prim": extra["path"]})
    else:
        notes.append("no nut: a loose fastener, thread recorded for assembly")
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints, "mechanisms": mechanisms, "thread": thread,
            "_analysis": {"tier": "thread", "separate": [p["path"] for p in separate], "notes": notes},
            "_instructions": "Thread drafted from the shank. CHECK the size (the file's scale may be off) and pitch."}, notes
