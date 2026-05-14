"""
setup_helpers.py
----------------
Functions automatically available inside every task's pre-session-setup
block, prepended at runtime by _apply_pre_session_setup.

The motivation: writing identical boilerplate (URDF import with fallback,
placeholder articulation, ground plane, default light) in 100+ task
setups is unmaintainable. Helpers here own the tricky parts (robust URDF
import that survives Kit-version drift) and let task setups stay focused
on what's specific to that task.

Usage in a task .md's pre-session-setup block:

    ## Pre-session setup

    ```python
    setup_world(physics=True, light=True)
    import_urdf_safe(
        "/home/anton/.../franka_panda.urdf",
        dest_path="/World/Robot",
    )
    ```

The setup_helpers module is prepended automatically — task code does NOT
need to import these names. They are available as globals.
"""
from __future__ import annotations


def setup_world(physics: bool = True, light: bool = True, ground: bool = False) -> None:
    """Common scaffolding: /World, optionally PhysicsScene, DomeLight, ground.

    Idempotent — re-running on an existing stage doesn't blow up.
    """
    import omni.usd
    from pxr import UsdGeom, UsdPhysics, UsdLux, Gf, Sdf

    stage = omni.usd.get_context().get_stage()
    if not stage.GetPrimAtPath("/World").IsValid():
        UsdGeom.Xform.Define(stage, "/World")
    if physics and not stage.GetPrimAtPath("/World/PhysicsScene").IsValid():
        UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    if light and not stage.GetPrimAtPath("/World/DomeLight").IsValid():
        dl = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
        # USD schema default for inputs:intensity is 1.0 — effectively
        # black in the viewport. Set to 1000 (Isaac Sim's typical default
        # and what create_prim's light path uses) so the scene actually
        # lights up. Caught when VR-18's stage rendered black despite
        # light=True being passed.
        dl.GetIntensityAttr().Set(1000.0)
    if ground:
        gpath = "/World/Ground"
        if not stage.GetPrimAtPath(gpath).IsValid():
            g = UsdGeom.Cube.Define(stage, gpath).GetPrim()
            UsdGeom.Xformable(g).AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.05))
            UsdGeom.Xformable(g).AddScaleOp().Set(Gf.Vec3d(2, 2, 0.05))
            UsdPhysics.CollisionAPI.Apply(g)


def _placeholder_articulation(stage, dest_path: str) -> str:
    """Manually-authored 3-DOF articulation with RevoluteJoints + DriveAPI.

    Adds an end_effector Xform child so motion / sensor / IK tasks have
    a frame to target. Joint names follow Franka convention so
    set_joint_targets({'panda_joint1': X}) works on the placeholder.

    If `dest_path` already exists with content, this clears it and re-builds —
    avoids stale-state bleed across canary tasks.
    """
    from pxr import UsdGeom, UsdPhysics, Gf

    # Wipe any existing prim at dest to keep the build deterministic
    existing = stage.GetPrimAtPath(dest_path)
    if existing.IsValid():
        stage.RemovePrim(dest_path)

    robot = UsdGeom.Xform.Define(stage, dest_path).GetPrim()
    UsdPhysics.ArticulationRootAPI.Apply(robot)
    UsdPhysics.RigidBodyAPI.Apply(robot)
    UsdPhysics.MassAPI.Apply(robot).CreateMassAttr(15.0)

    base = UsdGeom.Cube.Define(stage, f"{dest_path}/base").GetPrim()
    UsdGeom.Xformable(base).AddScaleOp().Set(Gf.Vec3d(0.1, 0.1, 0.05))
    UsdPhysics.RigidBodyAPI.Apply(base)
    UsdPhysics.CollisionAPI.Apply(base)

    for i in range(1, 4):
        link = UsdGeom.Cube.Define(stage, f"{dest_path}/link{i}").GetPrim()
        UsdGeom.Xformable(link).AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.1 * i))
        UsdGeom.Xformable(link).AddScaleOp().Set(Gf.Vec3d(0.05, 0.05, 0.05))
        UsdPhysics.RigidBodyAPI.Apply(link)
        UsdPhysics.CollisionAPI.Apply(link)
        joint = UsdPhysics.RevoluteJoint.Define(stage, f"{dest_path}/panda_joint{i}")
        parent = "base" if i == 1 else f"link{i-1}"
        joint.CreateBody0Rel().SetTargets([f"{dest_path}/{parent}"])
        joint.CreateBody1Rel().SetTargets([f"{dest_path}/link{i}"])
        joint.CreateAxisAttr("Z")
        joint.CreateLowerLimitAttr(-2.8973)
        joint.CreateUpperLimitAttr(2.8973)
        drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular")
        drive.CreateTypeAttr("force")
        drive.CreateMaxForceAttr(50.0)
        drive.CreateDampingAttr(2.0)
        drive.CreateStiffnessAttr(100.0)

    ee = UsdGeom.Xform.Define(stage, f"{dest_path}/end_effector").GetPrim()
    UsdGeom.Xformable(ee).AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.4))
    return dest_path


