"""
Reusable diagnostic scripts for Isaac Sim scenes via Kit RPC (port 8001).

Each DIAG_* dict has:
  - "name": short identifier
  - "description": what it checks
  - "code": Python code string to send to /exec_sync

Usage from service or CLI:
    import requests, json
    from scripts.diagnostics.kit_diagnostics import DIAGNOSTICS

    for diag in DIAGNOSTICS:
        resp = requests.post("http://localhost:8001/exec_sync",
                             json={"code": diag["code"]}, timeout=60)
        print(f"=== {diag['name']} ===")
        print(resp.json().get("output", resp.text))
"""

# ── 1. Physics Materials ─────────────────────────────────────────────────────

DIAG_PHYSICS_MATERIALS = {
    "name": "physics_materials",
    "description": "List all physics materials with friction values and combine modes",
    "code": """
import omni.usd
from pxr import UsdPhysics, PhysxSchema
stage = omni.usd.get_context().get_stage()
out = []
for p in stage.Traverse():
    if p.HasAPI(UsdPhysics.MaterialAPI):
        m = UsdPhysics.MaterialAPI(p)
        sf = m.GetStaticFrictionAttr().Get()
        df = m.GetDynamicFrictionAttr().Get()
        rest = m.GetRestitutionAttr().Get()
        line = f"{p.GetPath()}: staticF={sf} dynamicF={df} restitution={rest}"
        if p.HasAPI(PhysxSchema.PhysxMaterialAPI):
            px = PhysxSchema.PhysxMaterialAPI(p)
            line += f" frictionCombine={px.GetFrictionCombineModeAttr().Get()}"
        out.append(line)
if not out:
    out.append("WARNING: No physics materials found in scene")
print(chr(10).join(out))
""".strip(),
}

# ── 2. Wheel / Drive Joint Inspector ─────────────────────────────────────────

DIAG_WHEEL_JOINTS = {
    "name": "wheel_joints",
    "description": "Inspect all revolute joints with drive properties (velocity, damping, stiffness, friction)",
    "code": """
import omni.usd
from pxr import UsdPhysics, PhysxSchema
stage = omni.usd.get_context().get_stage()
out = []
for p in stage.Traverse():
    if not p.IsA(UsdPhysics.RevoluteJoint):
        continue
    line = f"{p.GetPath()}"
    props = {}
    for attr in p.GetAttributes():
        n = attr.GetName()
        v = attr.Get()
        if v is not None and any(k in n for k in ["damp", "stiff", "limit", "friction", "drive", "max", "velocity", "target"]):
            props[n] = v
    for k in sorted(props):
        line += f"  {k}={props[k]}"
    out.append(line)
if not out:
    out.append("No revolute joints found")
print(chr(10).join(out))
""".strip(),
}

# ── 3. Collision Shape Material Bindings ──────────────────────────────────────

DIAG_COLLISION_BINDINGS = {
    "name": "collision_material_bindings",
    "description": "Check which physics material is bound to each collision shape",
    "code": """
import omni.usd
from pxr import UsdPhysics, UsdShade, Usd
stage = omni.usd.get_context().get_stage()
out = []
for p in stage.Traverse():
    if not p.HasAPI(UsdPhysics.CollisionAPI):
        continue
    bind = UsdShade.MaterialBindingAPI(p)
    pmat = None
    if bind:
        m, _ = bind.ComputeBoundMaterial(UsdShade.Tokens.physics)
        pmat = m.GetPath() if m else None
    out.append(f"{p.GetPath()} [{p.GetTypeName()}] physMat={pmat}")
if not out:
    out.append("No collision shapes found")
print(chr(10).join(out))
""".strip(),
}

# ── 4. Ground Plane / Floor Check ─────────────────────────────────────────────

