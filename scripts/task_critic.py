#!/usr/bin/env python3
"""The task critic: judges a robot episode against what its object is FOR.

A run's own verdict says whether the planned motion happened (the pinch held,
the pivot turned 40 deg, the chuck ran at the law's speed). It cannot say
whether that is how the object is used. The critic asks that question, with
the object's use ladder (asset_class_priors.json classes.<class>.use: its
purpose, how a person operates it, the sign of work done, and the rungs of
using it) as the reference:

  - which rung of the ladder the run reached, with the evidence;
  - how the robot operated the object against how it is operated;
  - whether the frames agree with the verdict's numbers (a metric met in a
    way that does not mean what it should, or a verdict misread);
  - the gaps, and the NEXT TASK: the next rung as a testable spec - its
    actions, its success measures, the capabilities the system lacks for it.

It reads the scene's spec (the task, the object, its class), the run's result
(grasp/grasp.json) and frames of the run (the contact sheet, and frames cut
from the film at the start of each stage), asks the vision judges (gemma,
local; gemini - gemini-4-argon, the film itself as video too - when a Gemini
key is in the environment or .env; claude when ANTHROPIC_API_KEY is set) and
writes <scene>/critique.json. --blind withholds the record from the judges
and scores their claims against it (workspace/knowledge/critic_experiments:
Gemini 4 matched every grip feature it called on the drill and caught the
two-hand scissors that Gemma credited as one-hand use).
The next task goes to workspace/knowledge/task_backlog.json: the loop's queue
- critic, task, plan, run, critic.

    task_critic.py workspace/scenes/aida_r5_rail_scissors [--judge gemma|claude|gemini|all] [--blind] [--no-backlog]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRIORS = REPO / "workspace" / "knowledge" / "asset_class_priors.json"
BACKLOG = REPO / "workspace" / "knowledge" / "task_backlog.json"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
GEMMA_MODEL = os.environ.get("TASK_CRITIC_GEMMA_MODEL", "gemma4")
CLAUDE_MODEL = os.environ.get("TASK_CRITIC_CLAUDE_MODEL", "claude-opus-5")
GEMINI_MODEL = os.environ.get("TASK_CRITIC_GEMINI_MODEL", "gemini-4-argon")
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"


def grip_schema(blind: bool = False) -> dict:
    s = {"type": "string"}
    out = _grip_schema()
    if blind:
        # the judge's own verdict on each grip feature, from the pictures alone,
        # scored afterwards against the record's measurement of the same feature
        out["properties"]["feature_verdicts"] = {"type": "array", "items": {"type": "object", "properties": {
            "id": s, "holds": {"type": "string", "enum": ["yes", "no", "cannot_tell"]}, "evidence": s},
            "required": ["id", "holds", "evidence"]}}
        out["required"] = out["required"] + ["feature_verdicts"]
    return out


def _grip_schema() -> dict:
    s = {"type": "string"}
    return {"type": "object", "properties": {
        "holds_as_a_person_does": {"type": "boolean"},
        "grip_differences": s,
        "grip_gaps": {"type": "array", "items": {"type": "object", "properties": {
            "gap": s, "evidence": s, "severity": {"type": "string", "enum": ["high", "medium", "low"]}},
            "required": ["gap", "evidence", "severity"]}},
        "grip_task": {"type": "object", "properties": {
            "title": s, "description": s,
            "changes_to_try": {"type": "array", "items": s},
            "success_measures": {"type": "array", "items": s}},
            "required": ["title", "description", "changes_to_try", "success_measures"]}},
        "required": ["holds_as_a_person_does", "grip_differences", "grip_gaps", "grip_task"]}


def schema(n_rungs: int, grip: bool = False, blind: bool = False) -> dict:
    out = _schema(n_rungs)
    if grip:
        out["properties"]["grip"] = grip_schema(blind)
        out["required"] = out["required"] + ["grip"]
    return out


def _schema(n_rungs: int) -> dict:
    s = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "object_purpose": s,
            "what_the_robot_did": s,
            "operated_as_intended": {"type": "boolean"},
            "how_it_differs": s,
            "rung_reached": {"type": "integer", "minimum": 0, "maximum": n_rungs},
            "rung_evidence": s,
            "verdict_matches_frames": {"type": "boolean"},
            "verdict_notes": s,
            "gaps": {"type": "array", "items": {"type": "object", "properties": {
                "gap": s, "severity": {"type": "string", "enum": ["high", "medium", "low"]}},
                "required": ["gap", "severity"]}},
            "next_task": {"type": "object", "properties": {
                "title": s, "rung": {"type": "integer"}, "description": s,
                "success_measures": {"type": "array", "items": s},
                "capabilities_needed": {"type": "array", "items": s}},
                "required": ["title", "rung", "description", "success_measures", "capabilities_needed"]},
        },
        "required": ["object_purpose", "what_the_robot_did", "operated_as_intended", "how_it_differs", "rung_reached",
                     "rung_evidence", "verdict_matches_frames", "verdict_notes", "gaps", "next_task"],
    }


def target_class(spec: dict, scene_dir: Path) -> tuple[str | None, dict]:
    """The target object's class and its spec entry: the asset's queue entry's
    class, else its registry category's name, else the spec's label."""
    obj = next((o for o in spec.get("objects", []) if o.get("target") or o.get("target_member")), spec.get("objects", [{}])[0])
    aid = obj.get("asset_id")
    cls = None
    q = REPO / "workspace" / "review_queue" / f"{aid}.json"
    if q.exists():
        e = json.loads(q.read_text())
        cls = e.get("class_hint") or (e.get("report") or {}).get("matched_class")
    if not cls:
        classes = json.loads(PRIORS.read_text())["classes"]
        for name, c in classes.items():
            if any(k in (aid or "") for k in c.get("keywords", [])) or name.rstrip("s") in (aid or ""):
                cls = name
                break
    return cls, obj


def measured(result: dict) -> dict:
    """The run's verdicts, condensed (the numbers the judge checks the frames against)."""
    keep = {}
    for k in ("pass", "lifted", "held", "target_peak_rise_m", "target_end_rise_m", "hand_object_drift_m"):
        if k in result:
            keep[k] = result[k]
    for block in ("push", "tool", "loop", "cloth", "move"):
        if isinstance(result.get(block), dict):
            keep[block] = {k: v for k, v in result[block].items() if k != "planned"}
    return keep


