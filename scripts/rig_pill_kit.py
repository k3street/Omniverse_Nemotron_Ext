#!/usr/bin/env python3
"""The pill kit proved in PhysX before a robot uses it (make_pill_kit.py):

  drop     ten tablets dropped into the open vial standing on a floor: every
           one ends inside it (in its cavity, on its floor, under its rim) -
           none through a wall or the floor (small_parts_contact)
  spring   the tweezers held at their root, gravity off: the tips rest at their
           gap; a force pressed on one tip closes it as the arm's stiffness
           says (k_tip, the drive's per-degree units carrying k_theta) -
           tweezer_flex
  cap      the cap seated on the vial stays seated (no explosion, no drift)

Writes workspace/asset_library/pill_kit_rig.json; prints RIG PASS|FAIL.
(Isaac's python.sh; one Kit at a time - scripts/isaac_slot.sh.)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LIB = REPO / "workspace" / "asset_library"
sys.path.insert(0, str(HERE))

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})
import numpy as np  # noqa: E402
import omni.physx  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Sdf, UsdGeom, UsdPhysics  # noqa: E402

HZ = 480
VIAL = json.loads((LIB / "pill_vial" / "pill_vial.json").read_text())
TW = json.loads((LIB / "tweezers_115" / "tweezers_115.json").read_text())


def new_stage():
    ctx = omni.usd.get_context()
    ctx.new_stage()
    for _ in range(5):
        app.update()
    st = ctx.get_stage()
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    scene = UsdPhysics.Scene.Define(st, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)
    p = scene.GetPrim()
    p.AddAppliedSchema("PhysxSceneAPI")
    p.CreateAttribute("physxScene:enableGPUDynamics", Sdf.ValueTypeNames.Bool).Set(False)
    p.CreateAttribute("physxScene:broadphaseType", Sdf.ValueTypeNames.Token).Set("MBP")
    p.CreateAttribute("physxScene:timeStepsPerSecond", Sdf.ValueTypeNames.UInt).Set(HZ)
    floor = UsdGeom.Cube.Define(st, "/World/Floor")
    floor.CreateSizeAttr(1.0)
    floor.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, -0.01))
    floor.AddScaleOp().Set(Gf.Vec3f(1.0, 1.0, 0.02))
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
    return st


def ref(st, path, usd, at=(0.0, 0.0, 0.0)):
    x = UsdGeom.Xform.Define(st, path)
    x.GetPrim().GetReferences().AddReference(str(usd))
    x.AddTranslateOp().Set(Gf.Vec3d(*at))
    return x


def run(seconds, step=None):
    px = omni.physx.get_physx_interface()
    omni.timeline.get_timeline_interface().play()
    for _ in range(3):
        app.update()
    t = 0.0
    for i in range(int(seconds * HZ)):
        if step:
            step(i)
        px.update_simulation(1.0 / HZ, t)
        t += 1.0 / HZ
    px.update_transformations(False, True, False, False)
    return px


def drop_test() -> dict:
    st = new_stage()
    ref(st, "/World/Vial", LIB / "pill_vial" / "pill_vial.usda")
    st.GetPrimAtPath("/World/Vial/PillVial/Cap").SetActive(False)          # open: the cap off
    n = 10
    for k in range(n):
        a = 2 * math.pi * k / 5
        r = 0.006 if k % 2 else 0.0
        ref(st, f"/World/T{k}", LIB / "tablet" / "tablet.usda", (r * math.cos(a), r * math.sin(a), VIAL["height_m"] + 0.02 + 0.012 * k))
    run(2.5)
    px = omni.physx.get_physx_interface()
    inside = []
    r_in = VIAL["inner_d_m"] / 2
    for k in range(n):
        p = np.array(px.get_rigidbody_transformation(f"/World/T{k}/Tablet")["position"], float)
        ok = np.hypot(p[0], p[1]) < r_in and VIAL["floor_z_m"] - 0.001 < p[2] < VIAL["opening_z_m"]
        inside.append({"tablet": k, "p": np.round(p, 4).tolist(), "inside": bool(ok)})
    omni.timeline.get_timeline_interface().stop()
    return {"tablets": n, "inside": sum(x["inside"] for x in inside), "each": inside, "pass": all(x["inside"] for x in inside)}


def spring_test() -> dict:
    import omni.physics.tensors as tensors
    st = new_stage()
    ref(st, "/World/Tw", LIB / "tweezers_115" / "tweezers_115.usda", (0.0, 0.0, 0.05))
    root = "/World/Tw/Tweezers"
    fj = UsdPhysics.FixedJoint.Define(st, "/World/TwFix")
    fj.CreateBody1Rel().SetTargets([Sdf.Path(f"{root}/Root")])
    fj.CreateLocalPos0Attr(Gf.Vec3f(0.0, 0.0, 0.05))
    for arm in ("UpperArm", "LowerArm", "Root"):
        q = st.GetPrimAtPath(f"{root}/{arm}")
        q.AddAppliedSchema("PhysxRigidBodyAPI")
        q.CreateAttribute("physxRigidBody:disableGravity", Sdf.ValueTypeNames.Bool).Set(True)
    spr = TW["spring"]
    L = spr["flex_length_m"]
    F = 1.0                                                                  # N, pressed on the upper tip toward the midline

    def tip(arm):
        px = omni.physx.get_physx_interface()
        tr = px.get_rigidbody_transformation(f"{root}/{arm}")
        p = np.array(tr["position"], float)
        q = tr["rotation"]
        from scipy.spatial.transform import Rotation as R
        return p + R.from_quat([q[0], q[1], q[2], q[3]]).apply([L, 0.0, 0.0])

    run(0.5)
    # the gap between the tips' inner faces: the arms' midlines less one thickness
    rest_gap = float(np.linalg.norm(tip("UpperArm") - tip("LowerArm"))) - 0.0010
    sv = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
    av = sv.create_articulation_view(root)
    links = list(av.shared_metatype.link_names)
    k_up = links.index("UpperArm")
    idx = np.arange(1, dtype=np.int32)

    def push(_i):
        f = np.zeros((1, len(links), 3), np.float32)
        p = np.zeros((1, len(links), 3), np.float32)
        t = tip("UpperArm")
        f[0, k_up] = [0.0, 0.0, -F]
        p[0, k_up] = t
        av.apply_forces_and_torques_at_position(f, None, p, idx, True)
    up0 = tip("UpperArm")
    run(1.0, push)
    up1 = tip("UpperArm")
    defl = float(up0[2] - up1[2])
    want = F / spr["k_tip_n_per_m"]
    omni.timeline.get_timeline_interface().stop()
    return {"rest_gap_mm": round(rest_gap * 1000, 2), "want_gap_mm": round(TW["tip_gap_m"] * 1000, 2),
            "force_n": F, "tip_deflection_mm": round(defl * 1000, 3), "beam_says_mm": round(want * 1000, 3),
            "pass": bool(abs(rest_gap - TW["tip_gap_m"]) < 0.0015 and abs(defl - want) < 0.1 * want)}


def cap_test() -> dict:
    st = new_stage()
    ref(st, "/World/Vial", LIB / "pill_vial" / "pill_vial.usda")
    run(1.5)
    px = omni.physx.get_physx_interface()
    cap = np.array(px.get_rigidbody_transformation("/World/Vial/PillVial/Cap")["position"], float)
    body = np.array(px.get_rigidbody_transformation("/World/Vial/PillVial/Body")["position"], float)
    seated_z = VIAL["height_m"] - VIAL["cap_h_m"] + 0.002
    omni.timeline.get_timeline_interface().stop()
    return {"cap_z_mm": round((cap[2] - body[2]) * 1000, 2), "seated_z_mm": round(seated_z * 1000, 2),
            "cap_xy_off_mm": round(float(np.hypot(*(cap - body)[:2])) * 1000, 2),
            "pass": bool(abs((cap[2] - body[2]) - seated_z) < 0.003 and np.hypot(*(cap - body)[:2]) < 0.002)}


res = {"drop": drop_test(), "spring": spring_test(), "cap": cap_test()}
res["pass"] = all(v["pass"] for v in res.values())
(LIB / "pill_kit_rig.json").write_text(json.dumps(res, indent=1))
print(f"RIG {'PASS' if res['pass'] else 'FAIL'}: drop {res['drop']['inside']}/{res['drop']['tablets']} inside; "
      f"tweezers rest gap {res['spring']['rest_gap_mm']} mm (want {res['spring']['want_gap_mm']}), {res['spring']['force_n']} N closed a tip "
      f"{res['spring']['tip_deflection_mm']} mm (beam {res['spring']['beam_says_mm']}); cap at {res['cap']['cap_z_mm']} mm "
      f"(seated {res['cap']['seated_z_mm']}), {res['cap']['cap_xy_off_mm']} mm off axis", flush=True)
app.close()