DIAG_GROUND_PLANE = {
    "name": "ground_plane",
    "description": "Find ground/floor/plane prims and check collision + material setup",
    "code": """
import omni.usd
from pxr import UsdPhysics, UsdShade
stage = omni.usd.get_context().get_stage()
out = []
for p in stage.Traverse():
    nm = p.GetName().lower()
    if not any(k in nm for k in ["ground", "floor", "plane"]):
        continue
    has_coll = p.HasAPI(UsdPhysics.CollisionAPI)
    out.append(f"{p.GetPath()} [{p.GetTypeName()}] collision={has_coll}")
    if has_coll:
        bind = UsdShade.MaterialBindingAPI(p)
        if bind:
            m, _ = bind.ComputeBoundMaterial(UsdShade.Tokens.physics)
            out.append(f"  physMat={m.GetPath() if m else 'NONE (using default friction!)'}")
if not out:
    out.append("WARNING: No ground/floor/plane prim found")
print(chr(10).join(out))
""".strip(),
}

# ── 5. Physics Scene Settings ─────────────────────────────────────────────────

DIAG_PHYSICS_SCENE = {
    "name": "physics_scene",
    "description": "Dump PhysicsScene settings (gravity, solver, timestep, GPU config)",
    "code": """
import omni.usd
from pxr import UsdPhysics
stage = omni.usd.get_context().get_stage()
out = []
for p in stage.Traverse():
    if not p.IsA(UsdPhysics.Scene):
        continue
    out.append(f"PhysicsScene: {p.GetPath()}")
    for attr in sorted(p.GetAttributes(), key=lambda a: a.GetName()):
        v = attr.Get()
        if v is not None:
            out.append(f"  {attr.GetName()} = {v}")
if not out:
    out.append("WARNING: No PhysicsScene found")
print(chr(10).join(out))
""".strip(),
}

# ── 6. Rigid Body Mass Properties ─────────────────────────────────────────────

DIAG_MASS_PROPERTIES = {
    "name": "mass_properties",
    "description": "List all rigid bodies with mass, center of mass, and inertia",
    "code": """
import omni.usd
from pxr import UsdPhysics
stage = omni.usd.get_context().get_stage()
out = []
for p in stage.Traverse():
    if not p.HasAPI(UsdPhysics.RigidBodyAPI):
        continue
    mass = None
    com = None
    if p.HasAPI(UsdPhysics.MassAPI):
        ma = UsdPhysics.MassAPI(p)
        mass = ma.GetMassAttr().Get()
        com = ma.GetCenterOfMassAttr().Get()
    out.append(f"{p.GetPath()}: mass={mass} com={com}")
if not out:
    out.append("No rigid bodies found")
print(chr(10).join(out))
""".strip(),
}

# ── 7. Caster Wheel Impedance Check ──────────────────────────────────────────

DIAG_CASTER_IMPEDANCE = {
    "name": "caster_impedance",
    "description": "Check if caster swivel joints have stiffness/damping that impedes free rotation",
    "code": """
import omni.usd
from pxr import UsdPhysics
stage = omni.usd.get_context().get_stage()
out = []
issues = []
for p in stage.Traverse():
    if not p.IsA(UsdPhysics.RevoluteJoint):
        continue
    name = p.GetName().lower()
    if "caster" not in name and "swivel" not in name:
        continue
    stiff = p.GetAttribute("drive:angular:physics:stiffness").Get()
    damp = p.GetAttribute("drive:angular:physics:damping").Get()
    tgt_pos = p.GetAttribute("drive:angular:physics:targetPosition").Get()
    tgt_vel = p.GetAttribute("drive:angular:physics:targetVelocity").Get()
    out.append(f"{p.GetPath()}: stiffness={stiff} damping={damp} targetPos={tgt_pos} targetVel={tgt_vel}")
    if stiff is not None and stiff > 0:
        issues.append(f"  ISSUE: {p.GetPath()} has stiffness={stiff} — caster swivel should be free (stiffness=0)")
    if damp is not None and damp > 5:
        issues.append(f"  WARN: {p.GetPath()} has damping={damp} — high damping on caster resists motion")
if issues:
    out.extend(issues)
elif out:
    out.append("OK: No caster impedance issues detected")
else:
    out.append("No caster/swivel joints found")
print(chr(10).join(out))
""".strip(),
}

# ── 8. Articulation Root Check ────────────────────────────────────────────────

