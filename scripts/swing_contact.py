#!/usr/bin/env python3
"""How far a part can turn about a pivot before it runs into its mate.

PhysX never collides two links joined by a joint, so a pair of scissors, a
pair of pliers or a clothes peg is stopped only by its joint limit: a limit
past the point where the parts meet runs them through each other. This finds
that point from geometry.

The moving part's surface is turned in small steps about the pivot axis; it
has met the fixed part when one of its points lies INSIDE the fixed part -
looking down the axis, within the fixed part's footprint and, along the axis,
within its thickness there. A point on the fixed part's face (a scissor blade
sliding over its mate, a layer above) is not inside, so blades pass while
pliers' jaws, scissors' finger rings and a peg's handles stop each other.
What touches at rest touches by design: round the pivot it is left out,
elsewhere contact is that overlap growing (a shut peg's jaws pushed into
each other).
"""
from __future__ import annotations

import math

import numpy as np


def surface_points(stage, paths, step: float) -> np.ndarray:
    """World points over the meshes' faces (vertices, edges and interiors) about
    `step` apart: a large flat face has no vertices inside it."""
    from pxr import Gf, Usd, UsdGeom

    out = []
    for path in paths:
        for prim in Usd.PrimRange(stage.GetPrimAtPath(path)):
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(prim)
            m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
            v = np.array([list(m.Transform(Gf.Vec3d(*q))) for q in mesh.GetPointsAttr().Get() or []])
            if not len(v):
                continue
            idx = mesh.GetFaceVertexIndicesAttr().Get() or []
            off = 0
            tris = []
            for n in mesh.GetFaceVertexCountsAttr().Get() or []:
                for k in range(1, n - 1):
                    tris.append((idx[off], idx[off + k], idx[off + k + 1]))
                off += n
            out.append(v)
            if not tris:
                continue
            t = v[np.array(tris)]                                      # (n, 3, 3)
            edge = np.max(np.linalg.norm(t - np.roll(t, 1, axis=1), axis=2), axis=1)
            # as fine as asked, within a budget of points (a long flat face needs
            # many; a per-triangle cap left holes in a blade's footprint)
            st = step
            total = float(np.sum(np.ceil(edge / st) ** 2) / 2)
            if total > 1.5e6:
                st *= math.sqrt(total / 1.5e6)
            dens_all = np.clip(np.ceil(edge / st), 1, None).astype(int)
            for dens in np.unique(dens_all):
                sel = t[dens_all == dens]
                bary = np.array([(i / dens, j / dens) for i in range(dens + 1) for j in range(dens + 1 - i)])
                a, b, c = sel[:, 0], sel[:, 1], sel[:, 2]
                pts = (a[:, None] + bary[None, :, :1] * (b - a)[:, None] + bary[None, :, 1:] * (c - a)[:, None])
                out.append(pts.reshape(-1, 3))
    return np.vstack(out) if out else np.zeros((0, 3))


def swing_until_contact(stage, fixed_paths, moving_paths, pivot, axis: int, direction: float, span: float,
                        max_deg: float = 120.0, step_deg: float = 0.5, axis_sign: float = 1.0):
    """Degrees the moving part turns (joint sign `direction`, about +axis
    times axis_sign) before it is inside the fixed part; None if it never is
    within max_deg."""
    cell = 0.01 * span
    plane = [k for k in range(3) if k != axis]
    fa = surface_points(stage, fixed_paths, cell / 2)
    fb = surface_points(stage, moving_paths, cell / 2)
    if not len(fa) or not len(fb):
        return None
    # the fixed part's thickness along the axis, cell by cell looking down it
    keys = [tuple(k) for k in np.floor(fa[:, plane] / cell).astype(int)]
    span_z: dict = {}
    for key, z in zip(keys, fa[:, axis]):
        lo, hi = span_z.get(key, (z, z))
        span_z[key] = (min(lo, z), max(hi, z))
    # a point at the footprint's rim is touching, not inside: only cells whose
    # neighbours are all in the footprint count (two halves lying side by side
    # share a rim along their whole length)
    core = {c for c in span_z if all((c[0] + du, c[1] + dv) in span_z for du in (-1, 0, 1) for dv in (-1, 0, 1))}
    span_z = {c: span_z[c] for c in core}
    pv = np.array(pivot, float)

    def inside(q):
        cells = np.floor(q[:, plane] / cell).astype(int)
        hit = np.zeros(len(q), bool)
        for i, (c, z) in enumerate(zip(map(tuple, cells), q[:, axis])):
            iv = span_z.get(c)
            if iv is not None:
                m = 0.15 * (iv[1] - iv[0]) + 0.05 * cell
                hit[i] = iv[0] + m < z < iv[1] - m
        return hit

    # what touches at rest touches by design. Round the pivot (the rivet's
    # stack, which low-poly halves often run into each other at) it is left
    # out entirely - turning brings the next ring of it inside. Elsewhere (a
    # shut peg's jaws) contact is the overlap GROWING past its rest amount:
    # jaws pushed into each other grow it, opening them shrinks it.
    pv = np.array(pivot, float)
    rest_in = inside(fb)
    keep = np.ones(len(fb), bool)
    if rest_in.any():
        d_rest = np.linalg.norm((fb[rest_in] - pv)[:, plane], axis=1)
        near = d_rest[d_rest < 0.3 * span]
        if len(near):
            hub = near.max() + 2 * cell
            keep = np.linalg.norm((fb - pv)[:, plane], axis=1) > hub
    rel = fb[keep] - pv
    if not len(rel):
        return None
    base = int(rest_in[keep].sum())
    u, v = plane
    # a positive turn about +axis is counter-clockwise in (u, v) for X and Z,
    # clockwise for Y (Z onto X)
    ccw = axis_sign * (-1.0 if axis == 1 else 1.0)
    for k in range(1, int(max_deg / step_deg) + 1):
        q = math.radians(ccw * direction * k * step_deg)
        c, s = math.cos(q), math.sin(q)
        r = rel.copy()
        r[:, u], r[:, v] = c * rel[:, u] - s * rel[:, v], s * rel[:, u] + c * rel[:, v]
        if inside(r + pv).sum() >= base + 3:
            return (k - 1) * step_deg
    return None
