#!/usr/bin/env python3
"""The pill kit proved in PhysX before a robot uses it (make_pill_kit.py):

  drop     ten tablets dropped into the open vial standing on a floor: every
           one ends inside it (in its cavity, on its floor, under its rim) -
           none through a wall or the floor (small_parts_contact)
  spring   the tweezers held at their root, gravity off: the tips rest at their
           gap; a force pressed on the hinged (lower) tip closes the gap as
           both arms' beams in series say (2 F / k_tip: the one hinge carries
           both) - tweezer_flex
  floating the tweezers floating as a hand holds them, squeezed 1 N a side at
           the grip: the angle between the arms closes as both beams say
           (2 F d / k_theta) - two hinges in a floating tree left the first arm
           with no spring
  cap      the cap seated on the vial stays seated (no explosion, no drift)
  thread   the cap's thread run by behaviors.cap_thread, the vial's body held, a
           'hand' turning the cap (a joint driving it at 90 deg/s, its torque
           capped): seated at a closure applied at 0.6 N m, 0.2 N m does not
           turn it and 0.45 N m breaks it loose; it rises a pitch a turn,
           leaves the thread at its end and lifts off; set back on the neck it
           catches the thread, screws down to its seat, and closed at 0.4 N m
           holds against 0.15 and gives at 0.3 (cap_thread)
  torque_sign  the hand's torque on the cap as a take reads it from its
           contacts: a finger drawn anticlockwise past the free cap turns it
           anticlockwise, and the torque read is positive

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
    from build_er_scene import joints_to_switched_off
    for jp in joints_to_switched_off(st.GetPrimAtPath("/World/Vial")):     # and its thread with it (as a scene does)
        st.GetPrimAtPath(jp).SetActive(False)
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
    av = sv.create_articulation_view(f"{root}/Root")
    links = list(av.shared_metatype.link_names)
    k_lo = links.index("LowerArm")
    idx = np.arange(1, dtype=np.int32)

    def push(_i):
        f = np.zeros((1, len(links), 3), np.float32)
        p = np.zeros((1, len(links), 3), np.float32)
        t = tip("LowerArm")
        f[0, k_lo] = [0.0, 0.0, F]                                         # the lower tip pushed toward the upper
        p[0, k_lo] = t
        av.apply_forces_and_torques_at_position(f, None, p, idx, True)
    lo0 = tip("LowerArm")
    run(1.0, push)
    lo1 = tip("LowerArm")
    defl = float(lo1[2] - lo0[2])
    want = 2.0 * F / spr["k_tip_n_per_m"]                                  # both arms' beams in series
    omni.timeline.get_timeline_interface().stop()
    return {"rest_gap_mm": round(rest_gap * 1000, 2), "want_gap_mm": round(TW["tip_gap_m"] * 1000, 2),
            "force_n": F, "tip_deflection_mm": round(defl * 1000, 3), "beam_says_mm": round(want * 1000, 3),
            "pass": bool(abs(rest_gap - TW["tip_gap_m"]) < 0.0015 and abs(defl - want) < 0.1 * want)}


def floating_spring_test() -> dict:
    """The tweezers as a hand holds them: floating (no part fixed), gravity off, squeezed by equal and opposite forces
    on both arms at the grip (55 mm out). Each arm closes as its beam says, the same both sides - a hinge reversed in
    the articulation's tree loses its spring, which the fixed-root spring test cannot see."""
    import omni.physics.tensors as tensors
    st = new_stage()
    ref(st, "/World/Tw", LIB / "tweezers_115" / "tweezers_115.usda", (0.0, 0.0, 0.05))
    root = "/World/Tw/Tweezers"
    for arm in ("UpperArm", "LowerArm", "Root"):
        q = st.GetPrimAtPath(f"{root}/{arm}")
        q.AddAppliedSchema("PhysxRigidBodyAPI")
        q.CreateAttribute("physxRigidBody:disableGravity", Sdf.ValueTypeNames.Bool).Set(True)
    spr = TW["spring"]
    F, at = 1.0, 0.055                                                    # N; m from the hinge (the grip: plan_er_grasp.tool_grip_frame)
    omni.timeline.get_timeline_interface().play()
    for _ in range(3):
        app.update()
    sv = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
    av = sv.create_articulation_view(f"{root}/Root")
    links = list(av.shared_metatype.link_names)
    dofs = list(av.shared_metatype.dof_names)
    idx = np.arange(1, dtype=np.int32)
    px = omni.physx.get_physx_interface()
    from scipy.spatial.transform import Rotation as R

    def squeeze(_i):
        f = np.zeros((1, len(links), 3), np.float32)
        p = np.zeros((1, len(links), 3), np.float32)
        for arm, sgn in (("UpperArm", 1.0), ("LowerArm", -1.0)):
            tr = px.get_rigidbody_transformation(f"{root}/{arm}")
            rot = R.from_quat(tr["rotation"])
            k = links.index(arm)
            p[0, k] = np.asarray(tr["position"], float) + rot.apply([at, 0.0, 0.0])
            f[0, k] = rot.apply([0.0, 0.0, -sgn * F])                     # toward the other arm
        av.apply_forces_and_torques_at_position(f, None, p, idx, True)
    q0 = np.asarray(av.get_dof_positions(), float).reshape(-1).copy()
    t = 0.0
    for i in range(int(1.0 * HZ)):
        squeeze(i)
        px.update_simulation(1.0 / HZ, t)
        t += 1.0 / HZ
    q1 = np.asarray(av.get_dof_positions(), float).reshape(-1)
    omni.timeline.get_timeline_interface().stop()
    beam_deg = 2.0 * math.degrees(F * at / spr["k_theta_n_m_per_rad"])        # the angle between the arms: both beams
    closed = round(sum(abs(math.degrees(float(q1[i] - q0[i]))) for i in range(len(dofs))), 3)
    ok = abs(closed - beam_deg) < 0.1 * beam_deg
    return {"force_n": F, "at_m": round(at, 4), "closed_deg": closed, "beam_deg": round(beam_deg, 3), "dofs": dofs, "pass": bool(ok)}


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