def measured_cap(cls: str, spec: dict, result: dict) -> tuple[int | None, list[str]]:
    """The highest rung the run's own record allows, whatever a picture
    suggests: a judge credited 'opened and closed by the holding hand alone'
    to scissors the OTHER hand's finger had pushed (2026-10-15). Returns
    (cap or None, reasons)."""
    gr = spec.get("grasp_replay") or {}
    then = gr.get("then")
    then = [then] if isinstance(then, str) else list(then or [])
    reasons, cap = [], None

    def lower(c, why):
        nonlocal cap
        cap = c if cap is None else min(cap, c)
        reasons.append(why)
    held = bool(result.get("held")) or bool((result.get("tool") or {}).get("pass")) or bool((result.get("push") or {}).get("still_held"))
    if not held:
        lower(0, "the object was not held through the run")
    if cls == "scissors":
        if any(t.startswith("finger_push") for t in then):
            lower(1, "the blades were moved by the OTHER hand's finger pushing a loop (then: finger_push), not by the holding hand's digits in the loops")
        elif not (result.get("loop") or {}).get("pass"):
            lower(1, "the record shows no passing loop verdict: the blades were not opened and closed by the holding hand's spread")
        if not any(o.get("category") == "cloth" or "sheet" in str(o.get("asset_id", "")) or "paper" in str(o.get("asset_id", "")) for o in spec.get("objects", [])):
            lower(2, "there is no material in the scene to cut")
    if cls == "power_drill":
        tool = result.get("tool") or {}
        if not tool.get("pass"):
            lower(1, "the trigger did not run the chuck at the law's speed and stop it on release")
        if any(o.get("joint_state", {}).get("direction") is not None for o in spec.get("objects", [])):
            lower(2, "the forward/reverse switch was authored in the scene, not set by the hand")
        if not any("bit" in str(o.get("asset_id", "")) or "workpiece" in str(o.get("name", "")).lower() for o in spec.get("objects", [])):
            lower(3, "there is no bit and no workpiece: no work was done")
    return cap, reasons


FINGERS = ("index", "middle", "ring", "pinky", "thumb")


def _quat_xyzw(q_wxyz):
    return list(q_wxyz[1:]) + [q_wxyz[0]]


def _rows(log: list, stages) -> list:
    stages = [stages] if isinstance(stages, str) else list(stages)
    return [r for r in log if r.get("stage") in stages]


def _in_hand_pose(r: dict):
    """The target's pose in the palm's frame at a log row (the tool's body, or
    the target's logged pose), or None when the row has neither."""
    from scipy.spatial.transform import Rotation as R
    import numpy as np
    pw = r.get("palm_world")
    body = (r.get("tool") or {}).get("body") or r.get("target_pose")
    if not pw or not body:
        return None
    Rp = R.from_quat(_quat_xyzw(pw["q_wxyz"]))
    Rt = R.from_quat(_quat_xyzw(body["q_wxyz"]))
    return Rp.inv() * Rt, Rp.inv().apply(np.asarray(body["p"], float) - np.asarray(pw["p"], float))


