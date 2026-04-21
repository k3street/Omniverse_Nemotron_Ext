#!/usr/bin/env python3
"""
g1_onnx_runner.py — Pre-trained G1-29dof velocity walking policy via file-based state exchange.

Architecture:
  - ONE Kit RPC call (setup): applies PD gains, pre-positions robot, registers a
    persistent physics callback: state → /tmp/g1_state.json, actions ← /tmp/g1_actions.json
  - ONNX inference runs locally (onnxruntime in system Python).
  - No per-cycle Kit RPC — minimal latency.

Usage:
    python3 scripts/locomotion/g1_onnx_runner.py
    python3 scripts/locomotion/g1_onnx_runner.py --vx 0.3      # walk forward 0.3 m/s
    python3 scripts/locomotion/g1_onnx_runner.py --setup-only   # apply gains, register callback, exit
    python3 scripts/locomotion/g1_onnx_runner.py --no-setup     # callback already registered
"""
import argparse, json, math, os, sys, time, urllib.request
from typing import Optional
import numpy as np

ONNX_PATH   = ("/home/kimate/Downloads/unitree_rl_lab-main/deploy/robots/g1_29dof"
               "/config/policy/velocity/v0/exported/policy.onnx")
KIT_RPC_URL = "http://127.0.0.1:8001/exec_sync"
ROBOT_PRIM  = "/World/UnitreeG1"
STATE_FILE  = "/tmp/g1_state.json"
ACTION_FILE = "/tmp/g1_actions.json"

# Policy joint order = Unitree SDK motor order (decoded from deploy.yaml joint_ids_map)
POLICY_JOINT_NAMES = [
    "left_hip_pitch_joint",       # 0
    "right_hip_pitch_joint",      # 1
    "waist_yaw_joint",            # 2
    "left_hip_roll_joint",        # 3
    "right_hip_roll_joint",       # 4
    "waist_roll_joint",           # 5
    "left_hip_yaw_joint",         # 6
    "right_hip_yaw_joint",        # 7
    "waist_pitch_joint",          # 8
    "left_knee_joint",            # 9
    "right_knee_joint",           # 10
    "left_shoulder_pitch_joint",  # 11
    "right_shoulder_pitch_joint", # 12
    "left_ankle_pitch_joint",     # 13
    "right_ankle_pitch_joint",    # 14
    "left_shoulder_roll_joint",   # 15
    "right_shoulder_roll_joint",  # 16
    "left_ankle_roll_joint",      # 17
    "right_ankle_roll_joint",     # 18
    "left_shoulder_yaw_joint",    # 19
    "right_shoulder_yaw_joint",   # 20
    "left_elbow_joint",           # 21
    "right_elbow_joint",          # 22
    "left_wrist_roll_joint",      # 23
    "right_wrist_roll_joint",     # 24
    "left_wrist_pitch_joint",     # 25
    "right_wrist_pitch_joint",    # 26
    "left_wrist_yaw_joint",       # 27
    "right_wrist_yaw_joint",      # 28
]
N_JOINTS     = 29
OBS_PER_STEP = 3 + 3 + 3 + N_JOINTS*3   # 96
HISTORY_LEN  = 5
TOTAL_OBS    = OBS_PER_STEP * HISTORY_LEN  # 480
ACTION_SCALE = 0.25

# From deploy.yaml
DEFAULT_JOINT_POS = np.array([
    -0.1, -0.1,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
     0.3,  0.3,  0.3,  0.3, -0.2, -0.2,
     0.25,-0.25,  0.0,  0.0,  0.0,  0.0,
     0.97, 0.97, 0.15,-0.15,  0.0,  0.0,  0.0,  0.0,
], dtype=np.float32)

STIFFNESS = np.array([
    100.,100.,100.,150.,150.,100.,100.,100.,100.,150.,150.,
    200.,200., 40., 40., 40., 40., 40., 40., 40., 40.,
     40., 40., 40., 40., 40., 40., 40., 40.
], dtype=np.float32)

