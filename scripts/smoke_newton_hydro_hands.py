#!/usr/bin/env python3
"""CUDA smoke test for a generated Newton hydroelastic-hand wrapper.

The test builds all 22 authored mesh SDFs, then touches one fingertip with a
temporary hydroelastic sphere. It does not advance the solver or alter the
USD. With ``--write-manifest``, GPU evidence is added to the JSON manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from newton_runtime import require_newton_15


EXPECTED_HYDRO_MESHES = 22


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_smoke(wrapper: Path, device: str = "cuda:0",
              target_token: str = "psyonic_left_index_L2") -> dict:
    import numpy as np
    from scipy.spatial import ConvexHull
    from pxr import Usd, UsdGeom
    from newton.geometry import HydroelasticSDF

    newton, wp = require_newton_15()
    wp.set_device(device)
    wrapper = wrapper.expanduser().resolve()
    stage = Usd.Stage.Open(str(wrapper))
    if not stage:
        raise RuntimeError(f"could not open {wrapper}")
    hydro_prims = [
        prim for prim in stage.Traverse()
        if prim.GetName() == "NewtonHydroCollision"
    ]
    if len(hydro_prims) != EXPECTED_HYDRO_MESHES:
        raise ValueError(
            f"expected {EXPECTED_HYDRO_MESHES} hydro meshes, found {len(hydro_prims)}"
        )
    matching = [prim for prim in hydro_prims if target_token in str(prim.GetPath())]
    if len(matching) != 1:
        raise ValueError(
            f"--target-token must select one hydro mesh, selected {len(matching)}"
        )
    target = matching[0]

    # Place the probe across the largest convex-hull face. A sphere centered
    # inside the hull has no crossing surface and is not a useful contact test.
    points = np.asarray(UsdGeom.Mesh(target).GetPointsAttr().Get(), dtype=float)
    hull = ConvexHull(points)
    areas = []
    for face in hull.simplices:
        a, b, c = points[face]
        areas.append(np.linalg.norm(np.cross(b - a, c - a)) * 0.5)
    face_index = int(np.argmax(areas))
    surface = points[hull.simplices[face_index]].mean(axis=0)
    normal = hull.equations[face_index, :3]
    normal /= np.linalg.norm(normal)
    radius = 0.004
    penetration = 0.002
    probe_local = surface + normal * (radius - penetration)
    matrix = np.asarray(
        UsdGeom.XformCache().GetLocalToWorldTransform(target), dtype=float
    )
    probe_world = probe_local @ matrix[:3, :3] + matrix[3, :3]

    full_builder = newton.ModelBuilder()
    full_imported = full_builder.add_usd(
        stage, load_visual_shapes=False, load_static_visual_shapes=False
    )
    hydro_paths = sorted(str(prim.GetPath()) for prim in hydro_prims)
    full_hydro_indices = [
        full_imported["path_shape_map"][path] for path in hydro_paths
    ]
    full_model = full_builder.finalize(device=device)
    full_sdf_indices = full_model._shape_sdf_index.numpy()
    missing = [
        path for path, index in zip(hydro_paths, full_hydro_indices, strict=True)
        if int(full_sdf_indices[index]) < 0
    ]
    if missing:
        raise ValueError(f"Newton did not build {len(missing)} hand SDFs")

    # Collision pair eligibility is baked during finalize(), so construct a
    # second focused model and clear unrelated flags on the builder first.
    builder = newton.ModelBuilder()
    imported = builder.add_usd(
        stage, load_visual_shapes=False, load_static_visual_shapes=False
    )
    target_index = imported["path_shape_map"][str(target.GetPath())]
    for index in range(builder.shape_count):
        if index != target_index:
            builder.shape_flags[index] &= ~newton.ShapeFlags.COLLIDE_SHAPES
    probe_config = newton.ModelBuilder.ShapeConfig(
        is_hydroelastic=True,
        sdf_max_resolution=32,
        sdf_narrow_band_range=(-0.004, 0.004),
        kh=1.0e10,
        gap=0.001,
    )
    probe_index = builder.add_shape_sphere(
        body=-1,
        xform=wp.transform(wp.vec3(*probe_world), wp.quat_identity()),
        radius=radius,
        cfg=probe_config,
    )
    model = builder.finalize(device=device)
    sdf_indices = model._shape_sdf_index.numpy()
    if int(sdf_indices[probe_index]) < 0:
        raise ValueError("Newton did not build the probe SDF")

    state = model.state()
    newton.eval_fk(model, model.joint_q, model.joint_qd, state)
    pipeline = newton.CollisionPipeline(
        model,
        rigid_contact_max=1000,
        broad_phase="explicit",
        sdf_hydroelastic_config=HydroelasticSDF.Config(
            output_contact_surface=True,
            reduce_contacts=True,
            anchor_contact=True,
            buffer_fraction=1.0,
        ),
    )
    contacts = pipeline.contacts()
    pipeline.collide(state, contacts)
    wp.synchronize()
    pair_count = int(
        pipeline.narrow_phase.shape_pairs_sdf_sdf_count.numpy()[0]
    )
    contact_count = int(contacts.rigid_contact_count.numpy()[0])
    shape0 = contacts.rigid_contact_shape0.numpy()[:contact_count]
    shape1 = contacts.rigid_contact_shape1.numpy()[:contact_count]
    target_probe_contacts = int(np.sum(
        ((shape0 == target_index) & (shape1 == probe_index))
        | ((shape0 == probe_index) & (shape1 == target_index))
    ))
    if pair_count != 1:
        raise ValueError(f"expected one isolated SDF pair, found {pair_count}")
    if target_probe_contacts <= 0:
        raise ValueError("isolated fingertip/probe pair generated no hydro contacts")

    return {
        "date": datetime.now(timezone.utc).isoformat(),
        "method": "isolated_fingertip_hydroelastic_contact_smoke",
        "wrapper_sha256": _sha256(wrapper),
        "newton_version": newton.__version__,
        "warp_version": wp.__version__,
        "device": wp.get_device().name,
        "built_hand_sdf_count": len(full_hydro_indices),
        "target_path": str(target.GetPath()),
        "probe": {
            "shape": "temporary_static_hydroelastic_sphere",
            "radius_m": radius,
            "penetration_m": penetration,
            "center_world_m": [float(value) for value in probe_world],
        },
        "sdf_sdf_pair_count": pair_count,
        "rigid_contact_count": contact_count,
        "target_probe_contact_count": target_probe_contacts,
        "passed": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wrapper", type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--target-token", default="psyonic_left_index_L2")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args(argv)
    try:
        evidence = run_smoke(args.wrapper, args.device, args.target_token)
        if args.write_manifest:
            manifest_path = args.manifest or args.wrapper.with_suffix(".manifest.json")
            manifest = json.loads(manifest_path.read_text())
            manifest["gpu_validation"] = evidence
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(evidence, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