def grip_measures(log: list, result: dict) -> dict:
    """What the hand did to the object, from the run's own record: each digit's
    contact force by stage (mean over the stage), each digit's commanded and
    reached curl, the tool's turn and slide in the hand over each stage, and
    which digits worked a tool's control. The grip, not just whether it held."""
    import numpy as np
    stages = []
    for r in log:
        if not stages or stages[-1] != r.get("stage"):
            stages.append(r.get("stage"))
    out = {"stages": stages, "digit_n": {}, "digit_n_by_body": {}, "curl": {}, "in_hand": {}, "on_control_n": {}}
    for st in stages:
        rows = _rows(log, st)
        cn = [r["contact_n"] for r in rows if isinstance(r.get("contact_n"), dict)]
        if cn:
            out["digit_n"][st] = {d: round(float(np.mean([c.get(d, 0.0) for c in cn])), 2) for d in FINGERS + ("palm",)}
        cb = [r["contact_n_by_body"] for r in rows if isinstance(r.get("contact_n_by_body"), dict)]
        if cb:
            out["digit_n_by_body"][st] = {b: {d: round(float(np.mean([c.get(b, {}).get(d, 0.0) for c in cb])), 2) for d in FINGERS + ("palm",)}
                                          for b in cb[0]}
        hands = [r["hand"] for r in rows if isinstance(r.get("hand"), dict)]
        if hands:
            cmd = np.mean([h["cmd_rad"] for h in hands], axis=0)
            lock = np.mean([h.get("lock_rad", h["cmd_rad"]) for h in hands], axis=0)
            halted = np.max([h.get("halted", [0] * 5) for h in hands], axis=0)
            out["curl"][st] = {d: {"cmd_rad": round(float(cmd[k]), 3), "at_rad": round(float(lock[k]), 3), "halted": bool(halted[k])}
                               for k, d in enumerate(FINGERS) if k < len(cmd)}
            flex = [r.get("thumb_flex") for r in rows if r.get("thumb_flex") is not None]
            if flex:
                out["curl"][st]["thumb"]["flex_rad"] = round(float(np.mean(flex)), 3)
        poses = [x for x in (_in_hand_pose(r) for r in (rows[0], rows[-1])) if x is not None] if rows else []
        if len(poses) == 2:
            (R0, p0), (R1, p1) = poses
            out["in_hand"][st] = {"turn_deg": round(float(np.degrees((R0.inv() * R1).magnitude())), 2),
                                  "slide_mm": round(float(np.linalg.norm(p1 - p0) * 1000), 1)}
        trig = [(r.get("tool") or {}).get("on_trigger_n") for r in rows]
        trig = [t for t in trig if isinstance(t, dict)]
        if trig:
            out["on_control_n"][st] = {d: round(float(max(t.get(d, 0.0) for t in trig)), 2) for d in trig[0]}
    tool = result.get("tool") or {}
    if "trigger_peak_fraction" in tool:
        out["trigger_fraction"] = tool["trigger_peak_fraction"]
    return out