DAMPING = np.array([
    2.,2.,2.,4.,4.,2.,2.,2.,2.,4.,4.,
    5.,5.,2.,2.,10.,10.,2.,2.,10.,10.,
    10.,10.,10.,10.,10.,10.,10.,10.
], dtype=np.float32)


def kit_rpc(script: str, timeout: int = 5) -> dict:
    payload = json.dumps({"code": script, "timeout": timeout}).encode()
    req = urllib.request.Request(KIT_RPC_URL, data=payload,
                                  headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout+5) as r:
        return json.loads(r.read().decode())


def _read_state() -> Optional[dict]:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_actions(targets_deg: list) -> None:
    actions = list(zip(POLICY_JOINT_NAMES, [float(d) for d in targets_deg]))
    tmp = ACTION_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(actions, f)
    os.rename(tmp, ACTION_FILE)


def _build_setup_script() -> str:
    pj  = json.dumps(POLICY_JOINT_NAMES)
    dp  = json.dumps(DEFAULT_JOINT_POS.tolist())
    st  = json.dumps(STIFFNESS.tolist())
    dm  = json.dumps(DAMPING.tolist())
    rp  = ROBOT_PRIM
    return f"""
import builtins, json, math, os
import omni.usd, omni.timeline, omni.physx as _px
from pxr import UsdPhysics

timeline = omni.timeline.get_timeline_interface()
timeline.stop()
stage = omni.usd.get_context().get_stage()
ROBOT = "{rp}"

PJOINTS = {pj}
DEFPOS  = {dp}
STIFF   = {st}
DAMP    = {dm}

cfg = {{n:(DEFPOS[i],STIFF[i],DAMP[i]) for i,n in enumerate(PJOINTS)}}

n = 0
for prim in stage.Traverse():
    if ROBOT+'/' not in str(prim.GetPath()): continue
    nm = prim.GetPath().name
    if not prim.IsA(UsdPhysics.RevoluteJoint): continue
    d = UsdPhysics.DriveAPI.Apply(prim,'angular')
    if nm in cfg:
        pr,K,D = cfg[nm]
        d.GetStiffnessAttr().Set(K); d.GetDampingAttr().Set(D)
        d.GetTargetPositionAttr().Set(math.degrees(pr))
        d.GetMaxForceAttr().Set(500.0); n+=1
    elif 'hand' in nm.lower() or nm[:2] in ('L_','R_'):
        d.GetStiffnessAttr().Set(400.0); d.GetDampingAttr().Set(40.0)
        d.GetTargetPositionAttr().Set(0.0); d.GetMaxForceAttr().Set(100.0)
print(f"Gains applied: {{n}} joints")

for prim in stage.Traverse():
    if ROBOT+'/' not in str(prim.GetPath()): continue
    if prim.IsA(UsdPhysics.RevoluteJoint) and prim.GetPath().name in cfg:
        _,K,_ = cfg[prim.GetPath().name]
        UsdPhysics.DriveAPI(prim,'angular').GetStiffnessAttr().Set(K*10)
try:
    sim=_px.get_physx_simulation_interface(); dt=1/60.0
    for i in range(30): sim.simulate(dt,i*dt); sim.fetch_results()
    print("Pre-position: 30 steps")
except Exception as e: print(f"Pre-pos skipped: {{e}}")
for prim in stage.Traverse():
    if ROBOT+'/' not in str(prim.GetPath()): continue
    if prim.IsA(UsdPhysics.RevoluteJoint) and prim.GetPath().name in cfg:
        _,K,_ = cfg[prim.GetPath().name]
        UsdPhysics.DriveAPI(prim,'angular').GetStiffnessAttr().Set(K)

pelvis = stage.GetPrimAtPath(ROBOT+"/pelvis")
jm = {{}}
for prim in stage.Traverse():
    if ROBOT+'/' not in str(prim.GetPath()): continue
    if prim.IsA(UsdPhysics.RevoluteJoint): jm[prim.GetPath().name]=prim

builtins._g1_pelvis=pelvis; builtins._g1_jm=jm; builtins._g1_pj=PJOINTS
builtins._g1_pq=[0.,0.,0.,1.]; builtins._g1_pjp=DEFPOS[:]; builtins._g1_s=0

def _g1cb(dt):
    import builtins,json,math,os
    from pxr import UsdGeom,UsdPhysics
    try:
        xc=UsdGeom.XformCache(); xf=xc.GetLocalToWorldTransform(builtins._g1_pelvis)
        tr=xf.ExtractTranslation(); rq=xf.ExtractRotationQuat()
        im=rq.GetImaginary(); w=rq.GetReal()
        q=[float(im[0]),float(im[1]),float(im[2]),float(w)]
        pq=builtins._g1_pq
        if dt>0:
            dq=[(q[i]-pq[i])/dt for i in range(4)]
            cx,cy,cz,cw=-q[0],-q[1],-q[2],q[3]
            av=[2*(cw*dq[0]+cx*dq[3]+cy*dq[2]-cz*dq[1]),
                2*(cw*dq[1]-cx*dq[2]+cy*dq[3]+cz*dq[0]),
                2*(cw*dq[2]+cx*dq[1]-cy*dq[0]+cz*dq[3])]
        else: av=[0.,0.,0.]
        builtins._g1_pq=q
        jm2=builtins._g1_jm; pj=builtins._g1_pj
        jp=[]
        for nm in pj:
            if nm in jm2:
                t=UsdPhysics.DriveAPI(jm2[nm],'angular').GetTargetPositionAttr().Get()
                jp.append(math.radians(float(t)) if t is not None else 0.)
            else: jp.append(0.)
        pjp=builtins._g1_pjp
        jv=[(jp[i]-pjp[i])/dt if dt>0 else 0. for i in range(len(jp))]
        builtins._g1_pjp=jp
        s={{"pelvis_z":float(tr[2]),"quat_xyzw":q,"ang_vel":av,"jpos":jp,"jvel":jv,"step":builtins._g1_s,"dt":float(dt)}}
        builtins._g1_s+=1
        tmp="/tmp/g1_state.json.tmp"
        with open(tmp,'w') as f: json.dump(s,f)
        os.rename(tmp,"/tmp/g1_state.json")
        try:
            with open("/tmp/g1_actions.json") as f: acts=json.load(f)
            for nm,td in acts:
                if nm in jm2: UsdPhysics.DriveAPI(jm2[nm],'angular').GetTargetPositionAttr().Set(float(td))
        except FileNotFoundError: pass
        except: pass
    except: pass

if hasattr(builtins,'_g1_cb_sub'):
    try: builtins._g1_cb_sub.unsubscribe()
    except: pass
builtins._g1_cb_sub=_px.get_physx_interface().subscribe_physics_step_events(_g1cb)
print("Callback registered → /tmp/g1_state.json")
timeline.play()
print("Playing. ONNX runner ready.")
"""


