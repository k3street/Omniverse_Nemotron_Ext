#!/usr/bin/env python3
"""A reel of several takes' films: a title card, then for each take a chapter
card and its key moments, cut from the take's own film (compose_film.py) by
its own log's stages, each sped up or not as the reel's spec says, with a
caption and the speed shown.

A segment names where it starts and ends by stage ("descend", "lift#2" - the
second run of that stage in the take) or by tick (from_tick / to_tick); the
film's time for a tick is the time its log records (the overlay's t), so a
film rendered every Nth tick cuts the same way. A segment's "to" stage is its
first run at or after the "from" run, unless numbered.

    compose_reel.py config/reels/<reel>.json [--out workspace/reels/<reel>]
writes <out>/reel.mp4 and <out>/cards.png (the chapter cards on one sheet)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H, FPS = 1280, 720, 30


def stage_runs(log: list) -> list[tuple[str, int, int]]:
    """The take's stages in order, each run as (stage, first index, last index) into the log."""
    runs: list[list] = []
    for i, r in enumerate(log):
        st = r.get("stage") or r.get("phase")
        if runs and runs[-1][0] == st:
            runs[-1][2] = i
        else:
            runs.append([st, i, i])
    return [tuple(r) for r in runs]


def _named(spec: str) -> tuple[str, int | None]:
    name, _, n = spec.partition("#")
    return name, (int(n) if n else None)


def resolve(seg: dict, log: list, runs: list) -> tuple[float, float]:
    """A segment's film times (start, end) from its stages or ticks."""
    def run_of(spec: str, after: int = 0) -> tuple[str, int, int]:
        name, n = _named(spec)
        hits = [r for r in runs if r[0] == name]
        if not hits:
            raise SystemExit(f"no stage {name!r} in the take (its stages: {sorted({r[0] for r in runs})})")
        if n is not None:
            if n > len(hits):
                raise SystemExit(f"stage {name!r} runs {len(hits)} times, not {n}")
            return hits[n - 1]
        later = [r for r in hits if r[1] >= after]
        if not later:
            raise SystemExit(f"no run of stage {name!r} at or after index {after}")
        return later[0]
    if "from_tick" in seg:
        i0 = int(seg["from_tick"])
    else:
        i0 = run_of(seg["from"])[1]
    if "to_tick" in seg:
        i1 = int(seg["to_tick"])
    else:
        i1 = run_of(seg["to"], after=i0)[2]
    i0, i1 = max(0, min(i0, len(log) - 1)), max(0, min(i1, len(log) - 1))
    if i1 <= i0:
        raise SystemExit(f"segment {seg} ends before it starts ({i0}..{i1})")
    return float(log[i0]["t"]), float(log[i1]["t"])