def grip_findings(grip: dict, gm: dict, spec: dict) -> list[dict]:
    """The class's grip features (asset_class_priors classes.<class>.use.grip.
    features), each checked against the measures: pass, fail, or unmeasured
    (the record does not hold what the feature needs), with the numbers."""
    import numpy as np
    out = []
    for f in grip.get("features", []):
        m, ev, ok = f.get("measure"), None, None
        st = f.get("stages") or []
        if m == "digit_n":
            vals = {d: [gm["digit_n"][s][d] for s in st if s in gm["digit_n"]] for d in f["digits"]}
            if all(vals.values()):
                means = {d: round(float(np.mean(v)), 2) for d, v in vals.items()}
                ok = all(v >= f["min_n"] for v in means.values())
                ev = f"mean contact over {', '.join(st)}: " + ", ".join(f"{d} {v} N" for d, v in means.items())
        elif m == "digits_in_loops":
            # each digit on ITS loop's body (the loop grip names them): the thumb on
            # one half, the finger on the other - not both pressing one half
            lg = (spec.get("grasp_plan") or {}).get("loop_grip") or {}
            want = {d: lg.get(role) for d, role in f["pairs"].items()}
            per = [gm.get("digit_n_by_body", {}).get(s_) for s_ in st]
            per = [x for x in per if x]
            if per and all(want.values()):
                got = {d: round(float(np.mean([x.get(b, {}).get(d, 0.0) for x in per])), 2) for d, b in want.items()}
                ok = all(v >= f.get("min_n", 0.3) for v in got.values())
                ev = "mean contact over " + ", ".join(st) + ": " + ", ".join(f"{d} on {want[d]} {v} N" for d, v in got.items())
        elif m == "thumb_opposes":
            flex = [gm["curl"][s]["thumb"].get("flex_rad", gm["curl"][s]["thumb"]["at_rad"]) for s in st if s in gm["curl"]]
            force = [gm["digit_n"][s]["thumb"] for s in st if s in gm["digit_n"]]
            if flex and force:
                fl, fo = float(np.mean(flex)), float(np.mean(force))
                ok = fl >= f["min_flex_rad"] and fo >= f["min_n"]
                ev = f"thumb flexed {fl:.2f} rad (opposing needs >= {f['min_flex_rad']}), pressing {fo:.1f} N"
                if fo >= f["min_n"] and fl < f["min_flex_rad"]:
                    ev += " - a flat thumb that the tool rests against, not one closing round it"
        elif m == "control_digits":
            per = {}
            for s in st:
                for d, v in (gm["on_control_n"].get(s) or {}).items():
                    per[d] = max(per.get(d, 0.0), v)
            if per:
                others = {d: v for d, v in per.items() if d not in f["only"] and v > f.get("max_other_n", 0.5)}
                ok = all(per.get(d, 0.0) > f.get("min_n", 1.0) for d in f["only"]) and not others
                ev = "peak force on the control: " + ", ".join(f"{d} {v} N" for d, v in per.items())
        elif m == "trigger_fraction":
            if "trigger_fraction" in gm:
                ok = gm["trigger_fraction"] >= f["min"]
                ev = f"peak pull {gm['trigger_fraction']:.0%} of the trigger's travel (needs {f['min']:.0%})"
                cs = [gm["curl"][s]["index"] for s in st if s in gm["curl"]]
                if cs and not ok:
                    c = cs[-1]
                    ev += (f"; the index commanded to {c['cmd_rad']} rad reached {c['at_rad']} rad"
                           + (" and halted (stalled at the collision threshold)" if c["halted"] else ""))
        elif m == "in_hand_motion":
            seg = [gm["in_hand"][s] for s in st if s in gm["in_hand"]]
            if seg:
                turn = round(sum(x["turn_deg"] for x in seg), 2)
                slide = round(sum(x["slide_mm"] for x in seg), 1)
                ok = turn <= f["max_turn_deg"] and slide <= f["max_slide_mm"]
                ev = f"over {', '.join(st)}: turned {turn} deg, slid {slide} mm in the hand"
        out.append({"id": f["id"], "feature": f["text"], "pass": ok, "evidence": ev or "unmeasured: the record does not hold it"})
    return out


def stage_frames(scene_dir: Path, out_dir: Path, n_max: int = 8, width: int = 640) -> list[Path]:
    """A frame from the film at the start of each stage (by the log's ticks)."""
    log = json.loads((scene_dir / "grasp" / "log.json").read_text())
    film = scene_dir / "grasp" / "grasp.mp4"
    if not film.exists():
        return []
    firsts, last = [], None
    for r in log:
        if r["stage"] != last:
            firsts.append((r["stage"], r["t"]))
            last = r["stage"]
    # the stages' starts, then the end; thinned to n_max evenly
    picks = firsts + [("end", log[-1]["t"])]
    if len(picks) > n_max:
        idx = [round(i * (len(picks) - 1) / (n_max - 1)) for i in range(n_max)]
        picks = [picks[i] for i in sorted(set(idx))]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, (stage, t) in enumerate(picks):
        p = out_dir / f"f{k:02d}_{stage}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t + 0.5):.2f}", "-i", str(film), "-frames:v", "1",
                        "-vf", f"scale={width}:-1", str(p)], check=False)
        if p.exists():
            paths.append(p)
    return paths


def grip_prompt_blind(use: dict, findings: list[dict]) -> str:
    """The grip question without the record: how a person holds it and the
    features to judge, each to be answered from the pictures (and film)."""
    grip = use.get("grip") or {}
    lines = "\n".join(f"  - {f['id']}: {f['feature']}" for f in findings)
    return f"""

THE GRIP. How a person holds it: {grip.get('how')}
Judge each of these grip features FROM THE PICTURES ALONE (no measurements are given):
{lines}

Also answer grip:
- feature_verdicts: for every feature above, its id, holds = yes / no / cannot_tell, and what in the pictures shows it (name the frame).
- holds_as_a_person_does: true only if the pictures show the person's grip described above.
- grip_differences: how this grip differs from a person's, digit by digit.
- grip_gaps: each difference that matters, with the visual evidence, by severity.
- grip_task: the next improvement to THIS grip (not the next rung), and the measures that would show it worked."""