def quat_rotate_inverse(q_xyzw: np.ndarray, v: np.ndarray) -> np.ndarray:
    x,y,z,w = q_xyzw
    qv = np.array([-x,-y,-z], dtype=np.float32)
    t  = 2.0 * np.cross(qv, v.astype(np.float32))
    return v.astype(np.float32) + w*t + np.cross(qv,t)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--onnx",       default=ONNX_PATH)
    p.add_argument("--vx",         type=float, default=0.0)
    p.add_argument("--vy",         type=float, default=0.0)
    p.add_argument("--wz",         type=float, default=0.0)
    p.add_argument("--steps",      type=int,   default=0)
    p.add_argument("--dt",         type=float, default=0.02)
    p.add_argument("--setup-only", action="store_true")
    p.add_argument("--no-setup",   action="store_true")
    args = p.parse_args()

    vel_cmd = np.array([args.vx, args.vy, args.wz], dtype=np.float32)
    print(f"[G1] cmd vx={args.vx:.2f} vy={args.vy:.2f} wz={args.wz:.2f}")

    try:
        import onnxruntime as ort
    except ImportError:
        print("ERROR: pip install onnxruntime"); sys.exit(1)

    session    = ort.InferenceSession(args.onnx, providers=["CPUExecutionProvider"])
    in_name    = session.get_inputs()[0].name
    out_name   = session.get_outputs()[0].name
    in_shape   = session.get_inputs()[0].shape
    print(f"[G1] Policy loaded. Input {in_shape}")
    if in_shape[-1] != TOTAL_OBS:
        print(f"[G1] WARNING: expected {TOTAL_OBS}, policy wants {in_shape[-1]}")

    if not args.no_setup:
        print("[G1] Setting up Isaac Sim (stop → PD gains → pre-pos → callback → play)...")
        res = kit_rpc(_build_setup_script(), timeout=45)
        out = res.get("output","").strip()
        print(f"[G1] Setup {'OK' if res.get('success') else 'WARN'}:\n{out}")
        print("[G1] Waiting for state file...")
        for _ in range(30):
            if _read_state() is not None: print("[G1] State file ready."); break
            time.sleep(0.1)
        else:
            print("[G1] WARNING: no state file — callback may not be firing.")

    if args.setup_only:
        print("[G1] --setup-only done."); return

    obs_history = np.zeros((HISTORY_LEN, OBS_PER_STEP), dtype=np.float32)
    obs_history[:] = np.concatenate([
        np.zeros(3), np.array([0.,0.,-1.]), vel_cmd,
        np.zeros(N_JOINTS), np.zeros(N_JOINTS), np.zeros(N_JOINTS)
    ])
    last_action = np.zeros(N_JOINTS, dtype=np.float32)
    step = 0; prev_sid = -1; read_errs = 0

    print("[G1] Control loop started. Ctrl+C to stop.")
    try:
        while args.steps == 0 or step < args.steps:
            t0 = time.monotonic()

            st = _read_state()
            if st is None:
                read_errs += 1
                if read_errs > 30:
                    print("[G1] State file absent — callback not running?"); break
                time.sleep(0.01); continue
            if st.get("step",0) == prev_sid:
                time.sleep(0.002); continue
            prev_sid = st["step"]; read_errs = 0

            jpos    = np.array(st["jpos"],      dtype=np.float32)
            jvel    = np.array(st["jvel"],      dtype=np.float32)
            q_xyzw  = np.array(st["quat_xyzw"], dtype=np.float32)
            ang_vel = np.array(st["ang_vel"],   dtype=np.float32)
            pz      = float(st.get("pelvis_z", 0.8))

            proj_g   = quat_rotate_inverse(q_xyzw, np.array([0.,0.,-1.]))
            jpos_rel = jpos - DEFAULT_JOINT_POS

            obs_t = np.concatenate([
                ang_vel  * 0.2,
                proj_g   * 1.0,
                vel_cmd  * 1.0,
                jpos_rel * 1.0,
                jvel     * 0.05,
                last_action,
            ])
            obs_history = np.roll(obs_history, -1, axis=0)
            obs_history[-1] = obs_t
            obs_flat = obs_history.flatten().reshape(1,-1)

            raw   = session.run([out_name],{in_name: obs_flat})[0][0]
            last_action[:] = raw
            tgt_r = raw * ACTION_SCALE + DEFAULT_JOINT_POS

            tgt_deg = [math.degrees(float(r)) for r in tgt_r]
            try: _write_actions(tgt_deg)
            except Exception as e: print(f"[G1] write err: {e}")

            step += 1

            if step % 50 == 0:
                print(f"[G1] step={step:4d}  pz={pz:.3f}m  grav_z={float(proj_g[2]):.3f}"
                      f"  |act|={float(np.linalg.norm(raw)):.3f}"
                      f"  knee={math.degrees(float(tgt_r[9])):.1f}°")
                if pz < 0.25:
                    print("[G1] Low pelvis — robot may have fallen.")

            elapsed = time.monotonic() - t0
            wait = max(0., args.dt - elapsed)
            if wait > 0: time.sleep(wait)

    except KeyboardInterrupt:
        print(f"\n[G1] Stopped at step {step}.")

    try: os.remove(ACTION_FILE)
    except FileNotFoundError: pass
    print(f"[G1] Done. {step} steps.")


if __name__ == "__main__":
    main()
