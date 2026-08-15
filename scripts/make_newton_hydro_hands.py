#!/usr/bin/env python3
"""Create a non-destructive Newton 1.5 hydroelastic-hand USD wrapper.

The source robot is referenced, never edited.  For every Psyonic hand rigid
body, this tool merges Newton's imported collision coverage into one closed
convex hull, disables the overlapping source collision shapes in the wrapper,
and applies ``NewtonSDFCollisionAPI`` to the replacement.

Run in the side-by-side Newton environment::

    WARP_CACHE_PATH=/tmp/newton-warp-cache \
      .venv-newton/bin/python scripts/make_newton_hydro_hands.py \
      SOURCE.usda OUTPUT.usda

Hydroelastic contact is bilateral: manipulated objects must also use an SDF
collision shape with ``newton:hydroelasticEnabled = true``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


GENERATOR_CONTRACT = "homehero-newton-hydro-hands-v1"
EXPECTED_HAND_BODIES = 22
EXPECTED_SOURCE_HAND_SHAPES = 68
HYDRO_CHILD_NAME = "NewtonHydroCollision"
HAND_PREFIXES = ("psyonic_left_", "psyonic_right_")
FINGERS = ("index", "middle", "pinky", "ring", "thumb")


@dataclass(frozen=True)
class HydroConfig:
    sdf_max_resolution: int = 64
    sdf_narrow_band_inner: float = -0.004
    sdf_narrow_band_outer: float = 0.004
    sdf_padding: float = 0.004
    sdf_texture_format: str = "uint16"
    hydroelastic_stiffness: float = 1.0e10
    contact_margin: float = 0.0
    contact_gap: float = 0.001

    def validate(self) -> None:
        if self.sdf_max_resolution <= 0 or self.sdf_max_resolution % 8:
            raise ValueError("--sdf-resolution must be positive and divisible by 8")
        if self.sdf_narrow_band_inner > 0:
            raise ValueError("--sdf-inner must be <= 0")
        if self.sdf_narrow_band_outer < 0:
            raise ValueError("--sdf-outer must be >= 0")
        if self.sdf_padding < 0:
            raise ValueError("--sdf-padding must be >= 0")
        if self.hydroelastic_stiffness <= 0:
            raise ValueError("--stiffness must be > 0")
        if self.contact_margin < 0 or self.contact_gap < 0:
            raise ValueError("contact margin and gap must be >= 0")


def is_hand_body_path(path: str) -> bool:
    return Path(path).name.startswith(HAND_PREFIXES)


def adjacent_hand_body_pairs(body_paths: Iterable[str]) -> list[tuple[str, str]]:
    """Return hand kinematic-neighbor pairs that should not self-collide."""
    by_name = {Path(path).name: path for path in body_paths}
    pairs: list[tuple[str, str]] = []
    for side in ("left", "right"):
        base = by_name[f"psyonic_{side}_base"]
        for finger in FINGERS:
            l1 = by_name[f"psyonic_{side}_{finger}_L1"]
            l2 = by_name[f"psyonic_{side}_{finger}_L2"]
            pairs.extend(((base, l1), (l1, l2)))
    return pairs


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _deterministic_convex_hull(points):
    """Return lexically ordered vertices and outward-wound triangle faces."""
    import numpy as np
    from scipy.spatial import ConvexHull

    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError(f"expected Nx3 points, got {points.shape}")
    points = np.unique(points.round(9), axis=0)
    if len(points) < 4:
        raise ValueError("a volumetric collision hull requires at least four points")

    hull = ConvexHull(points)
    vertices = points[hull.vertices]
    # Lexical vertex order makes generated ASCII USD stable across runs.
    order = np.lexsort((vertices[:, 2], vertices[:, 1], vertices[:, 0]))
    vertices = vertices[order]
    old_to_new = {int(old): new for new, old in enumerate(hull.vertices[order])}
    center = vertices.mean(axis=0)
    faces = []
    for simplex in hull.simplices:
        face = [old_to_new[int(index)] for index in simplex]
        a, b, c = vertices[face]
        if np.dot(np.cross(b - a, c - a), (a + b + c) / 3.0 - center) < 0:
            face[1], face[2] = face[2], face[1]
        # Preserve winding while rotating the lowest index to the front.
        start = face.index(min(face))
        face = face[start:] + face[:start]
        faces.append(tuple(face))
    faces = np.asarray(sorted(faces), dtype=np.int32)

    signed_volume = float(
        np.sum(
            np.einsum(
                "ij,ij->i",
                vertices[faces[:, 0]],
                np.cross(vertices[faces[:, 1]], vertices[faces[:, 2]]),
            )
        )
        / 6.0
    )
    if signed_volume <= 0:
        raise ValueError(f"generated hull has non-positive volume {signed_volume}")
    return vertices, faces, signed_volume


def _body_local_points(builder, shape_indices: Sequence[int]):
    import numpy as np
    from scipy.spatial.transform import Rotation

    clouds = []
    for shape_index in shape_indices:
        source = builder.shape_source[shape_index]
        vertices = getattr(source, "_vertices", None)
        if vertices is None:
            raise TypeError(f"hand shape {shape_index} is not a mesh")
        points = np.asarray(vertices, dtype=np.float64)
        points *= np.asarray(builder.shape_scale[shape_index], dtype=np.float64)
        transform = np.asarray(list(builder.shape_transform[shape_index]), dtype=np.float64)
        points = Rotation.from_quat(transform[3:7]).apply(points) + transform[:3]
        clouds.append(points)
    return np.concatenate(clouds, axis=0)


def _set_custom(prim, name: str, value_type, value) -> None:
    prim.CreateAttribute(name, value_type, custom=True).Set(value)


def _instance_roots_for_paths(stage, paths: Iterable[str]) -> list[str]:
    """Find the instance roots whose proxies contain any requested path."""
    roots: set[str] = set()
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if not prim:
            raise ValueError(f"missing composed prim {path}")
        if not prim.IsInstanceProxy():
            continue
        ancestor = prim.GetParent()
        while ancestor and not ancestor.IsPseudoRoot():
            if ancestor.IsInstance():
                roots.add(str(ancestor.GetPath()))
                break
            ancestor = ancestor.GetParent()
        else:
            raise ValueError(f"could not find instance root for proxy {path}")
    return sorted(roots)


def _author_hydro_mesh(stage, body_path: str, vertices, faces,
                       source_paths: Sequence[str], config: HydroConfig) -> str:
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics, Vt

    mesh_path = f"{body_path}/{HYDRO_CHILD_NAME}"
    mesh = UsdGeom.Mesh.Define(stage, mesh_path)
    mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(vertices.astype("float32")))
    mesh.CreateFaceVertexCountsAttr([3] * len(faces))
    mesh.CreateFaceVertexIndicesAttr(faces.reshape(-1).tolist())
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreatePurposeAttr(UsdGeom.Tokens.guide)
    minimum = vertices.min(axis=0)
    maximum = vertices.max(axis=0)
    mesh.CreateExtentAttr([
        Gf.Vec3f(*(float(value) for value in minimum)),
        Gf.Vec3f(*(float(value) for value in maximum)),
    ])

    prim = mesh.GetPrim()
    collision = UsdPhysics.CollisionAPI.Apply(prim)
    collision.CreateCollisionEnabledAttr(True)
    prim.AddAppliedSchema("NewtonSDFCollisionAPI")
    _set_custom(prim, "newton:sdfMaxResolution", Sdf.ValueTypeNames.Int,
                config.sdf_max_resolution)
    _set_custom(prim, "newton:sdfNarrowBandInner", Sdf.ValueTypeNames.Float,
                config.sdf_narrow_band_inner)
    _set_custom(prim, "newton:sdfNarrowBandOuter", Sdf.ValueTypeNames.Float,
                config.sdf_narrow_band_outer)
    _set_custom(prim, "newton:sdfPadding", Sdf.ValueTypeNames.Float,
                config.sdf_padding)
    _set_custom(prim, "newton:sdfTextureFormat", Sdf.ValueTypeNames.Token,
                config.sdf_texture_format)
    _set_custom(prim, "newton:hydroelasticEnabled", Sdf.ValueTypeNames.Bool,
                True)
    _set_custom(prim, "newton:hydroelasticStiffness", Sdf.ValueTypeNames.Float,
                config.hydroelastic_stiffness)
    _set_custom(prim, "newton:contactMargin", Sdf.ValueTypeNames.Float,
                config.contact_margin)
    _set_custom(prim, "newton:contactGap", Sdf.ValueTypeNames.Float,
                config.contact_gap)
    _set_custom(prim, "homehero:hydroSourceShapes",
                Sdf.ValueTypeNames.StringArray, list(source_paths))
    _set_custom(prim, "homehero:generatorContract", Sdf.ValueTypeNames.String,
                GENERATOR_CONTRACT)
    return mesh_path


def _check_closed_triangle_mesh(vertices, faces) -> float:
    import numpy as np

    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError("hydro mesh must be triangulated")
    edge_counts: dict[tuple[int, int], int] = {}
    for face in faces:
        if len(set(int(value) for value in face)) != 3:
            raise ValueError("hydro mesh contains a degenerate triangle")
        for a, b in zip(face, (face[1], face[2], face[0]), strict=True):
            edge = tuple(sorted((int(a), int(b))))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
    bad_edges = [edge for edge, count in edge_counts.items() if count != 2]
    if bad_edges:
        raise ValueError(f"hydro mesh is not watertight ({len(bad_edges)} open/nonmanifold edges)")
    volume = float(
        np.sum(
            np.einsum(
                "ij,ij->i",
                vertices[faces[:, 0]],
                np.cross(vertices[faces[:, 1]], vertices[faces[:, 2]]),
            )
        )
        / 6.0
    )
    if volume <= 0:
        raise ValueError(f"hydro mesh has non-positive signed volume {volume}")
    return volume


def _convex_overlap_depth(vertices_a, vertices_b) -> float | None:
    """Return the largest shared inscribed-ball radius, or None if disjoint.

    The generated link meshes are convex. Combining their half-space systems
    with a four-variable linear program gives a deterministic CPU-only test
    for volumetric overlap; positive depth means the interiors intersect.
    """
    import numpy as np
    from scipy.optimize import linprog
    from scipy.spatial import ConvexHull

    vertices_a = np.asarray(vertices_a, dtype=np.float64)
    vertices_b = np.asarray(vertices_b, dtype=np.float64)
    if (
        np.any(vertices_a.max(axis=0) < vertices_b.min(axis=0))
        or np.any(vertices_b.max(axis=0) < vertices_a.min(axis=0))
    ):
        return None
    equations = np.vstack((
        ConvexHull(vertices_a).equations,
        ConvexHull(vertices_b).equations,
    ))
    # scipy's hull normals are unit length, so the slack variable is metres.
    constraints = np.column_stack((
        equations[:, :3], np.ones(len(equations), dtype=np.float64)
    ))
    result = linprog(
        [0.0, 0.0, 0.0, -1.0],
        A_ub=constraints,
        b_ub=-equations[:, 3],
        bounds=[(None, None)] * 4,
        method="highs",
    )
    return float(result.x[3]) if result.success else None


def validate_wrapper(wrapper: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate USD topology/schema and Newton's importer contract on CPU."""
    import numpy as np
    import newton
    import warp as wp
    from pxr import Usd, UsdGeom, UsdPhysics

    newton.use_coord_layout_targets = True
    wp.set_device("cpu")
    stage = Usd.Stage.Open(str(wrapper))
    if not stage:
        raise RuntimeError(f"could not reopen generated stage {wrapper}")
    root = stage.GetDefaultPrim()
    selection = root.GetVariantSets().GetVariantSet("Physics").GetVariantSelection()
    if selection != "physics":
        raise ValueError(f"expected Physics=physics, found {selection!r}")

    hydro_paths = manifest["hydro_mesh_paths"]
    hydro_world_points = {}
    xform_cache = UsdGeom.XformCache()
    for path in hydro_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.HasAPI("NewtonSDFCollisionAPI"):
            raise ValueError(f"missing NewtonSDFCollisionAPI at {path}")
        if prim.GetAttribute("newton:hydroelasticEnabled").Get() is not True:
            raise ValueError(f"hydroelastic is not enabled at {path}")
        mesh = UsdGeom.Mesh(prim)
        points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
        indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int64)
        counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=np.int64)
        if not np.all(counts == 3):
            raise ValueError(f"non-triangle face at {path}")
        _check_closed_triangle_mesh(points, indices.reshape(-1, 3))
        matrix = np.asarray(
            xform_cache.GetLocalToWorldTransform(prim), dtype=np.float64
        )
        hydro_world_points[path] = points @ matrix[:3, :3] + matrix[3, :3]

    for path in manifest["disabled_source_shape_paths"]:
        prim = stage.GetPrimAtPath(path)
        enabled = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
        if enabled is not False:
            raise ValueError(f"source collision was not disabled: {path}")

    for first, second in manifest["adjacent_filtered_body_pairs"]:
        targets = UsdPhysics.FilteredPairsAPI(
            stage.GetPrimAtPath(first)
        ).GetFilteredPairsRel().GetTargets()
        if second not in {str(path) for path in targets}:
            raise ValueError(f"missing adjacent-body collision filter: {first} -> {second}")

    expected_filtered_body_pairs = {
        tuple(sorted(pair))
        for pair in manifest["adjacent_filtered_body_pairs"]
    }
    overlap_body_pairs = set()
    overlap_depths = {}
    for index, first_path in enumerate(hydro_paths):
        for second_path in hydro_paths[index + 1:]:
            depth = _convex_overlap_depth(
                hydro_world_points[first_path], hydro_world_points[second_path]
            )
            if depth is not None and depth > 1.0e-6:
                body_pair = tuple(sorted((
                    str(Path(first_path).parent),
                    str(Path(second_path).parent),
                )))
                overlap_body_pairs.add(body_pair)
                overlap_depths[" | ".join(body_pair)] = depth
    unfiltered_overlaps = overlap_body_pairs - expected_filtered_body_pairs
    if unfiltered_overlaps:
        raise ValueError(
            f"found {len(unfiltered_overlaps)} non-adjacent hydro overlaps"
        )

    builder = newton.ModelBuilder()
    result = builder.add_usd(
        stage, load_visual_shapes=False, load_static_visual_shapes=False
    )
    if (builder.body_count, builder.joint_count) != (52, 52):
        raise ValueError(
            "articulation contract changed: "
            f"{builder.body_count} bodies, {builder.joint_count} joints"
        )
    hydro_flag = int(newton.ShapeFlags.HYDROELASTIC)
    collide_flag = int(newton.ShapeFlags.COLLIDE_SHAPES)
    imported_hydro = []
    for path in hydro_paths:
        shape_index = result["path_shape_map"].get(path)
        if shape_index is None:
            raise ValueError(f"Newton did not import hydro shape {path}")
        if not builder.shape_flags[shape_index] & hydro_flag:
            raise ValueError(f"Newton did not enable hydroelastic at {path}")
        if not builder.shape_flags[shape_index] & collide_flag:
            raise ValueError(f"Newton did not enable collision at {path}")
        if builder.shape_sdf_max_resolution[shape_index] != manifest["config"]["sdf_max_resolution"]:
            raise ValueError(f"Newton did not preserve SDF resolution at {path}")
        imported_hydro.append(shape_index)
    disabled_still_colliding = []
    for path in manifest["disabled_source_shape_paths"]:
        shape_index = result["path_shape_map"].get(path)
        if shape_index is not None and builder.shape_flags[shape_index] & collide_flag:
            disabled_still_colliding.append(path)
    if disabled_still_colliding:
        raise ValueError(
            f"Newton still enables {len(disabled_still_colliding)} disabled source shapes"
        )
    imported_filter_pairs = {
        tuple(sorted((int(pair[0]), int(pair[1]))))
        for pair in builder.shape_collision_filter_pairs
    }
    missing_imported_hydro_filters = []
    for first, second in manifest["adjacent_filtered_body_pairs"]:
        first_index = result["path_shape_map"].get(
            f"{first}/{HYDRO_CHILD_NAME}"
        )
        second_index = result["path_shape_map"].get(
            f"{second}/{HYDRO_CHILD_NAME}"
        )
        pair = tuple(sorted((first_index, second_index)))
        if pair not in imported_filter_pairs:
            missing_imported_hydro_filters.append([first, second])
    if missing_imported_hydro_filters:
        raise ValueError(
            "Newton did not import "
            f"{len(missing_imported_hydro_filters)} adjacent hydro filters"
        )
    hydro_count = sum(bool(flags & hydro_flag) for flags in builder.shape_flags)
    if hydro_count != EXPECTED_HAND_BODIES:
        raise ValueError(f"expected 22 hydro shapes after import, found {hydro_count}")
    return {
        "physics_variant": selection,
        "body_count": builder.body_count,
        "joint_count": builder.joint_count,
        "shape_count": builder.shape_count,
        "colliding_shape_count": sum(
            bool(flags & collide_flag) for flags in builder.shape_flags
        ),
        "disabled_source_shapes_still_colliding": len(disabled_still_colliding),
        "default_pose_hydro_overlap_count": len(overlap_body_pairs),
        "default_pose_unfiltered_hydro_overlap_count": len(unfiltered_overlaps),
        "default_pose_overlap_depth_m": dict(sorted(overlap_depths.items())),
        "imported_adjacent_hydro_filter_count": len(
            manifest["adjacent_filtered_body_pairs"]
        ),
        "hydro_shape_count": hydro_count,
        "hydro_shape_indices": imported_hydro,
    }