def grip_prompt(use: dict, findings: list[dict], gm: dict, spec: dict) -> str:
    """The grip half of the question: how a person holds it, what the record
    measured feature by feature, and the planner's levers (the grasp plan's own
    options and the hand's firmware settings, with their reasons)."""
    grip = use.get("grip") or {}
    lines = "\n".join(f"  - {f['id']}: {'PASS' if f['pass'] else ('FAIL' if f['pass'] is False else 'UNMEASURED')} - {f['feature']}. {f['evidence']}"
                       for f in findings)
    plan = spec.get("grasp_plan") or {}
    task = plan.get("task")
    levers = {k: v for k, v in (plan.get(task) or {}).items()} if task else {}
    hand = {k: v for k, v in ((spec.get("grasp_replay") or {}).get("hand_model") or {}).items() if k != "calibration"}
    return f"""

THE GRIP. How a person holds it: {grip.get('how')}
The record's grip features (measured from the run's own contact forces, the hand's motors and the object's pose in the palm):
{lines}
Digit contact force by stage (N, mean): {json.dumps(gm.get('digit_n'))}
The planner that chose this grip ({task}) and its options, with the reasons they were set: {json.dumps(levers)}
The hand's firmware settings: {json.dumps(hand)}

Also answer grip:
- holds_as_a_person_does: true only if every feature passes AND the frames show the person's grip described above.
- grip_differences: how this grip differs from a person's, digit by digit.
- grip_gaps: each difference that matters, with the measured evidence, by severity (a FAIL above is at least medium).
- grip_task: the next improvement to THIS grip (not the next rung): what to change in the planner or the hand (name its options where one applies), and the measures that show it worked."""


def prompt_text(spec: dict, cls: str, use: dict, meas: dict, frame_names: list[str], grip_part: str = "",
                blind: bool = False, video: bool = False) -> str:
    ladder = "\n".join(f"  rung {i + 1}: {r}" for i, r in enumerate(use.get("ladder", [])))
    text = f"""You are the critic of a robot experiment in simulation. Judge the run against what the OBJECT IS FOR, not against the experiment's own success metric.

The object: a {cls}. What it is for: {use.get('purpose')}
How a person operates it: {use.get('operated_by')}
The sign that it did its work: {use.get('work_signal')}
Its use ladder (rung 0 = not even held):
{ladder}

The experiment's stated purpose: {spec.get('purpose')}
The experiment's own measured outcome (JSON): {json.dumps(meas)}

The images: frames of the run in order ({', '.join(frame_names)}), and a contact sheet of the whole run.
The robot is an Aida R5 with two PSYONIC Ability Hands (five digits: index, middle, ring, pinky, a thumb that rotates and flexes).

Answer:
- object_purpose: one sentence.
- what_the_robot_did: what the frames and numbers show, plainly.
- operated_as_intended: true only if the robot operated it the way a person does (see 'operated by').
- how_it_differs: how the robot's operation differs from that.
- rung_reached: the highest rung FULLY shown (0 if none), and rung_evidence.
- verdict_matches_frames / verdict_notes: do the frames agree with the numbers? Say if a number is met in a way that does not mean what it should.
- gaps: what stands between this run and the object's real use, by severity.
- next_task: the NEXT rung as one concrete, testable experiment: what the robot does, how success is measured (numbers a simulation can check), and the capabilities the system would need that this run did not show.
Be specific and critical. Do not praise.""" + grip_part
    if blind:
        # no numbers: the judge says what the pictures show, and the record scores it
        text = text.replace(f"The experiment's own measured outcome (JSON): {json.dumps(meas)}",
                            "The experiment's measured outcome is WITHHELD: judge from the pictures alone. Where the pictures cannot "
                            "show something, say so rather than guess.")
        text = text.replace("- verdict_matches_frames / verdict_notes: do the frames agree with the numbers? Say if a number is met in a way that does not mean what it should.",
                            "- verdict_matches_frames / verdict_notes: answer true, and in verdict_notes say what the pictures could NOT show you.")
        text = text.replace("what the frames and numbers show", "what the pictures show")
    if video:
        text = text.replace("The images: frames of the run in order", "The film of the whole run (video), then still frames of the run in order")
    return text


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def judge_gemma(images: list[Path], text: str, n_rungs: int, grip: bool = False, blind: bool = False, video: Path | None = None) -> dict:
    payload = {"model": GEMMA_MODEL, "stream": False, "format": schema(n_rungs, grip, blind), "options": {"temperature": 0},
               "messages": [{"role": "user", "content": text, "images": [b64(p) for p in images]}]}
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        out = json.loads(r.read())
    res = json.loads(out["message"]["content"])
    res.update(judge="gemma", model=GEMMA_MODEL)
    return res


def judge_claude(images: list[Path], text: str, n_rungs: int, grip: bool = False, blind: bool = False, video: Path | None = None) -> dict:
    import anthropic

    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(p)}} for p in images]
    content.append({"type": "text", "text": text})
    client = anthropic.Anthropic()
    resp = client.messages.create(model=CLAUDE_MODEL, max_tokens=16000, messages=[{"role": "user", "content": content}],
                                  output_config={"format": {"type": "json_schema", "schema": schema(n_rungs, grip, blind)}})
    if resp.stop_reason == "refusal":
        raise RuntimeError("the judge declined")
    res = json.loads(next(b.text for b in resp.content if b.type == "text"))
    res.update(judge="claude", model=CLAUDE_MODEL)
    return res