def thread_test() -> dict:
    """The cap's thread worked as a hand would: see the module's doc. The rig's hand is a joint from the world to the
    cap whose twist is driven at a speed, its torque capped; the law is told that cap as the hand's torque (a take
    reads it from the hand's contacts)."""
    import omni.physics.tensors as tensors
    from behaviors import cap_thread
    from scipy.spatial.transform import Rotation as R
    st = new_stage()
    ref(st, "/World/Vial", LIB / "pill_vial" / "pill_vial.usda")
    root = "/World/Vial/PillVial"
    th = VIAL["thread"]
    fj = UsdPhysics.FixedJoint.Define(st, "/World/BodyFix")
    fj.CreateBody1Rel().SetTargets([Sdf.Path(f"{root}/Body")])
    hand = UsdPhysics.Joint.Define(st, "/World/Hand")
    hand.CreateBody1Rel().SetTargets([Sdf.Path(f"{root}/Cap")])
    x_to_z = Gf.Quatf(float(math.cos(-math.pi / 4)), Gf.Vec3f(0.0, float(math.sin(-math.pi / 4)), 0.0))
    hand.CreateLocalPos0Attr(Gf.Vec3f(0.0, 0.0, float(th["seat_z_m"])))
    hand.CreateLocalRot0Attr(x_to_z)
    hand.CreateLocalPos1Attr(Gf.Vec3f(0.0, 0.0, 0.0))
    hand.CreateLocalRot1Attr(x_to_z)
    hd = UsdPhysics.DriveAPI.Apply(hand.GetPrim(), "rotX")
    hd.CreateTypeAttr("force")
    hd.CreateStiffnessAttr(0.0)
    hd.CreateDampingAttr(10.0)
    hd.CreateTargetVelocityAttr(0.0)
    hd.CreateMaxForceAttr(0.0)
    jp = st.GetPrimAtPath(f"{root}/{th['joint']}")
    enabled = jp.GetAttribute("physics:jointEnabled")
    target = jp.GetAttribute(th["height_target_attr"])
    tl = th["twist_lock"]
    k_a, tgt_a = jp.GetAttribute(tl["stiffness_attr"]), jp.GetAttribute(tl["target_attr"])
    lo = th["lift_off"]
    lk_a = jp.GetAttribute(lo["height_stiffness_attr"])
    lc_a = jp.GetAttribute(lo["height_damping_attr"])
    ll_a = [jp.GetAttribute(a) for a in lo["height_limit_attrs"]]
    lift_on = (lk_a.Get(), [a.Get() for a in ll_a], lc_a.Get())
    omni.timeline.get_timeline_interface().play()
    for _ in range(3):
        app.update()
    sv = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
    capv = sv.create_rigid_body_view(f"{root}/Cap")
    px = omni.physx.get_physx_interface()
    law = {}
    kw = dict(turns=th["turns"], retention=th["retention"], run_n_m=th["run_n_m"], pitch_m=th["pitch_m"])
    track = {"u": 0.0, "yaw": None, "t": 0.0, "locked": True, "target": 0.0, "lifting": False}
    log = []

    def lock(on):
        if on != track["locked"]:
            if on:                                                         # held where it is now: its physical twist
                tgt_a.Set(float(math.degrees(track["yaw"] or 0.0)))           # (the law's turn counts from the thread's end once caught)
            k_a.Set(float(tl["n_m_per_deg"]) if on else 0.0)
            track["locked"] = on

    def step(seconds, speed_deg_s, torque_n_m, lift_n=0.0):
        hd.GetTargetVelocityAttr().Set(float(speed_deg_s))
        hd.GetMaxForceAttr().Set(float(torque_n_m))
        for _ in range(int(seconds * HZ)):
            if lift_n:
                capv.apply_forces_and_torques_at_position(np.array([[0.0, 0.0, lift_n]], np.float32), None,
                                                           None, np.arange(1, dtype=np.int32), True)
            px.update_simulation(1.0 / HZ, track["t"])
            track["t"] += 1.0 / HZ
            tr = capv.get_transforms()[0]
            p, rot = np.asarray(tr[:3], float), R.from_quat(tr[3:7])
            yaw = float(rot.as_euler("xyz")[2])
            if track["yaw"] is not None:
                track["u"] += (yaw - track["yaw"] + math.pi) % (2 * math.pi) - math.pi
            track["yaw"] = yaw
            height = float(p[2] - th["seat_z_m"])
            off = float(np.hypot(p[0], p[1]))
            tilt = float(np.arccos(np.clip(rot.apply([0.0, 0.0, 1.0])[2], -1.0, 1.0)))
            r = cap_thread(law, track["u"], math.copysign(torque_n_m, speed_deg_s) if speed_deg_s else 0.0, applied_n_m=0.6,
                           free_pose={"off_axis_m": off, "tilt_rad": tilt, "height_m": height}, **kw)
            if r["event"] == "off_thread":
                # resting on the thread's end, lifting off along the neck: the height drive let go, its floor raised
                lock(False)
                lk_a.Set(0.0)
                lc_a.Set(0.0)
                ll_a[0].Set(float(th["pitch_m"] * th["turns"]))
                ll_a[1].Set(float(th["pitch_m"] * th["turns"] + 0.05))
                track["lifting"] = True
                hand.GetPrim().GetAttribute("physics:jointEnabled").Set(False)
            elif r["event"] == "on_thread":
                # caught at the thread's start: the turn counted from the thread's end, the joint on again
                track["u"] = 2 * math.pi * th["turns"]
                lk_a.Set(float(lift_on[0]))
                lc_a.Set(float(lift_on[2]))
                ll_a[0].Set(float(lift_on[1][0]))
                ll_a[1].Set(float(lift_on[1][1]))
                target.Set(float(th["pitch_m"] * th["turns"]))
                lock(False)
                enabled.Set(True)
            if track["lifting"] and height > th["pitch_m"] * th["turns"] + lo["clear_m"]:
                enabled.Set(False)                                          # clear of the neck: free
                track["lifting"] = False
                log.append({"t": round(track["t"], 3), "event": "clear_of_neck", "u_deg": round(math.degrees(track["u"]), 1),
                            "height_mm": round(height * 1000, 2)})
            if r["on"]:
                lock(r["locked"])
                if not r["locked"]:
                    z = float(th["pitch_m"] * track["u"] / (2 * math.pi))
                    if abs(z - track["target"]) > 1e-6:
                        target.Set(z)
                        track["target"] = z
            if r["event"]:
                log.append({"t": round(track["t"], 3), "event": r["event"], "u_deg": round(math.degrees(track["u"]), 1),
                            "height_mm": round(height * 1000, 2)})
        tr = capv.get_transforms()[0]
        return {"u_deg": round(math.degrees(track["u"]), 2), "height_mm": round(float(tr[2] - th["seat_z_m"]) * 1000, 3)}

    out = {}
    out["held_0.2"] = step(0.5, 90.0, 0.2)
    out["broke_0.45"] = step(0.5, 90.0, 0.45)
    rises = []
    for _ in range(6):
        s_ = step(0.25, 90.0, 0.45)
        if law.get("on", True):
            rises.append((math.radians(s_["u_deg"]), s_["height_mm"] / 1000))
    out["off"] = step(3.0, 90.0, 0.45)
    out["lifted"] = step(0.3, 0.0, 0.0, lift_n=VIAL["cap_mass_kg"] * 9.81 * 1.15)
    lifted_mm = out["lifted"]["height_mm"]
    # set back on the neck: the cap placed 1 mm over the thread's start, a little off its axis, the hand on it again
    q = R.from_euler("z", 0.3).as_quat()
    capv.set_transforms(np.array([[0.0005, 0.0, th["seat_z_m"] + th["pitch_m"] * th["turns"] + 0.001, *q]], np.float32), np.arange(1, dtype=np.int32))
    capv.set_velocities(np.zeros((1, 6), np.float32), np.arange(1, dtype=np.int32))
    track["yaw"] = None
    hand.GetPrim().GetAttribute("physics:jointEnabled").Set(True)
    out["caught"] = step(0.1, 0.0, 0.0)
    out["closed_0.4"] = step(5.5, -90.0, 0.4)
    applied = law.get("applied_n_m")
    out["reheld_0.15"] = step(0.5, 90.0, 0.15)
    out["regave_0.3"] = step(0.5, 90.0, 0.3)
    omni.timeline.get_timeline_interface().stop()
    slope = float(np.polyfit([u for u, _ in rises], [z for _, z in rises], 1)[0]) if len(rises) > 2 else 0.0
    want = th["pitch_m"] / (2 * math.pi)
    seat = math.radians(5.0)
    checks = {"held": math.radians(out["held_0.2"]["u_deg"]) < seat,
              "broke_loose": math.radians(out["broke_0.45"]["u_deg"]) > seat,
              "pitch": abs(slope - want) < 0.05 * want,
              "off_thread": any(e["event"] == "off_thread" for e in log),
              "rests_on_thread_end": abs(out["off"]["height_mm"] - th["pitch_m"] * th["turns"] * 1000) < 0.5,
              "lifted_off": lifted_mm > th["pitch_m"] * th["turns"] * 1000 + 5.0 and any(e["event"] == "clear_of_neck" for e in log),
              "caught": any(e["event"] == "on_thread" for e in log),
              "seated": any(e["event"] == "seated" for e in log) and abs(out["closed_0.4"]["height_mm"]) < 0.5,
              "applied_0.4": applied is not None and abs(applied - 0.4) < 1e-6,
              # seated and held: the hand still closing at 0.4 N m turns it no further than the lock gives (< 2 deg)
              "seat_held": any(e["event"] == "seated" for e in log) and abs(
                  out["closed_0.4"]["u_deg"] - next(e["u_deg"] for e in log if e["event"] == "seated")) < 2.0,
              "reheld": abs(math.radians(out["reheld_0.15"]["u_deg"]) - math.radians(out["closed_0.4"]["u_deg"])) < seat,
              "regave": math.radians(out["regave_0.3"]["u_deg"]) - math.radians(out["reheld_0.15"]["u_deg"]) > seat}
    return {"phases": out, "events": log, "rise_mm_per_turn": round(slope * 2 * math.pi * 1000, 3),
            "pitch_mm": round(th["pitch_m"] * 1000, 3), "checks": checks, "pass": all(checks.values())}


