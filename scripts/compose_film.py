#!/usr/bin/env python3
"""A replay's film from its rendered frames: the close-up with the robot's
head camera inset and the tick's stage, the room overview, and a contact
sheet. Frames may be every Nth tick (replay_grasp.py --frame-every N): the
film plays at the control rate / N, so it runs in real time - a sparse take
used to keep its frames and make no film at all (2026-10-16).

    compose_film.py <scene_dir> [--keep-frames]
writes <scene>/grasp/{grasp.mp4, grasp_overview.mp4, contact_sheet.png}
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def frame_ticks(frames_dir: Path) -> list[int]:
    """The ticks rendered, from the frame files' names (f00000.png ...)."""
    return sorted(int(p.stem[1:]) for p in frames_dir.glob("f*.png") if p.stem[1:].isdigit())


def compose(out: Path, log: list, label: str, hz: float, keep_frames: bool = False) -> dict:
    from PIL import Image, ImageDraw

    frames = {k: out / f"frames_{k}" for k in ("close", "head", "overview")}
    ticks = frame_ticks(frames["close"])
    if not ticks:
        raise SystemExit(f"no frames in {frames['close']}")
    every = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    comp, over = out / "frames_comp", out / "frames_over"
    for d in (comp, over):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
    for k, i in enumerate(ticks):
        big = Image.open(frames["close"] / f"f{i:05d}.png").convert("RGB")
        hp = frames["head"] / f"f{i:05d}.png"
        if hp.exists():
            head = Image.open(hp).convert("RGB").resize((384, 288))
            big.paste(head, (big.width - 384 - 16, big.height - 288 - 16))
            d = ImageDraw.Draw(big)
            d.rectangle([big.width - 384 - 16, big.height - 288 - 16, big.width - 16, big.height - 16], outline=(255, 255, 255), width=2)
            d.text((big.width - 384 - 10, big.height - 288 - 32), "head camera", fill=(255, 255, 255))
        rr = log[i - 1] if 0 < i <= len(log) else {"t": 0.0, "phase": "start", "target_rise_m": 0.0}
        d = ImageDraw.Draw(big)
        door = (rr.get("tool") or {}).get("door")
        extra = f"  door {door['deg']:.1f} deg" if door else f"  {label} rise {100 * rr.get('target_rise_m', 0.0):.1f} cm"
        d.text((20, 18), f"Aida R5  t={rr['t']:.2f}s  {rr.get('stage', rr.get('phase'))}{extra}", fill=(255, 255, 255))
        big.save(comp / f"c{k:05d}.png")
        op = frames["overview"] / f"f{i:05d}.png"
        if op.exists():
            shutil.copyfile(op, over / f"c{k:05d}.png")
    rate = f"{hz / every:g}"
    ff = shutil.which("ffmpeg") or "ffmpeg"
    made = {}
    for name, src in (("grasp.mp4", comp), ("grasp_overview.mp4", over)):
        if not any(src.iterdir()):
            continue
        r = subprocess.run([ff, "-v", "error", "-y", "-framerate", rate, "-i", str(src / "c%05d.png"), "-c:v", "libx264", "-preset", "slow",
                            "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / name)], check=False)
        made[name] = r.returncode == 0
    n = len(ticks)
    picks = [round((n - 1) * f) for f in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
    tiles = [Image.open(comp / f"c{p:05d}.png").resize((640, 360)) for p in picks]
    sheet = Image.new("RGB", (3 * 640, 2 * 360))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % 3) * 640, (k // 3) * 360))
    sheet.save(out / "contact_sheet.png")
    for d in [comp, over] + ([] if keep_frames else list(frames.values())):
        shutil.rmtree(d, ignore_errors=True)
    return {"frames": n, "every": every, "fps": float(rate), "videos": made}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--keep-frames", action="store_true")
    args = ap.parse_args()
    d = Path(args.scene_dir)
    spec = json.loads((d / "spec.json").read_text())
    tgt = next((o for o in spec.get("objects", []) if o.get("target")), {})
    label = str(tgt.get("label") or tgt.get("name") or "target").removeprefix("the ")
    hz = float((spec.get("grasp_replay") or {}).get("control_hz", 30))
    log = json.loads((d / "grasp" / "log.json").read_text())
    print("FILM " + json.dumps(compose(d / "grasp", log, label, hz, args.keep_frames)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