def upsert_task(backlog: dict, task: dict) -> None:
    """Add a task to the backlog, or replace the open one it repeats (same
    scene, kind, judge and rung, still 'proposed'): a critic run again on a
    new take restates its open tasks, it does not stack copies of them."""
    key = lambda t: (t.get("from_scene"), t.get("kind", "rung"), t.get("judge"), t.get("rung") if t.get("kind", "rung") == "rung" else None)
    for i, t in enumerate(backlog["tasks"]):
        if t.get("status") == "proposed" and key(t) == key(task):
            backlog["tasks"][i] = task
            return
    backlog["tasks"].append(task)


def _gemini_key() -> str | None:
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(k):
            return os.environ[k]
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, sep, v = line.strip().removeprefix("export ").partition("=")
            if sep and k in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and v.strip():
                return v.strip().strip("'\"")
    return None


def film_for_judge(film: Path, out: Path, width: int = 640, max_mb: float = 6.0) -> Path | None:
    """The take's film made small enough to send inline (scaled, re-encoded),
    or None when it cannot be."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(film), "-vf", f"scale={width}:-2", "-an", "-c:v", "libx264",
                    "-crf", "32", "-preset", "veryfast", str(out)], check=False)
    return out if out.exists() and out.stat().st_size <= max_mb * 1e6 else None


def judge_gemini(images: list[Path], text: str, n_rungs: int, grip: bool = False, blind: bool = False, video: Path | None = None) -> dict:
    """Gemini over REST (the repo's key in the environment or .env): the film
    as video when given, the stills, the question; JSON by the same schema."""
    import time
    key = _gemini_key()
    if not key:
        raise RuntimeError("no GEMINI_API_KEY or GOOGLE_API_KEY")
    parts = []
    if video is not None:
        parts.append({"inline_data": {"mime_type": "video/mp4", "data": b64(video)}})
    parts += [{"inline_data": {"mime_type": "image/png", "data": b64(p)}} for p in images]
    parts.append({"text": text})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema(n_rungs, grip, blind)}}
    req = urllib.request.Request(f"{GEMINI_API}/{GEMINI_MODEL}:generateContent", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        resp = json.loads(r.read())
    cand = resp["candidates"][0]
    txt = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
    res = json.loads(txt)
    res.update(judge="gemini", model=GEMINI_MODEL, seconds=round(time.time() - t0, 1), with_video=video is not None,
               usage=resp.get("usageMetadata"), finish=cand.get("finishReason"))
    return res


JUDGES = {"gemma": judge_gemma, "claude": judge_claude, "gemini": judge_gemini}


def blind_scores(v: dict, findings: list[dict], cap: int | None) -> dict:
    """A blind judge's claims against the record: its rung against the cap
    the record allows, and each grip feature it called against the record's
    measurement (cannot_tell counted apart)."""
    rec = {f["id"]: f["pass"] for f in findings if f["pass"] is not None}
    said = {fv["id"]: fv["holds"] for fv in (v.get("grip") or {}).get("feature_verdicts", [])}
    agree = [k for k, h in said.items() if k in rec and h != "cannot_tell" and (h == "yes") == rec[k]]
    wrong = [k for k, h in said.items() if k in rec and h != "cannot_tell" and (h == "yes") != rec[k]]
    unsure = [k for k, h in said.items() if k in rec and h == "cannot_tell"]
    claimed = v.get("rung_claimed", v.get("rung_reached"))
    return {"rung_claimed": claimed, "record_cap": cap, "over_credited": (cap is not None and claimed is not None and claimed > cap),
            "features_agree": agree, "features_wrong": wrong, "features_cannot_tell": unsure,
            "features_missing": [k for k in rec if k not in said]}


def critique(scene_dir: Path, judges: list[str], record_only: bool = False, blind: bool = False, write_backlog: bool = True,
             out_name: str = "critique.json") -> dict:
    spec = json.loads((scene_dir / "spec.json").read_text())
    result = json.loads((scene_dir / "grasp" / "grasp.json").read_text())
    cls, obj = target_class(spec, scene_dir)
    use = (json.loads(PRIORS.read_text())["classes"].get(cls or "", {}) or {}).get("use")
    if not use:
        raise SystemExit(f"{scene_dir.name}: no use ladder for class {cls!r} (asset_class_priors.json classes.<class>.use) - "
                         "the critic judges against what the object is for, and that is not recorded")
    # the pictures must be of THIS take: a physics-only take leaves the last filmed
    # take's film and sheet in place, and the critic judged a new record against an
    # old take's frames (2026-10-15). Older than the plan the take replayed: stale
    plan_t = max((p.stat().st_mtime for p in (scene_dir / "grasp_plan").glob("targets.npz")), default=0.0)
    film = scene_dir / "grasp" / "grasp.mp4"
    sheet = scene_dir / "grasp" / "contact_sheet.png"
    stale = [p.name for p in (film, sheet) if p.exists() and p.stat().st_mtime < plan_t]
    frames = stage_frames(scene_dir, scene_dir / "critique_frames") if film.exists() and film.name not in stale else []
    images = frames + ([sheet] if sheet.exists() and sheet.name not in stale else [])
    if not images and not record_only:
        raise SystemExit(f"{scene_dir.name}: no film or contact sheet of this take" + (f" ({', '.join(stale)} older than its plan)" if stale else "")
                         + " - film the run, or judge the record alone (--record-only)")
    meas = measured(result)
    log_path = scene_dir / "grasp" / "log.json"
    gm = grip_measures(json.loads(log_path.read_text()), result) if log_path.exists() else {}
    findings = grip_findings(use.get("grip") or {}, gm, spec) if gm and use.get("grip") else []
    has_grip = bool(findings)
    n = len(use.get("ladder", []))
    small_film = film_for_judge(film, scene_dir / "critique_frames" / "film_small.mp4") \
        if ("gemini" in judges and film.exists() and film.name not in stale) else None
    verdicts = []
    for j in ([] if record_only else judges):
        fn = JUDGES[j]
        if j == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
            verdicts.append({"judge": "claude", "skipped": "no ANTHROPIC_API_KEY"})
            continue
        if j == "gemini" and not _gemini_key():
            verdicts.append({"judge": "gemini", "skipped": "no GEMINI_API_KEY"})
            continue
        vid = small_film if j == "gemini" else None
        grip_part = (grip_prompt_blind(use, findings) if blind else grip_prompt(use, findings, gm, spec)) if has_grip else ""
        text = prompt_text(spec, cls, use, meas, [p.stem for p in frames], grip_part, blind=blind, video=vid is not None)
        try:
            verdicts.append(fn(images, text, n, has_grip, blind, vid))
        except Exception as e:                                   # noqa: BLE001 - a judge's failure is recorded, not fatal
            detail = e.read().decode()[:400] if hasattr(e, "read") else ""
            verdicts.append({"judge": j, "error": (str(e) + " " + detail)[:600]})
    text = prompt_text(spec, cls, use, meas, [p.stem for p in frames],
                       (grip_prompt_blind(use, findings) if blind else grip_prompt(use, findings, gm, spec)) if has_grip else "", blind=blind)
    ok = [v for v in verdicts if "next_task" in v]
    cap, cap_why = measured_cap(cls, spec, result)
    if blind:
        for v in ok:
            v["blind_scores"] = blind_scores(v, findings, cap)
    if cap is not None and write_backlog:
        # a judge that credited more than the record allows is asked again for the
        # next task, from the rung the record supports (the next rung is cap + 1)
        for v in ok:
            if v["rung_reached"] > cap or v["next_task"].get("rung", cap + 1) > cap + 1:
                v["rung_claimed"] = v["rung_reached"]
                v["rung_reached"] = min(v["rung_reached"], cap)
                redo = text + (f"\n\nThe run's own record caps it at rung {cap}: " + "; ".join(cap_why) +
                               f". Propose next_task for rung {cap + 1} exactly (not beyond): "
                               f"'{use['ladder'][min(cap, n - 1)]}'. Keep the other answers as they were.")
                try:
                    v2 = JUDGES[v["judge"]](images, redo, n, has_grip, blind, small_film if v["judge"] == "gemini" else None)
                    v["next_task_first"] = v["next_task"]
                    v["next_task"] = v2["next_task"]
                except Exception as e:                           # noqa: BLE001
                    v["next_task_redo_error"] = str(e)[:200]
    out = {"scene": scene_dir.name, "measured_cap": cap, "measured_cap_why": cap_why, "at": datetime.now().isoformat(timespec="seconds"), "object": obj.get("asset_id"), "class": cls,
           "use": use, "measured": meas, "grip_measures": gm, "grip_findings": findings,
           "grip_failed": [f["id"] for f in findings if f["pass"] is False],
           "frames": [str(p) for p in images], "film_sent": str(small_film) if small_film else None, "stale_pictures": stale,
           "record_only": record_only, "blind": blind, "judges": verdicts,
           # the consensus is the LOWEST rung any judge saw fully shown: a rung is reached when every judge agrees
           "rung_reached": min([v["rung_reached"] for v in ok] + ([cap] if cap is not None else []), default=cap),
           "operated_as_intended": all(v["operated_as_intended"] for v in ok) if ok else None,
           "verdict_matches_frames": all(v["verdict_matches_frames"] for v in ok) if ok else None}
    measured_any = any(f["pass"] is not None for f in findings)
    if not has_grip:
        out["holds_as_a_person_does"] = None
    elif ok:
        out["holds_as_a_person_does"] = all(v.get("grip", {}).get("holds_as_a_person_does", False) for v in ok) and not out["grip_failed"]
    else:
        # the record alone: a grip with nothing measured is unknown, not a person's
        out["holds_as_a_person_does"] = (not out["grip_failed"]) if measured_any else None
    (scene_dir / out_name).write_text(json.dumps(out, indent=1))
    if not write_backlog:
        return out
    if has_grip and (out["grip_failed"] or any("grip" in v for v in ok)):
        # the grip's own improvement task: the failed features (from the record,
        # whatever a judge says) and each judge's proposed change to the grip
        backlog = json.loads(BACKLOG.read_text()) if BACKLOG.exists() else {"tasks": []}
        upsert_task(backlog, {"from_scene": scene_dir.name, "object": obj.get("asset_id"), "class": cls, "at": out["at"],
                                 "kind": "grip", "judge": "record", "rung_reached": out["rung_reached"],
                                 "title": "grip: " + ", ".join(out["grip_failed"]) if out["grip_failed"] else "grip: judged",
                                 "failed_features": [f for f in findings if f["pass"] is False],
                                 "judge_tasks": [dict(v["grip"]["grip_task"], judge=v["judge"]) for v in ok if "grip" in v],
                                 "status": "proposed"})
        BACKLOG.write_text(json.dumps(backlog, indent=1))
    if ok:
        backlog = json.loads(BACKLOG.read_text()) if BACKLOG.exists() else {"tasks": []}
        for v in ok:
            upsert_task(backlog, {"from_scene": scene_dir.name, "object": obj.get("asset_id"), "class": cls, "at": out["at"],
                                  "judge": v["judge"], "rung_reached": v["rung_reached"], **v["next_task"], "status": "proposed"})
        BACKLOG.parent.mkdir(parents=True, exist_ok=True)
        BACKLOG.write_text(json.dumps(backlog, indent=1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--judge", default="all", choices=("gemma", "claude", "gemini", "all"))
    ap.add_argument("--blind", action="store_true", help="the judges see the pictures (and film) only, not the record; their claims are scored against it")
    ap.add_argument("--no-backlog", action="store_true", help="an experiment: write the critique, leave the backlog alone")
    ap.add_argument("--out", default="critique.json", help="the critique's file name in the scene directory")
    ap.add_argument("--record-only", action="store_true", help="no vision judges: the record's caps and grip findings alone (a physics-only take)")
    args = ap.parse_args()
    judges = ["gemma", "claude", "gemini"] if args.judge == "all" else [args.judge]
    out = critique(Path(args.scene_dir), judges, args.record_only, args.blind, not args.no_backlog, args.out)
    for v in out["judges"]:
        if "blind_scores" in v:
            print(f"[{v['judge']}] BLIND vs the record: {json.dumps(v['blind_scores'])}")
    for v in out["judges"]:
        if "next_task" in v:
            print(f"[{v['judge']}] rung {v['rung_reached']} of {len(out['use']['ladder'])}"
                  + (f" (claimed {v['rung_claimed']}, capped by the record)" if "rung_claimed" in v else "")
                  + f"; operated as intended: {v['operated_as_intended']}; "
                  f"verdict matches the frames: {v['verdict_matches_frames']}")
            print(f"   did: {v['what_the_robot_did']}")
            print(f"   differs: {v['how_it_differs']}")
            for g in v["gaps"]:
                print(f"   gap ({g['severity']}): {g['gap']}")
            print(f"   NEXT (rung {v['next_task']['rung']}): {v['next_task']['title']} - {v['next_task']['description']}")
            for m in v["next_task"]["success_measures"]:
                print(f"      measure: {m}")
        else:
            print(f"[{v['judge']}] {v.get('error') or v.get('skipped')}")
    for w in out.get("measured_cap_why", []):
        print(f"   record: {w}")
    for f in out.get("grip_findings", []):
        print(f"   grip {f['id']}: {'PASS' if f['pass'] else ('FAIL' if f['pass'] is False else 'unmeasured')} - {f['evidence']}")
    for v in out["judges"]:
        g = v.get("grip")
        if g:
            print(f"   [{v['judge']}] grip as a person's: {g['holds_as_a_person_does']} - {g['grip_differences']}")
            for gg in g["grip_gaps"]:
                print(f"      grip gap ({gg['severity']}): {gg['gap']} [{gg['evidence']}]")
            t = g["grip_task"]
            print(f"      GRIP TASK: {t['title']} - {t['description']}")
            for c in t["changes_to_try"]:
                print(f"         change: {c}")
            for m in t["success_measures"]:
                print(f"         measure: {m}")
    print(f"CRITIQUE {out['scene']}: rung {out['rung_reached']}, operated as intended {out['operated_as_intended']}, "
          f"grip as a person's {out.get('holds_as_a_person_does')}, "
          f"verdict matches frames {out['verdict_matches_frames']} -> {Path(args.scene_dir) / args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