def card(path: Path, title: str, lines: list[str], kicker: str = "", result: str = "") -> None:
    """A chapter (or title) card: a kicker over the title, a few lines under it, the result in green."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (W, H), (18, 21, 27))
    d = ImageDraw.Draw(im)
    f_k, f_t, f_l = ImageFont.truetype(FONT, 26), ImageFont.truetype(FONT_BOLD, 54), ImageFont.truetype(FONT, 30)
    y = 210
    if kicker:
        d.text((96, y), kicker, font=f_k, fill=(140, 150, 165))
        y += 50
    for ln in textwrap.wrap(title, 36):
        d.text((96, y), ln, font=f_t, fill=(240, 242, 245))
        y += 68
    d.rectangle([96, y + 8, 96 + 120, y + 12], fill=(64, 156, 255))
    y += 40
    for line in lines:
        for ln in textwrap.wrap(line, 64):
            d.text((96, y), ln, font=f_l, fill=(200, 205, 214))
            y += 42
    if result:
        y += 10
        for ln in textwrap.wrap(result, 64):
            d.text((96, y), ln, font=f_l, fill=(110, 210, 140))
            y += 42
    im.save(path)


def run_ff(args: list[str]) -> None:
    r = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-loglevel", "error", "-y"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {r.stderr[-1500:]}")


def card_clip(png: Path, seconds: float, out: Path) -> None:
    run_ff(["-loop", "1", "-t", f"{seconds:.3f}", "-i", str(png),
            "-vf", f"fps={FPS},format=yuv420p,fade=t=in:st=0:d=0.25,fade=t=out:st={max(0.0, seconds - 0.25):.3f}:d=0.25",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])


def segment_clip(film: Path, t0: float, t1: float, speed: float, caption: str, work: Path, out: Path, crop=None) -> float:
    """The film from t0 to t1 played at `speed`, its caption at the bottom left and its speed at the top right;
    with crop [x, y, w, h] (the film's pixels, 16:9) a zoom on the work - a lab camera's tablet is a few pixels."""
    cap = work / (out.stem + ".txt")
    cap.write_text("\n".join(textwrap.wrap(caption, 58)) if caption else " ")
    tag = work / (out.stem + "_tag.txt")
    tag.write_text("real time" if abs(speed - 1.0) < 1e-6 else f"{speed:g}x speed")
    zoom = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
    vf = (f"setpts=(PTS-STARTPTS)/{speed},fps={FPS},{zoom}scale={W}:{H}:flags=lanczos,"
          f"drawtext=fontfile={FONT}:textfile={cap}:fontsize=26:fontcolor=white:line_spacing=8:"
          f"box=1:boxcolor=black@0.55:boxborderw=12:x=28:y=h-th-40,"
          f"drawtext=fontfile={FONT_BOLD}:textfile={tag}:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.45:"
          f"boxborderw=8:x=w-tw-28:y=24")
    run_ff(["-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(film), "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])
    return (t1 - t0) / speed


def compose(spec: dict, out: Path) -> dict:
    from PIL import Image
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    parts, made, cards = [], [], []
    t = spec.get("title_card") or {}
    if t:
        png = work / "c000_title.png"
        card(png, t["title"], t.get("lines", []), t.get("kicker", ""))
        cards.append(png)
        clip = work / "c000_title.mp4"
        card_clip(png, float(t.get("seconds", 4.0)), clip)
        parts.append(clip)
    chapters = spec["chapters"]
    for k, ch in enumerate(chapters, 1):
        sd = REPO / "workspace" / "scenes" / ch["scene"]
        film, log = sd / "grasp" / "grasp.mp4", json.loads((sd / "grasp" / "log.json").read_text())
        rec = json.loads((sd / "grasp" / "grasp.json").read_text())
        png = work / f"c{k:03d}_card.png"
        card(png, ch["title"], ch.get("lines", []), f"{k} / {len(chapters)}  ·  {ch.get('date', '')}  ·  {ch['scene']}", ch.get("result", ""))
        cards.append(png)
        clip = work / f"c{k:03d}_card.mp4"
        card_clip(png, float(spec.get("card_seconds", 2.5)), clip)
        parts.append(clip)
        runs = stage_runs(log)
        segs = []
        for j, seg in enumerate(ch["segments"], 1):
            t0, t1 = resolve(seg, log, runs)
            speed = float(seg.get("speed", 1.0))
            if seg.get("max_s") and (t1 - t0) / speed > float(seg["max_s"]):
                speed = (t1 - t0) / float(seg["max_s"])
            sc = work / f"c{k:03d}_s{j:02d}.mp4"
            dur = segment_clip(film, t0, t1, speed, seg.get("caption", ""), work, sc, seg.get("crop", ch.get("crop")))
            parts.append(sc)
            segs.append({"from_s": round(t0, 2), "to_s": round(t1, 2), "speed": round(speed, 2), "out_s": round(dur, 2)})
        made.append({"scene": ch["scene"], "take_pass": rec.get("pass"), "segments": segs})
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    reel = out / "reel.mp4"
    run_ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(reel)])
    # the cards on one sheet, to check the titles at a glance
    th = [Image.open(p).resize((320, 180)) for p in cards]
    cols = 4
    sheet = Image.new("RGB", (cols * 320, ((len(th) + cols - 1) // cols) * 180), (0, 0, 0))
    for i, im in enumerate(th):
        sheet.paste(im, ((i % cols) * 320, (i // cols) * 180))
    sheet.save(out / "cards.png")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(reel)],
                               capture_output=True, text=True).stdout.strip() or 0)
    if not spec.get("keep_work"):
        shutil.rmtree(work, ignore_errors=True)
    return {"reel": str(reel), "seconds": round(dur, 1), "chapters": made}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    args = ap.parse_args()
    spec = json.loads(Path(args.spec).read_text())
    out = Path(args.out) if args.out else REPO / "workspace" / "reels" / spec["name"]
    out.mkdir(parents=True, exist_ok=True)
    res = compose(spec, out)
    (out / "reel.json").write_text(json.dumps(res, indent=1))
    print(f"REEL {res['reel']} {res['seconds']} s, {len(res['chapters'])} chapters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
