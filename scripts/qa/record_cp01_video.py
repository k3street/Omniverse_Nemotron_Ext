"""Record CP-01 (conveyor pick-place spline 3/4) as MP4 video.

CP-01 is the gold-standard verified template. This script:
  1. Resets the Kit stage
  2. Sends CP-01's goal to the chat service (agent builds the scene)
  3. Starts Kit-side viewport capture (omni.kit.capture.viewport)
  4. Plays the timeline for N seconds of real-world wall-clock time
  5. Stops capture, locates the MP4 output
  6. Copies the MP4 to /tmp/canary_replays/CP-01/recording.mp4

Sequential. Do not run concurrently with another canary or replay.

Usage:
    python -m scripts.qa.record_cp01_video [--duration 30]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx

from scripts.qa.multi_turn_session import _reset_stage, ISAAC_ASSIST_URL

KIT_EXEC = "http://127.0.0.1:8001/exec_sync"
OUT_DIR = Path("/tmp/canary_replays/CP-01")
KIT_OUT_DIR = "/tmp/cp01_capture"  # where Kit writes the MP4

CP01_PROMPT = (
    "Build a conveyor pick-place cell for verified template CP-01: "
    "Place a 2x1x0.75m table at origin. Add a Franka panda on the table top facing +Y. "
    "Add a 1.6x0.3x0.1m conveyor belt in front of the robot at y=+0.3 with surface velocity (0.2,0,0) along +X. "
    "Place 4 cubes (5cm size) on the belt at x=-0.6,-0.4,-0.2,0.0, all at y=0.3 z=0.875, with rigid body + collision + mass APIs and sleepThreshold=0. "
    "Add a 0.3x0.3x0.15m open-top bin behind the robot at y=-0.4 with floor flush on table top (z=0.75). "
    "Add a dome light. Add a proximity sensor at (0.3, 0.3, 0.86) size 0.04. "
    "Then call setup_pick_place_controller with robot_path=/World/Franka, target_source='spline' (NOT auto, force spline), "
    "sensor_path=/World/PickSensor, belt_path=/World/ConveyorBelt, "
    "source_paths=[/World/Cube_1, /World/Cube_2, /World/Cube_3, /World/Cube_4], destination_path=/World/Bin. "
    "Use robot_wizard with robot_name='franka_panda', position=[0,0,0.75], orientation=[0.7071068,0,0,0.7071068]."
)


def kit_exec(code: str, timeout: float = 60.0) -> dict:
    r = httpx.post(KIT_EXEC, json={"code": code, "timeout": timeout}, timeout=timeout + 10)
    r.raise_for_status()
    return r.json()


def setup_scene_via_chat() -> tuple[bool, str]:
    """Ask the chat service to build CP-01. Returns (ok, last_reply_text)."""
    session_id = f"cp01_record_{uuid.uuid4().hex[:6]}"
    print("→ Building CP-01 scene via chat service…")
    t0 = time.time()
    r = httpx.post(
        ISAAC_ASSIST_URL,
        json={"session_id": session_id, "message": CP01_PROMPT},
        timeout=600.0,
    )
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    body = r.json()
    msgs = body.get("response_messages", []) or []
    text = "\n".join(m.get("content", "") for m in msgs if m.get("message_type") == "text")
    tool_n = len(body.get("tool_calls", []) or [])
    elapsed = time.time() - t0
    print(f"  built in {elapsed:.1f}s, {tool_n} tool calls, {len(text)} chars reply")
    return True, text


def start_capture(duration_s: float, fps: int = 30) -> dict:
    Path(KIT_OUT_DIR).mkdir(parents=True, exist_ok=True)
    code = f"""
import os, json
os.makedirs({KIT_OUT_DIR!r}, exist_ok=True)

# Try modern API first
try:
    from omni.kit.capture.viewport import CaptureOptions, CaptureExtension
    options = CaptureOptions()
    options.file_type = ".mp4"
    options.output_folder = {KIT_OUT_DIR!r}
    options.file_name = "cp01_recording"
    options.fps = {fps}
    # Capture all viewport frames during this real-time interval
    options.capture_every_nth_frames = 1
    options.start_frame = 0
    options.end_frame = int({duration_s} * {fps})
    options.use_temp_for_capture = False
    options.real_time_capture = True

    ext = CaptureExtension.get_instance()
    if ext is None:
        raise RuntimeError("CaptureExtension instance unavailable")
    ext.start(options)
    print(json.dumps({{"started": True, "out_dir": {KIT_OUT_DIR!r}}}))