def _articulation_has_joints(stage, dest_path: str) -> bool:
    """Walk descendants of dest_path and return True if any RevoluteJoint /
    PrismaticJoint exists. Used to validate that an attempted URDF import
    actually produced a useful articulation (Path-1 / Path-2 may return
    success while leaving the dest_path empty)."""
    from pxr import UsdPhysics
    p = stage.GetPrimAtPath(dest_path)
    if not p.IsValid():
        return False
    for child in p.GetAllDescendants() if hasattr(p, "GetAllDescendants") else []:
        if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
            return True
    # GetAllDescendants is not on every USD version — fall back to manual walk
    def _walk(n):
        for c in n.GetAllChildren():
            if c.IsA(UsdPhysics.RevoluteJoint) or c.IsA(UsdPhysics.PrismaticJoint):
                return True
            if _walk(c):
                return True
        return False
    return _walk(p)


_ROBOT_NAME_TO_URDF = {
    "franka": "/home/anton/robots/franka_panda/franka_panda.urdf",
    "panda": "/home/anton/robots/franka_panda/franka_panda.urdf",
    "ur10e": "/home/anton/robots/ur10e/ur10e.urdf",
}


def import_urdf_safe(
    urdf_path: str = "",
    dest_path: str = "/World/Robot",
    require_joints: bool = True,
    *,
    robot_name: str = "",
    prim_path: str = "",
) -> dict:
    """Robust URDF import. Tries modern + legacy paths, falls back to a
    manually-authored placeholder if all imports fail.

    Returns a dict describing how the import resolved:
        {"prim_path": <where articulation lives>,
         "method": one of 'kit_command', 'isaacsim_api', 'placeholder', 'noop',
         "joints_found": <bool — True if the articulation has revolute/prismatic joints>,
         "warnings": [str, ...]}

    `require_joints=True` (default) means we treat an "import succeeded but
    articulation has no joints" outcome as a failure and fall back to the
    placeholder. Set False if you don't care (just need a prim there).

    Convenience aliases: pass `robot_name="franka"` instead of a full
    urdf_path, and/or `prim_path="/World/Foo"` instead of `dest_path=`.
    """
    import os
    import omni.usd

    warnings: list[str] = []
    if robot_name and not urdf_path:
        urdf_path = _ROBOT_NAME_TO_URDF.get(robot_name.lower(), "")
        if not urdf_path:
            warnings.append(f"unknown robot_name {robot_name!r}; falling back to placeholder")
    if prim_path:
        dest_path = prim_path
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        # No stage — pre-session-setup contract violated. Be loud.
        raise RuntimeError("import_urdf_safe: no active USD stage; "
                           "open or new-stage Kit before calling")

    file_exists = os.path.exists(urdf_path)
    if not file_exists:
        warnings.append(f"URDF not found at {urdf_path!r}; using placeholder")

    # Path-1: Kit command (works on most 5.x builds). Ignored if file missing.
    if file_exists:
        try:
            import omni.kit.commands
            result, prim_path = omni.kit.commands.execute(
                "URDFParseAndImportFile",
                urdf_path=urdf_path,
                dest_path=dest_path,
            )
            if result and prim_path:
                p = stage.GetPrimAtPath(prim_path)
                if p.IsValid():
                    if not require_joints or _articulation_has_joints(stage, prim_path):
                        print(f"[setup_helpers] kit_command imported {urdf_path} → {prim_path}")
                        return {
                            "prim_path": prim_path, "method": "kit_command",
                            "joints_found": _articulation_has_joints(stage, prim_path),
                            "warnings": warnings,
                        }
                    warnings.append("kit_command returned success but articulation has no joints")
            else:
                warnings.append(
                    f"kit_command URDFParseAndImportFile returned "
                    f"(result={result!r}, prim_path={prim_path!r})"
                )
        except Exception as e:
            warnings.append(f"kit_command exception: {type(e).__name__}: {e}")

        # Path-2: direct isaacsim.asset.importer.urdf API
        try:
            import isaacsim.asset.importer.urdf as urdf_mod
            cfg = urdf_mod.ImportConfig() if hasattr(urdf_mod, "ImportConfig") else None
            if hasattr(urdf_mod, "import_urdf") and cfg is not None:
                _ = urdf_mod.import_urdf(urdf_path, cfg, dest_path)
                p = stage.GetPrimAtPath(dest_path)
                if p.IsValid() and (not require_joints or _articulation_has_joints(stage, dest_path)):
                    print(f"[setup_helpers] isaacsim_api imported {urdf_path} → {dest_path}")
                    return {
                        "prim_path": dest_path, "method": "isaacsim_api",
                        "joints_found": _articulation_has_joints(stage, dest_path),
                        "warnings": warnings,
                    }
            else:
                warnings.append("isaacsim.asset.importer.urdf has no import_urdf()/ImportConfig()")
        except ImportError:
            warnings.append("isaacsim.asset.importer.urdf not importable in this Kit build")
        except Exception as e:
            warnings.append(f"isaacsim_api exception: {type(e).__name__}: {e}")

    # Path-3 fallback: manual placeholder articulation
    out = _placeholder_articulation(stage, dest_path)
    print(f"[setup_helpers] placeholder articulation at {out} "
          f"(after {len(warnings)} warnings)")
    return {
        "prim_path": out, "method": "placeholder",
        "joints_found": True,  # placeholder always authors joints
        "warnings": warnings,
    }
