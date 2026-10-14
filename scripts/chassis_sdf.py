#!/usr/bin/env python3
"""A signed-distance field of a static link's big collision part (the R5
chassis shell), computed on the GPU with Warp from HomeHero's planning
collision mesh, for a cuMotion SDF obstacle: the chassis at its true shape
where the arm's mount nestles into it (2.5 cm surface voxels put walls
through the shoulder housing; the bounding box swallowed the arm).

    LD_LIBRARY_PATH=<cumotion libs> <isaac venv python> scripts/chassis_sdf.py [voxel_m] [shell_m]
writes workspace/cumotion/aida_r5/sdf_<link>_<part>.npz (values, origin, voxel, shape)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from cumotion_r5 import BIG_PART_M3, OUT, STATIC, _link_meshes  # noqa: E402


def sdf_grid(pts, faces, voxel, pad):
    import warp as wp
    wp.init()
    lo, hi = pts.min(0) - pad, pts.max(0) + pad
    shape = np.ceil((hi - lo) / voxel).astype(int) + 1
    mesh = wp.Mesh(points=wp.array(pts.astype(np.float32), dtype=wp.vec3), indices=wp.array(faces.astype(np.int32).ravel(), dtype=wp.int32))
    ijk = np.indices(shape).reshape(3, -1).T
    centres = (lo + (ijk + 0.5) * voxel).astype(np.float32)

    @wp.kernel
    def signed(mesh_id: wp.uint64, q: wp.array(dtype=wp.vec3), out: wp.array(dtype=float)):
        i = wp.tid()
        d = wp.mesh_query_point_sign_normal(mesh_id, q[i], 10.0)
        if d.result:
            p = wp.mesh_eval_position(mesh_id, d.face, d.u, d.v)
            out[i] = wp.length(q[i] - p)                # unsigned: the shell's surface distance
        else:
            out[i] = 10.0
    out = wp.zeros(len(centres), dtype=float)
    wp.launch(signed, dim=len(centres), inputs=[mesh.id, wp.array(centres, dtype=wp.vec3), out])
    return out.numpy().reshape(*shape), lo, shape


def main() -> int:
    voxel = float(sys.argv[1]) if len(sys.argv) > 1 else 0.01
    shell = float(sys.argv[2]) if len(sys.argv) > 2 else 0.012      # wall thickness counted as solid (m)
    import trimesh
    meshes = _link_meshes()
    made = []
    for link in STATIC:
        for k, (pts, faces) in enumerate(meshes.get(link, [])):
            lo, hi = pts.min(0), pts.max(0)
            if np.prod(hi - lo) <= BIG_PART_M3:
                continue
            if faces is None or len(faces) == 0:
                hull = trimesh.convex.convex_hull(pts)
                pts, faces = np.asarray(hull.vertices), np.asarray(hull.faces)
            t0 = time.time()
            vals, origin, shape = sdf_grid(pts, faces, voxel, pad=0.03)
            # the chassis is an open shell, not a solid: a sign from the
            # nearest face's normal flips in the hollow (measured: 718 voxels
            # "23 cm inside" a 1 cm wall), so the field is the unsigned
            # surface distance less half the wall - negative within it
            vals = vals - shell / 2
            inside = int((vals < 0).sum())
            np.savez_compressed(OUT / f"sdf_{link}_{k}.npz", values=vals.astype(np.float32), origin=origin, voxel=voxel, shape=shape)
            made.append({"link": link, "part": k, "shape": shape.tolist(), "voxel": voxel, "origin": origin.round(4).tolist(),
                         "inside_voxels": inside, "shell_m": shell, "seconds": round(time.time() - t0, 2)})
            print(made[-1])
    (OUT / "sdf_parts.json").write_text(json.dumps(made))
    return 0


if __name__ == "__main__":
    sys.exit(main())