def torque_sign_test() -> dict:
    """The hand's torque on a cap as a take reads it (collision_measure.contact_torque from a contact view's normal
    and friction data): a kinematic finger pressed on the free cap's skirt and drawn past it anticlockwise (seen from
    above) turns the cap anticlockwise - the torque read must be positive, the sense the law takes as opening."""
    import omni.physics.tensors as tensors
    from collision_measure import contact_torque
    from scipy.spatial.transform import Rotation as R
    st = new_stage()
    ref(st, "/World/Vial", LIB / "pill_vial" / "pill_vial.usda")
    root = "/World/Vial/PillVial"
    th = VIAL["thread"]
    fj = UsdPhysics.FixedJoint.Define(st, "/World/BodyFix")
    fj.CreateBody1Rel().SetTargets([Sdf.Path(f"{root}/Body")])
    st.GetPrimAtPath(f"{root}/{th['joint']}").GetAttribute(th["twist_lock"]["stiffness_attr"]).Set(0.0)   # broken loose
    r_cap = VIAL["cap_d_m"] / 2
    z = th["seat_z_m"] + (VIAL["cap_h_m"] - 0.002) / 2
    side = 0.006
    x0 = r_cap + side / 2 - 0.0003                                        # pressed 0.3 mm into the skirt
    cq = st.GetPrimAtPath(f"{root}/Cap")                                  # a contact view's sensor: reported before the start
    cq.AddAppliedSchema("PhysxContactReportAPI")
    cq.CreateAttribute("physxContactReport:threshold", Sdf.ValueTypeNames.Float).Set(0.0)
    fin = UsdGeom.Cube.Define(st, "/World/Finger")
    fin.CreateSizeAttr(side)
    fin.AddTranslateOp().Set(Gf.Vec3d(x0, -0.004, z))
    UsdPhysics.CollisionAPI.Apply(fin.GetPrim())
    UsdPhysics.RigidBodyAPI.Apply(fin.GetPrim()).CreateKinematicEnabledAttr(True)
    omni.timeline.get_timeline_interface().play()
    for _ in range(3):
        app.update()
    sv = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
    fv = sv.create_rigid_body_view("/World/Finger")
    capv = sv.create_rigid_body_view(f"{root}/Cap")
    cv = sv.create_rigid_contact_view(f"{root}/Cap", filter_patterns=["/World/Finger"], max_contact_data_count=64)
    px = omni.physx.get_physx_interface()
    idx = np.arange(1, dtype=np.int32)
    t, u, yaw0, tqs = 0.0, 0.0, None, []
    for i in range(int(0.4 * HZ)):
        y = -0.004 + 0.02 * t                                             # drawn along +y at 2 cm/s: anticlockwise at +x
        fv.set_kinematic_targets(np.asarray([[x0, y, z, 0.0, 0.0, 0.0, 1.0]], np.float32), idx)
        px.update_simulation(1.0 / HZ, t)
        t += 1.0 / HZ
        tr = capv.get_transforms()[0]
        yaw = float(R.from_quat(tr[3:7]).as_euler("xyz")[2])
        if yaw0 is not None:
            u += (yaw - yaw0 + math.pi) % (2 * math.pi) - math.pi
        yaw0 = yaw
        fn, pts, nrm, _sep, cnt, st_ = cv.get_contact_data(1.0 / HZ)
        ff, fpts, fcnt, fst = cv.get_friction_data(1.0 / HZ)
        tq = contact_torque(fn, pts, nrm, cnt, st_, ff, fpts, fcnt, fst, np.asarray(tr[:3], float), [0.0, 0.0, 1.0])
        if abs(tq) > 1e-6:
            tqs.append(tq)
    omni.timeline.get_timeline_interface().stop()
    mean = float(np.mean(tqs)) if tqs else 0.0
    return {"cap_turned_deg": round(math.degrees(u), 2), "torque_mean_n_m": round(mean, 5), "ticks_in_contact": len(tqs),
            "pass": bool(u > math.radians(1.0) and mean > 0.0)}