DIAG_ARTICULATION = {
    "name": "articulation_root",
    "description": "Find ArticulationRootAPI prims and check if fixed base or floating",
    "code": """
import omni.usd
from pxr import UsdPhysics, PhysxSchema
stage = omni.usd.get_context().get_stage()
out = []
for p in stage.Traverse():
    if not p.HasAPI(UsdPhysics.ArticulationRootAPI):
        continue
    has_fixed = False
    for child in stage.Traverse():
        if child.IsA(UsdPhysics.FixedJoint):
            b0 = child.GetRelationship("physics:body0").GetTargets()
            b1 = child.GetRelationship("physics:body1").GetTargets()
            if p.GetPath() in b0 or p.GetPath() in b1:
                has_fixed = True
    out.append(f"{p.GetPath()} [{p.GetTypeName()}] fixedBase={has_fixed}")
    if p.HasAPI(PhysxSchema.PhysxArticulationAPI):
        art = PhysxSchema.PhysxArticulationAPI(p)
        out.append(f"  enabledSelfCollisions={art.GetEnabledSelfCollisionsAttr().Get()}")
        out.append(f"  solverPositionIterationCount={art.GetSolverPositionIterationCountAttr().Get()}")
        out.append(f"  solverVelocityIterationCount={art.GetSolverVelocityIterationCountAttr().Get()}")
if not out:
    out.append("No ArticulationRoot found")
print(chr(10).join(out))
""".strip(),
}

# ── 9. Contact / Overlap Report ───────────────────────────────────────────────

DIAG_CONTACT_REPORT = {
    "name": "contact_report",
    "description": "Check for contact report API and active contacts on key bodies",
    "code": """
import omni.usd
from pxr import PhysxSchema
stage = omni.usd.get_context().get_stage()
out = []
for p in stage.Traverse():
    if p.HasAPI(PhysxSchema.PhysxContactReportAPI):
        cr = PhysxSchema.PhysxContactReportAPI(p)
        thresh = cr.GetThresholdAttr().Get() if cr.GetThresholdAttr() else None
        out.append(f"{p.GetPath()}: contactReport threshold={thresh}")
if not out:
    out.append("No PhysxContactReportAPI found on any prim (add one to debug contacts)")
print(chr(10).join(out))
""".strip(),
}

# ── 10. Scene Overview ────────────────────────────────────────────────────────

DIAG_SCENE_OVERVIEW = {
    "name": "scene_overview",
    "description": "Quick scene summary: file, prim count, rigid bodies, joints, colliders",
    "code": """
import omni.usd
from pxr import UsdPhysics, UsdGeom
stage = omni.usd.get_context().get_stage()
scene_file = stage.GetRootLayer().identifier
n_prims = 0
n_rigid = 0
n_joints = 0
n_colliders = 0
n_meshes = 0
root_xforms = []
for p in stage.Traverse():
    n_prims += 1
    if p.HasAPI(UsdPhysics.RigidBodyAPI): n_rigid += 1
    if p.HasAPI(UsdPhysics.CollisionAPI): n_colliders += 1
    if p.IsA(UsdPhysics.Joint): n_joints += 1
    if p.IsA(UsdGeom.Mesh): n_meshes += 1
for c in stage.GetPseudoRoot().GetChildren():
    root_xforms.append(str(c.GetPath()))
print(f"Scene: {scene_file}")
print(f"Root prims: {', '.join(root_xforms)}")
print(f"Total prims: {n_prims}")
print(f"Rigid bodies: {n_rigid}")
print(f"Collision shapes: {n_colliders}")
print(f"Joints: {n_joints}")
print(f"Meshes: {n_meshes}")
""".strip(),
}

# ── 11. Wheeled Robot Motion Diagnosis ────────────────────────────────────────