def generate(source: Path, output: Path, manifest_path: Path,
             config: HydroConfig, *, force: bool = False,
             validate: bool = True) -> dict[str, Any]:
    import numpy as np
    import newton
    import warp as wp
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    config.validate()
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source == output:
        raise ValueError("output must not replace the canonical source")
    previous_gpu_validation = None
    if force and output.is_file() and manifest_path.is_file():
        try:
            previous_manifest = json.loads(manifest_path.read_text())
            candidate = previous_manifest.get("gpu_validation")
            if (
                candidate
                and candidate.get("wrapper_sha256") == _sha256(output)
                and candidate.get("passed") is True
            ):
                previous_gpu_validation = candidate
        except (OSError, ValueError, TypeError):
            # Stale or hand-edited evidence is simply not carried forward.
            previous_gpu_validation = None
    for target in (output, manifest_path):
        if target.exists() and not force:
            raise FileExistsError(f"{target} exists; pass --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    newton.use_coord_layout_targets = True
    wp.set_device("cpu")
    source_stage = Usd.Stage.Open(str(source))
    if not source_stage:
        raise RuntimeError(f"could not open {source}")
    source_root = source_stage.GetDefaultPrim()
    physics_variants = source_root.GetVariantSets().GetVariantSet("Physics")
    if "physics" not in physics_variants.GetVariantNames():
        raise ValueError("source stage has no generic Physics=physics variant")
    physics_variants.SetVariantSelection("physics")

    builder = newton.ModelBuilder()
    imported = builder.add_usd(
        source_stage, load_visual_shapes=False, load_static_visual_shapes=False
    )
    hand_bodies = {
        path: index for path, index in imported["path_body_map"].items()
        if is_hand_body_path(path)
    }
    if len(hand_bodies) != EXPECTED_HAND_BODIES:
        raise ValueError(
            f"expected {EXPECTED_HAND_BODIES} Psyonic hand bodies, found {len(hand_bodies)}"
        )
    body_paths_by_index = {index: path for path, index in hand_bodies.items()}
    hand_shapes = {
        path: index for path, index in imported["path_shape_map"].items()
        if builder.shape_body[index] in body_paths_by_index
    }
    if len(hand_shapes) != EXPECTED_SOURCE_HAND_SHAPES:
        raise ValueError(
            f"expected {EXPECTED_SOURCE_HAND_SHAPES} imported hand shapes, "
            f"found {len(hand_shapes)}"
        )
    shape_paths_by_index = {index: path for path, index in hand_shapes.items()}

    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp.usda")
    if temporary.exists():
        temporary.unlink()
    stage = Usd.Stage.CreateNew(str(temporary))
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(source_stage))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(source_stage))
    root = UsdGeom.Xform.Define(stage, source_root.GetPath()).GetPrim()
    relative_reference = os.path.relpath(source, output.parent).replace(os.sep, "/")
    root.GetReferences().AddReference(relative_reference, source_root.GetPath())
    stage.SetDefaultPrim(root)
    root.GetVariantSets().GetVariantSet("Physics").SetVariantSelection("physics")
    _set_custom(root, "homehero:generatorContract", Sdf.ValueTypeNames.String,
                GENERATOR_CONTRACT)
    _set_custom(root, "homehero:sourceSha256", Sdf.ValueTypeNames.String,
                _sha256(source))
    _set_custom(root, "homehero:newtonVersion", Sdf.ValueTypeNames.String,
                getattr(newton, "__version__", "unknown"))
    _set_custom(root, "homehero:warpVersion", Sdf.ValueTypeNames.String,
                getattr(wp, "__version__", "unknown"))

    # USD forbids property opinions on instance proxies.  De-instance only the
    # affected collision mesh instances in this wrapper so their composed child
    # meshes can be disabled; the canonical layer and unrelated render
    # instancing remain unchanged.
    deinstanced_paths = _instance_roots_for_paths(stage, hand_shapes)
    for path in deinstanced_paths:
        stage.OverridePrim(path).SetInstanceable(False)

    for path in sorted(hand_shapes):
        prim = stage.GetPrimAtPath(path)
        if not prim:
            raise ValueError(f"referenced source shape disappeared: {path}")
        if prim.IsInstanceProxy():
            raise ValueError(f"source collision remains an instance proxy: {path}")
        UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(False)

    mesh_records = []
    hydro_mesh_paths = []
    for body_path, body_index in sorted(hand_bodies.items()):
        shape_indices = sorted(
            index for index in hand_shapes.values()
            if builder.shape_body[index] == body_index
        )
        source_paths = [shape_paths_by_index[index] for index in shape_indices]
        points = _body_local_points(builder, shape_indices)
        vertices, faces, volume = _deterministic_convex_hull(points)
        # An independent edge/volume check catches accidental hull corruption
        # before any layer is written.
        checked_volume = _check_closed_triangle_mesh(vertices, faces)
        if not np.isclose(volume, checked_volume, rtol=1e-8, atol=1e-14):
            raise ValueError(f"inconsistent hull volume for {body_path}")
        hydro_path = _author_hydro_mesh(
            stage, body_path, vertices, faces, source_paths, config
        )
        hydro_mesh_paths.append(hydro_path)
        mesh_records.append({
            "body_path": body_path,
            "mesh_path": hydro_path,
            "source_shape_paths": source_paths,
            "vertex_count": int(len(vertices)),
            "triangle_count": int(len(faces)),
            "volume_m3": volume,
        })

    adjacent_pairs = adjacent_hand_body_pairs(hand_bodies)
    for first, second in adjacent_pairs:
        prim = stage.GetPrimAtPath(first)
        api = UsdPhysics.FilteredPairsAPI.Apply(prim)
        relationship = api.CreateFilteredPairsRel()
        targets = {str(path) for path in relationship.GetTargets()}
        targets.add(second)
        relationship.SetTargets([Sdf.Path(path) for path in sorted(targets)])

    stage.GetRootLayer().Save()
    os.replace(temporary, output)

    manifest: dict[str, Any] = {
        "contract": GENERATOR_CONTRACT,
        "source": str(source),
        "source_sha256": root.GetAttribute("homehero:sourceSha256").Get(),
        "wrapper": str(output),
        "relative_reference": relative_reference,
        "newton_version": getattr(newton, "__version__", "unknown"),
        "warp_version": getattr(wp, "__version__", "unknown"),
        "physics_variant": "physics",
        "config": asdict(config),
        "hand_body_count": len(hand_bodies),
        "disabled_source_shape_count": len(hand_shapes),
        "disabled_source_shape_paths": sorted(hand_shapes),
        "deinstanced_collision_root_count": len(deinstanced_paths),
        "deinstanced_collision_root_paths": deinstanced_paths,
        "hydro_mesh_count": len(hydro_mesh_paths),
        "hydro_mesh_paths": hydro_mesh_paths,
        "adjacent_filter_count": len(adjacent_pairs),
        "adjacent_filtered_body_pairs": [list(pair) for pair in adjacent_pairs],
        "meshes": mesh_records,
    }
    if validate:
        manifest["validation"] = validate_wrapper(output, manifest)
    if (
        previous_gpu_validation
        and previous_gpu_validation["wrapper_sha256"] == _sha256(output)
    ):
        manifest["gpu_validation"] = previous_gpu_validation
    temporary_manifest = manifest_path.with_name(
        f".{manifest_path.name}.{os.getpid()}.tmp"
    )
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    os.replace(temporary_manifest, manifest_path)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="canonical robot USD/USDA")
    parser.add_argument("output", type=Path, help="new wrapper USDA")
    parser.add_argument(
        "--manifest", type=Path,
        help="manifest path (default: OUTPUT with .manifest.json suffix)",
    )
    parser.add_argument("--sdf-resolution", type=int, default=64)
    parser.add_argument("--sdf-inner", type=float, default=-0.004)
    parser.add_argument("--sdf-outer", type=float, default=0.004)
    parser.add_argument("--sdf-padding", type=float, default=0.004)
    parser.add_argument("--stiffness", type=float, default=1.0e10)
    parser.add_argument("--contact-margin", type=float, default=0.0)
    parser.add_argument("--contact-gap", type=float, default=0.001)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-validate", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest_path = args.manifest or args.output.with_suffix(".manifest.json")
    config = HydroConfig(
        sdf_max_resolution=args.sdf_resolution,
        sdf_narrow_band_inner=args.sdf_inner,
        sdf_narrow_band_outer=args.sdf_outer,
        sdf_padding=args.sdf_padding,
        hydroelastic_stiffness=args.stiffness,
        contact_margin=args.contact_margin,
        contact_gap=args.contact_gap,
    )
    try:
        manifest = generate(
            args.source, args.output, manifest_path, config,
            force=args.force, validate=not args.no_validate,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    validation = manifest.get("validation", {})
    print(json.dumps({
        "wrapper": manifest["wrapper"],
        "manifest": str(manifest_path.resolve()),
        "hand_bodies": manifest["hand_body_count"],
        "disabled_source_shapes": manifest["disabled_source_shape_count"],
        "hydro_meshes": manifest["hydro_mesh_count"],
        "adjacent_filters": manifest["adjacent_filter_count"],
        "validation": validation,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