res = {"drop": drop_test(), "spring": spring_test(), "floating": floating_spring_test(), "cap": cap_test(), "thread": thread_test(),
       "torque_sign": torque_sign_test()}
res["pass"] = all(v["pass"] for v in res.values())
(LIB / "pill_kit_rig.json").write_text(json.dumps(res, indent=1))
print(f"RIG {'PASS' if res['pass'] else 'FAIL'}: drop {res['drop']['inside']}/{res['drop']['tablets']} inside; "
      f"tweezers rest gap {res['spring']['rest_gap_mm']} mm (want {res['spring']['want_gap_mm']}), {res['spring']['force_n']} N closed a tip "
      f"{res['spring']['tip_deflection_mm']} mm (beam {res['spring']['beam_says_mm']}); floating, squeezed {res['floating']['force_n']} N at the grip: "
      f"arms closed {res['floating']['closed_deg']} deg (beam {res['floating']['beam_deg']}); cap at {res['cap']['cap_z_mm']} mm "
      f"(seated {res['cap']['seated_z_mm']}), {res['cap']['cap_xy_off_mm']} mm off axis; thread rises "
      f"{res['thread']['rise_mm_per_turn']} mm a turn (pitch {res['thread']['pitch_mm']}), checks {res['thread']['checks']}; "
      f"a finger drawn anticlockwise turned the cap {res['torque_sign']['cap_turned_deg']} deg at a read torque of "
      f"{res['torque_sign']['torque_mean_n_m']} N m", flush=True)
app.close()