DIAG_WHEELED_ROBOT_MOTION = {
    "name": "wheeled_robot_motion",
    "description": "All-in-one diagnosis for slow/stuck wheeled robots: friction, caster impedance, drive config, ground material, gravity",
    "code": """
import omni.usd
from pxr import UsdPhysics, PhysxSchema, UsdShade, Usd
stage = omni.usd.get_context().get_stage()
out = []
issues = []

# 1. Physics materials
out.append("=== PHYSICS MATERIALS ===")
mats_found = False
for p in stage.Traverse():
    if p.HasAPI(UsdPhysics.MaterialAPI):
        mats_found = True
        m = UsdPhysics.MaterialAPI(p)
        sf = m.GetStaticFrictionAttr().Get()
        df = m.GetDynamicFrictionAttr().Get()
        out.append(f"  {p.GetPath()}: sF={sf} dF={df}")
        if sf is not None and sf < 0.3:
            issues.append(f"LOW_FRICTION: {p.GetPath()} staticFriction={sf}")
if not mats_found:
    issues.append("NO_PHYSICS_MATERIALS: Scene has no physics materials — all contacts use default friction")

# 2. Drive joints
out.append("\\n=== DRIVE JOINTS ===")
for p in stage.Traverse():
    if not p.IsA(UsdPhysics.RevoluteJoint):
        continue
    stiff = p.GetAttribute("drive:angular:physics:stiffness").Get()
    damp = p.GetAttribute("drive:angular:physics:damping").Get()
    tv = p.GetAttribute("drive:angular:physics:targetVelocity").Get()
    tp = p.GetAttribute("drive:angular:physics:targetPosition").Get()
    vel = p.GetAttribute("state:angular:physics:velocity").Get()
    if stiff is None and damp is None:
        out.append(f"  {p.GetPath()}: no drive (free joint) vel={vel}")
    else:
        out.append(f"  {p.GetPath()}: stiff={stiff} damp={damp} tgtVel={tv} tgtPos={tp} vel={vel}")
    name = p.GetName().lower()
    if ("caster" in name or "swivel" in name) and stiff is not None and stiff > 0:
        issues.append(f"CASTER_STIFF: {p.GetPath()} has stiffness={stiff} — blocks caster rotation")

# 3. Ground material
out.append("\\n=== GROUND SURFACE ===")
for p in stage.Traverse():
    nm = p.GetName().lower()
    if any(k in nm for k in ["ground", "floor", "plane"]) and p.HasAPI(UsdPhysics.CollisionAPI):
        bind = UsdShade.MaterialBindingAPI(p)
        pmat = None
        if bind:
            m, _ = bind.ComputeBoundMaterial(UsdShade.Tokens.physics)
            pmat = m.GetPath() if m else None
        out.append(f"  {p.GetPath()} physMat={pmat}")
        if pmat is None:
            issues.append(f"NO_GROUND_MATERIAL: {p.GetPath()} has no physics material — default low friction")

# 4. Gravity
ps = None
for p in stage.Traverse():
    if p.IsA(UsdPhysics.Scene):
        ps = p
        break
if ps:
    gd = ps.GetAttribute("physics:gravityDirection").Get()
    gm = ps.GetAttribute("physics:gravityMagnitude").Get()
    out.append(f"\\n=== GRAVITY === dir={gd} mag={gm}")
    if gd == (0,0,0) or gm == 0:
        issues.append(f"ZERO_GRAVITY: gravityDir={gd} gravityMag={gm}")

# Summary
out.append("\\n=== ISSUES ===")
if issues:
    for i in issues:
        out.append(f"  !! {i}")
else:
    out.append("  No issues detected")
print(chr(10).join(out))
""".strip(),
}


# ── Master list ───────────────────────────────────────────────────────────────

DIAGNOSTICS = [
    DIAG_SCENE_OVERVIEW,
    DIAG_PHYSICS_MATERIALS,
    DIAG_WHEEL_JOINTS,
    DIAG_CASTER_IMPEDANCE,
    DIAG_COLLISION_BINDINGS,
    DIAG_GROUND_PLANE,
    DIAG_PHYSICS_SCENE,
    DIAG_MASS_PROPERTIES,
    DIAG_ARTICULATION,
    DIAG_CONTACT_REPORT,
    DIAG_WHEELED_ROBOT_MOTION,
]


if __name__ == "__main__":
    """Run all diagnostics against a live Kit RPC server."""
    import requests
    import sys
    import json

    kit_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"
    names = sys.argv[2:] if len(sys.argv) > 2 else None

    for diag in DIAGNOSTICS:
        if names and diag["name"] not in names:
            continue
        print(f"\n{'='*60}")
        print(f"  {diag['name']}: {diag['description']}")
        print(f"{'='*60}")
        try:
            resp = requests.post(
                f"{kit_url}/exec_sync",
                json={"code": diag["code"]},
                timeout=60,
            )
            data = resp.json()
            print(data.get("output", json.dumps(data, indent=2)))
        except Exception as e:
            print(f"ERROR: {e}")