except Exception as e:
    print(json.dumps({{"started": False, "error": str(e)}}))
"""
    res = kit_exec(code, timeout=20)
    out = res.get("output", "").strip().splitlines()[-1]
    try:
        return json.loads(out)
    except Exception:
        return {"started": False, "error": out[:500]}


def play_and_wait(duration_s: float):
    code = f"""
import omni.timeline, time
tl = omni.timeline.get_timeline_interface()
tl.play()
time.sleep({duration_s})
tl.pause()
print("played_paused")
"""
    return kit_exec(code, timeout=duration_s + 30)


def stop_capture():
    code = """
import json
try:
    from omni.kit.capture.viewport import CaptureExtension
    ext = CaptureExtension.get_instance()
    if ext:
        ext.stop()
    # Wait briefly for the encoder to flush the MP4
    import omni.kit.app, time
    for _ in range(60):
        omni.kit.app.get_app().update()
    time.sleep(2.0)
    print(json.dumps({"stopped": True}))
except Exception as e:
    print(json.dumps({"stopped": False, "error": str(e)}))
"""
    res = kit_exec(code, timeout=30)
    out = res.get("output", "").strip().splitlines()[-1]
    try:
        return json.loads(out)
    except Exception:
        return {"stopped": False, "error": out[:500]}


def find_mp4() -> Path | None:
    d = Path(KIT_OUT_DIR)
    if not d.exists():
        return None
    candidates = sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def fallback_capture_pngs(duration_s: float, interval_s: float = 1.0) -> list[Path]:
    """If MP4 capture fails, snapshot every interval_s. Returns list of PNG paths."""
    print(f"→ Fallback: PNG sequence every {interval_s}s for {duration_s}s")
    pngs: list[Path] = []
    frames_dir = OUT_DIR / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    # Start the sim
    kit_exec("""
import omni.timeline
omni.timeline.get_timeline_interface().play()
print("playing")
""", timeout=10)
    n = int(duration_s / interval_s)
    for i in range(n):
        try:
            r = httpx.get("http://127.0.0.1:8001/capture", params={"max_dim": "1280"}, timeout=20)
            if r.status_code == 200:
                d = r.json()
                b64 = d.get("image_b64")
                if b64:
                    import base64
                    p = frames_dir / f"frame_{i:03d}.png"
                    p.write_bytes(base64.b64decode(b64))
                    pngs.append(p)
        except Exception as e:
            print(f"  frame {i} failed: {e}", file=sys.stderr)
        time.sleep(interval_s)
    kit_exec("""
import omni.timeline
omni.timeline.get_timeline_interface().pause()
print("paused")
""", timeout=10)
    return pngs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=30.0,
                    help="seconds of real-time playback to record")
    ap.add_argument("--skip-build", action="store_true",
                    help="assume scene is already built; record only")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_build:
        _reset_stage()
        ok, reply = setup_scene_via_chat()
        (OUT_DIR / "build_reply.txt").write_text(reply)
        if not ok:
            print(f"FAILED to build scene: {reply}", file=sys.stderr)
            return 1

    print(f"→ Starting capture (duration={args.duration}s)…")
    cap = start_capture(args.duration)
    if not cap.get("started"):
        print(f"  MP4 capture unavailable: {cap.get('error', '?')}")
        pngs = fallback_capture_pngs(args.duration, interval_s=1.5)
        print(f"  Saved {len(pngs)} PNG frames to {OUT_DIR / 'frames'}")
        return 0 if pngs else 2

    print(f"→ Playing timeline {args.duration}s (sim wall clock)…")
    play_and_wait(args.duration)

    print("→ Stopping capture, flushing encoder…")
    stop = stop_capture()
    if not stop.get("stopped"):
        print(f"  stop returned: {stop}", file=sys.stderr)

    mp4 = find_mp4()
    if mp4:
        target = OUT_DIR / "recording.mp4"
        shutil.copy(mp4, target)
        size_mb = target.stat().st_size / 1024 / 1024
        print(f"→ MP4 saved: {target} ({size_mb:.1f} MB)")
    else:
        print("  No MP4 found; falling back to PNG sequence")
        pngs = fallback_capture_pngs(args.duration, interval_s=1.5)
        print(f"  Saved {len(pngs)} PNG frames to {OUT_DIR / 'frames'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
