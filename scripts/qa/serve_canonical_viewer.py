"""Canonical Review Interface — HTTP server for browsing / running / scoring
the workspace/templates/CP-*.json canonicals.

Single-page UI:
  - Filterable left nav (search by id, tier, gate status).
  - Right panel: goal, tools, code, gates, feedback, run history.
  - [▶ Build] triggers execute_template_canonical via Kit RPC.
  - [▶ Build + Verify] also runs verify_pickplace_pipeline.
  - [▶ Build + Verify + Simulate] runs the full function-gate cycle.
  - Per-run results cached in workspace/canonical_runs/<id>.jsonl.
  - Feedback notes stored in workspace/canonical_feedback/<id>.jsonl.
  - Wilson score computed live from run history (passes / total).

Usage:
    python -m scripts.qa.serve_canonical_viewer
    open http://127.0.0.1:8092/

Optional env:
    PORT=8092
    TEMPLATES_DIR=workspace/templates
    FEEDBACK_DIR=workspace/canonical_feedback
    RUNS_DIR=workspace/canonical_runs
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import getpass
import html
import json
import math
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR", REPO_ROOT / "workspace" / "templates"))
FEEDBACK_DIR = Path(os.environ.get("FEEDBACK_DIR", REPO_ROOT / "workspace" / "canonical_feedback"))
RUNS_DIR = Path(os.environ.get("RUNS_DIR", REPO_ROOT / "workspace" / "canonical_runs"))
FUNC_GATE_LOG = Path(os.environ.get("FUNC_GATE_LOG", "/tmp/func_gate_audit.log"))
PORT = int(os.environ.get("PORT", "8092"))

# Wilson score (95% CI) — copied so we don't pull in scripts.qa._stats at import time.
def wilson_lower(passes: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = passes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def wilson_upper(passes: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 1.0
    p = passes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return min(1.0, (centre + margin) / denom)


# ── Data loading ─────────────────────────────────────────────────────────────

def list_canonicals() -> list[str]:
    return sorted(
        (p.stem for p in TEMPLATES_DIR.glob("CP-*.json")),
        key=lambda s: (len(s), s),  # CP-1 < CP-10 < CP-77
    )


def load_template(label: str) -> dict | None:
    p = TEMPLATES_DIR / f"{label}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        return {"task_id": label, "_load_error": str(e)}


def load_func_gate_audit() -> dict[str, dict]:
    """Parse /tmp/func_gate_audit.log into {label: {success, in_xy, ...}}."""
    out: dict[str, dict] = {}
    if not FUNC_GATE_LOG.exists():
        return out
    for line in FUNC_GATE_LOG.read_text().splitlines():
        line = line.strip()
        if not line.startswith("CP-"):
            continue
        try:
            label_part, rest = line.split(":", 1)
            label = label_part.strip()
            d = {
                "success": "success=True" in rest,
                "in_xy": "in_xy=True" in rest,
                "above_floor": "above_floor=True" in rest,
                "raw": rest.strip()[:240],
            }
            out[label] = d
        except Exception:
            continue
    return out


def load_runs(label: str) -> list[dict]:
    p = RUNS_DIR / f"{label}.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def save_run(label: str, entry: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    p = RUNS_DIR / f"{label}.jsonl"
    with p.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def load_feedback(label: str) -> list[dict]:
    p = FEEDBACK_DIR / f"{label}.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def save_feedback(label: str, entry: dict) -> None:
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    p = FEEDBACK_DIR / f"{label}.jsonl"
    with p.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def latest_run_summary(label: str, audit: dict[str, dict]) -> dict:
    """Combine workspace/canonical_runs/<id> + audit log into one summary."""
    runs = load_runs(label)
    form_passes = sum(1 for r in runs if r.get("form_ok") is True)
    form_total = sum(1 for r in runs if r.get("form_ok") is not None)
    func_passes = sum(1 for r in runs if r.get("func_ok") is True)
    func_total = sum(1 for r in runs if r.get("func_ok") is not None)
    last = runs[-1] if runs else None
    audit_entry = audit.get(label)
    return {
        "form_passes": form_passes,
        "form_total": form_total,
        "func_passes": func_passes,
        "func_total": func_total,
        "last": last,
        "audit": audit_entry,
        "runs": len(runs),
    }


# ── Run trigger (Kit RPC) ────────────────────────────────────────────────────

async def _do_run(label: str, mode: str) -> dict:
    """mode in {build, build_verify, build_verify_simulate}."""
    tmpl = load_template(label)
    if tmpl is None:
        return {"ok": False, "error": f"template {label} not found"}
    if "_load_error" in tmpl:
        return {"ok": False, "error": tmpl["_load_error"]}
    os.environ.setdefault("AUTO_APPROVE", "true")

    try:
        from service.isaac_assist_service.chat.canonical_instantiator import (
            execute_template_canonical, settle_after_canonical,
        )
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        from scripts.qa.verifier_smoke_tests import _reset_scene as _reset
    except Exception as e:
        return {"ok": False, "error": f"import failed: {e}", "traceback": traceback.format_exc()[-1500:]}

    out: dict = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "label": label,
        "form_ok": None,
        "func_ok": None,
    }

    try:
        await _reset()
        t0 = time.time()
        build = await execute_template_canonical(tmpl)
        out["build_ms"] = int((time.time() - t0) * 1000)
        out["instantiated"] = bool(build.get("instantiated"))
        out["n_ok"] = build.get("n_ok")
        out["n_calls"] = build.get("n_calls")
        out["build_errors"] = [str(e)[:200] for e in (build.get("errors") or [])[:5]]
        if not out["instantiated"]:
            out["ok"] = False
            return out

        await settle_after_canonical(tmpl)

        if mode in ("build_verify", "build_verify_simulate"):
            t0 = time.time()
            stages = (tmpl.get("verify_args") or {}).get("stages") or []
            res = await execute_tool_call("verify_pickplace_pipeline", {"stages": stages})
            out["verify_ms"] = int((time.time() - t0) * 1000)
            verify_out = (res.get("output") or "").strip()
            json_lines = [l for l in verify_out.splitlines() if l.strip().startswith("{")]
            if json_lines:
                d = json.loads(json_lines[-1])
                out["form_ok"] = bool(d.get("pipeline_ok"))
                out["form_issues"] = list(d.get("issues") or [])[:8]
            else:
                out["form_ok"] = False
                out["form_issues"] = ["NO_JSON_RESULT"]

        if mode == "build_verify_simulate":
            sim_args = dict(tmpl.get("simulate_args") or {})
            if "duration_s" not in sim_args:
                sim_args["duration_s"] = 60
            t0 = time.time()
            res = await execute_tool_call("simulate_traversal_check", sim_args)
            out["sim_ms"] = int((time.time() - t0) * 1000)
            sim_out = (res.get("output") or "").strip()
            json_lines = [l for l in sim_out.splitlines() if l.strip().startswith("{")]
            if json_lines:
                d = json.loads(json_lines[-1])
                out["func_ok"] = bool(d.get("success"))
                out["cube_final"] = d.get("cube_final")
                out["in_target_xy"] = d.get("in_target_xy")
                out["above_floor"] = d.get("above_floor")
                out["at_rest"] = d.get("at_rest")
            else:
                out["func_ok"] = False

        out["ok"] = True
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
        out["traceback"] = traceback.format_exc()[-1500:]

    save_run(label, out)
    return out


def trigger_run(label: str, mode: str) -> dict:
    return asyncio.run(_do_run(label, mode))


# ── Rendering ────────────────────────────────────────────────────────────────

def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


PAGE_HEAD = """<!doctype html><meta charset=utf-8>
<title>Canonical Review</title>
<style>
:root {
  --bg: #0e0e10;
  --panel: #16161a;
  --panel2: #1a1a1f;
  --border: #28282e;
  --border-hi: #3a3a44;
  --fg: #d8d8de;
  --fg-dim: #888892;
  --fg-muted: #5a5a64;
  --accent: #79c8ff;
  --ok: #6ddc83;
  --fail: #ff7373;
  --warn: #f0b86c;
  --link: #99c8ff;
}
* { box-sizing: border-box; }
body {
  font-family: ui-monospace, SFMono-Regular, Menlo, "Cascadia Code", monospace;
  margin: 0; background: var(--bg); color: var(--fg); font-size: 13px;
  height: 100vh; display: flex; flex-direction: column;
}
header {
  background: var(--panel); padding: 0.5rem 1rem; border-bottom: 1px solid var(--border);
  display: flex; gap: 1rem; align-items: center; flex-shrink: 0;
}
header h1 { margin: 0; font-size: 14px; font-weight: 600; }
header .stats { color: var(--fg-dim); font-size: 12px; }
header .spacer { flex: 1; }
header kbd {
  background: var(--panel2); border: 1px solid var(--border-hi);
  padding: 1px 5px; border-radius: 3px; font-size: 11px; color: var(--fg-dim);
}
.layout { display: grid; grid-template-columns: 240px 1fr; flex: 1; min-height: 0; }
nav { background: var(--panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
nav .search-row { padding: 0.5rem; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 0.4rem; }
nav input.search {
  background: var(--bg); border: 1px solid var(--border); padding: 0.3rem 0.5rem;
  color: var(--fg); font-family: inherit; font-size: 12px; border-radius: 3px;
  outline: none;
}
nav input.search:focus { border-color: var(--accent); }
nav .filter-row { display: flex; gap: 0.3rem; flex-wrap: wrap; }
nav .filter {
  font-size: 10px; padding: 1px 6px; background: var(--panel2);
  border: 1px solid var(--border); border-radius: 10px; cursor: pointer;
  color: var(--fg-dim);
}
nav .filter.on { background: #1f3650; border-color: var(--accent); color: var(--accent); }
nav .list { overflow-y: auto; flex: 1; padding: 0.2rem 0; }
nav a {
  display: flex; align-items: center; padding: 0.32rem 0.7rem; color: var(--fg);
  text-decoration: none; border-left: 3px solid transparent; font-size: 12px;
  gap: 0.4rem;
}
nav a:hover { background: var(--panel2); }
nav a.sel { border-left-color: var(--accent); background: #18283a; color: #fff; }
nav a .label { flex: 1; }
nav a .badge { font-size: 10px; opacity: 0.8; margin-left: 0.3rem; }
nav a .badge.nbg { color: var(--fg-dim); margin-right: 0.2rem; }
.ok { color: var(--ok); }
.fail { color: var(--fail); }
.warn { color: var(--warn); }
.dim { color: var(--fg-dim); }
.muted { color: var(--fg-muted); }
main { padding: 1rem 1.5rem; overflow-y: auto; line-height: 1.5; }
h2 { margin: 0 0 0.2rem 0; font-size: 16px; font-weight: 600; }
.sub { color: var(--fg-dim); font-size: 11px; margin-bottom: 0.7rem; }
.gates { display: flex; gap: 0.6rem; margin: 0.6rem 0 0.8rem; flex-wrap: wrap; }
.gate {
  padding: 0.25rem 0.6rem; border-radius: 3px; font-size: 11px;
  border: 1px solid var(--border); background: var(--panel2);
  display: inline-flex; gap: 0.3rem; align-items: center;
}
.gate.ok { background: #143a23; border-color: #285c3c; color: #b3f0c0; }
.gate.fail { background: #3a1414; border-color: #5c2828; color: #ffc3c3; }
.gate.warn { background: #3a2c14; border-color: #5c4628; color: #ffd9a3; }
.gate b { font-weight: 600; }
.run-row {
  display: flex; gap: 0.5rem; align-items: center; margin: 0.6rem 0 1rem;
  flex-wrap: wrap;
}
button {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--border-hi);
  padding: 0.4rem 0.9rem; font-family: inherit; font-size: 12px;
  border-radius: 3px; cursor: pointer; line-height: 1.3;
}
button:hover { background: #24242a; border-color: #45454e; }
button:disabled { opacity: 0.45; cursor: progress; }
button.play { background: #103a1d; border-color: #2a6b3a; color: #b8eec5; }
button.play:hover { background: #154828; border-color: #3a8a4a; }
button.full { background: #1a3458; border-color: #3a5a8a; color: #b8d4ee; }
button.full:hover { background: #224369; }
.run-status { font-size: 12px; }
section { margin: 1rem 0; }
section h3 { margin: 0 0 0.4rem 0; font-size: 12px; font-weight: 600;
             color: var(--fg-dim); text-transform: uppercase; letter-spacing: 0.05em; }
details {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 4px; margin: 0.5rem 0; overflow: hidden;
}
details > summary {
  padding: 0.5rem 0.7rem; cursor: pointer; font-weight: 600;
  color: var(--accent); list-style: none; user-select: none;
  display: flex; gap: 0.4rem; align-items: center;
}
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: "▸"; transition: transform 0.15s; font-size: 10px; }
details[open] > summary::before { transform: rotate(90deg); }
details > summary:hover { background: var(--panel2); }
details > .body { padding: 0 0.7rem 0.7rem 1.6rem; }
pre {
  background: var(--bg); border: 1px solid var(--border); padding: 0.6rem;
  border-radius: 4px; overflow-x: auto; white-space: pre-wrap; word-break: break-word;
  font-size: 11.5px; line-height: 1.5; margin: 0.4rem 0;
}
ul.tools, ul.failures { padding-left: 1.2rem; margin: 0.4rem 0; }
ul.tools li, ul.failures li { font-size: 12px; padding: 0.1rem 0; }
ul.failures li { color: var(--warn); }
textarea {
  width: 100%; min-height: 84px; background: var(--bg); color: var(--fg);
  border: 1px solid var(--border); padding: 0.6rem; font-family: inherit;
  font-size: 12px; border-radius: 4px; resize: vertical; outline: none;
  line-height: 1.5;
}
textarea:focus { border-color: var(--accent); }
.tag-row { display: flex; flex-wrap: wrap; gap: 0.3rem; margin: 0.4rem 0; }
.chip {
  padding: 0.15rem 0.55rem; background: var(--panel2); border: 1px solid var(--border);
  border-radius: 12px; font-size: 11px; cursor: pointer; user-select: none;
  color: var(--fg-dim);
}
.chip.on { background: #1a3658; border-color: var(--accent); color: var(--accent); }
.chip:hover { border-color: var(--border-hi); }
.fb-entry, .run-entry {
  background: var(--panel); border: 1px solid var(--border);
  padding: 0.45rem 0.65rem; border-radius: 4px; margin: 0.35rem 0; font-size: 12px;
}
.fb-meta, .run-meta { color: var(--fg-dim); font-size: 10.5px; margin-bottom: 0.2rem; }
.toast {
  position: fixed; bottom: 1.5rem; right: 1.5rem; background: var(--panel);
  border: 1px solid var(--border-hi); padding: 0.5rem 0.9rem; border-radius: 4px;
  font-size: 12px; opacity: 0; transition: opacity 0.2s; z-index: 999; max-width: 30rem;
  border-left: 3px solid var(--accent);
}
.toast.show { opacity: 1; }
.toast.ok { border-left-color: var(--ok); }
.toast.fail { border-left-color: var(--fail); }
.copy-btn {
  font-size: 10px; padding: 1px 6px; background: transparent;
  border: 1px solid var(--border); color: var(--fg-dim); cursor: pointer;
  margin-left: 0.4rem; border-radius: 2px;
}
.copy-btn:hover { color: var(--accent); border-color: var(--accent); }
.kbd-hints {
  margin-left: 1rem; color: var(--fg-muted); font-size: 11px;
}
.kbd-hints kbd {
  background: var(--panel2); border: 1px solid var(--border);
  padding: 0px 4px; border-radius: 2px; font-size: 10px;
}
.wilson {
  display: inline-block; padding: 0.15rem 0.5rem; border-radius: 3px;
  background: var(--panel); border: 1px solid var(--border);
  font-size: 11px; color: var(--fg-dim);
}
.wilson b { color: var(--fg); }
</style>
"""


TAGS = ["perception", "precision", "physics", "reachability", "controller",
        "gripper", "timing", "asset-drift", "vision", "multi-robot", "regression"]


def render_nav_items(canonicals: list[str], audit: dict[str, dict],
                     selected: str) -> str:
    items = []
    for label in canonicals:
        cls = "sel" if label == selected else ""
        runs = load_runs(label)
        latest = runs[-1] if runs else None
        a = audit.get(label)
        # Status decisions: prefer most recent run, fall back to audit
        if latest and latest.get("func_ok") is True:
            status = "func-ok"
            badge = '<span class="badge ok">●</span>'
        elif latest and latest.get("form_ok") is True:
            status = "form-ok"
            badge = '<span class="badge warn">○</span>'
        elif latest and latest.get("instantiated") is False:
            status = "build-fail"
            badge = '<span class="badge fail">✗</span>'
        elif a and a.get("success"):
            status = "func-ok"
            badge = '<span class="badge ok">●</span>'
        elif a and a.get("success") is False:
            status = "form-ok"  # audit ran, gate failed but build worked
            badge = '<span class="badge fail">○</span>'
        else:
            status = "untested"
            badge = '<span class="badge muted">·</span>'
        # Feedback count adornment
        fb_count = len(load_feedback(label))
        if fb_count:
            badge = f'<span class="badge nbg">📝{fb_count}</span>' + badge
        items.append(
            f'<a class="{cls}" href="/?id={label}" data-status="{status}" data-id="{label}">'
            f'<span class="label">{label}</span>{badge}</a>'
        )
    return "\n".join(items)


def fmt_run(run: dict) -> str:
    parts = []
    if run.get("ts"):
        parts.append(run["ts"][11:19])
    if run.get("mode"):
        parts.append(run["mode"])
    inst = run.get("instantiated")
    if inst is True:
        parts.append(f'<span class="ok">build {run.get("n_ok")}/{run.get("n_calls")}</span>')
    elif inst is False:
        parts.append('<span class="fail">build fail</span>')
    if run.get("form_ok") is True:
        parts.append('<span class="ok">form ✓</span>')
    elif run.get("form_ok") is False:
        parts.append('<span class="fail">form ✗</span>')
    if run.get("func_ok") is True:
        parts.append('<span class="ok">func ✓</span>')
    elif run.get("func_ok") is False:
        parts.append('<span class="fail">func ✗</span>')
    bms = run.get("build_ms"); vms = run.get("verify_ms"); sms = run.get("sim_ms")
    timing = []
    if bms: timing.append(f'b={bms}ms')
    if vms: timing.append(f'v={vms}ms')
    if sms: timing.append(f's={sms}ms')
    if timing:
        parts.append(f'<span class="muted">{" ".join(timing)}</span>')
    return " · ".join(parts)


def render_main(label: str, audit: dict[str, dict]) -> str:
    tmpl = load_template(label)
    if tmpl is None:
        return f"<main><h2>{label}</h2><p class=fail>Template not found</p></main>"
    if "_load_error" in tmpl:
        return f"<main><h2>{label}</h2><p class=fail>JSON parse error: {esc(tmpl['_load_error'])}</p></main>"

    goal = tmpl.get("goal") or ""
    extends = tmpl.get("extends") or "(none)"
    tools = tmpl.get("tools_used") or []
    code = tmpl.get("code") or ""
    settle = json.dumps(tmpl.get("settle_state") or {}, indent=2)
    verify = json.dumps(tmpl.get("verify_args") or {}, indent=2)
    sim = json.dumps(tmpl.get("simulate_args") or {}, indent=2)
    failures = tmpl.get("failure_modes") or []
    verified_status = tmpl.get("verified_status") or "(unset)"
    extension_notes = tmpl.get("extension_notes") or ""
    thoughts = tmpl.get("thoughts") or ""

    summary = latest_run_summary(label, audit)
    last = summary["last"]
    audit_entry = summary["audit"]

    # Form-gate pill
    if summary["form_total"] > 0:
        lo = wilson_lower(summary["form_passes"], summary["form_total"])
        hi = wilson_upper(summary["form_passes"], summary["form_total"])
        if summary["form_passes"] == summary["form_total"]:
            form_class = "ok"
        elif summary["form_passes"] == 0:
            form_class = "fail"
        else:
            form_class = "warn"
        form_text = (f'form-gate <b>{summary["form_passes"]}/{summary["form_total"]}</b> '
                     f'<span class="muted">[{lo*100:.0f}%, {hi*100:.0f}%]</span>')
    else:
        form_class = "warn"
        form_text = 'form-gate <b>untested</b>'

    if summary["func_total"] > 0:
        lo = wilson_lower(summary["func_passes"], summary["func_total"])
        hi = wilson_upper(summary["func_passes"], summary["func_total"])
        if summary["func_passes"] == summary["func_total"]:
            func_class = "ok"
        elif summary["func_passes"] == 0:
            func_class = "fail"
        else:
            func_class = "warn"
        func_text = (f'function-gate <b>{summary["func_passes"]}/{summary["func_total"]}</b> '
                     f'<span class="muted">[{lo*100:.0f}%, {hi*100:.0f}%]</span>')
    elif audit_entry:
        if audit_entry.get("success"):
            func_class, func_text = "ok", 'function-gate <b>1/1</b> <span class="muted">(audit)</span>'
        else:
            func_class, func_text = "fail", 'function-gate <b>0/1</b> <span class="muted">(audit)</span>'
    else:
        func_class = "warn"
        func_text = 'function-gate <b>untested</b>'

    tools_html = "\n".join(f"<li>{esc(t)}</li>" for t in tools)
    failures_html = "\n".join(f"<li>{esc(f)}</li>" for f in failures)

    feedback = load_feedback(label)
    fb_html = ""
    for entry in reversed(feedback[-15:]):
        ts = esc(entry.get("ts", "?"))
        author = esc(entry.get("author", "?"))
        tags = ", ".join(esc(t) for t in (entry.get("tags") or []))
        text = esc(entry.get("text") or "")
        fb_html += (
            f'<div class="fb-entry"><div class="fb-meta">{ts} · {author}'
            f'{" · " + tags if tags else ""}</div>'
            f'<div>{text}</div></div>'
        )

    runs = load_runs(label)
    runs_html = ""
    for r in reversed(runs[-8:]):
        runs_html += f'<div class="run-entry">{fmt_run(r)}</div>'

    chips_html = "\n".join(
        f'<span class="chip" data-tag="{t}" onclick="toggleChip(this)">{t}</span>'
        for t in TAGS
    )

    code_id = f"code-{label}"

    return f"""<main id="main">
<h2 id="title">{esc(label)} — {esc(goal[:120])}</h2>
<div class="sub">extends: <span class="dim">{esc(extends)}</span> · {esc(verified_status)}</div>

<div class="gates">
  <span class="gate {form_class}">{form_text}</span>
  <span class="gate {func_class}">{func_text}</span>
</div>

<div class="run-row">
  <button class="play" onclick="run('build')">▶ Build</button>
  <button onclick="run('build_verify')">▶ Build + Verify</button>
  <button class="full" onclick="run('build_verify_simulate')">▶ Build + Verify + Simulate</button>
  <span id="run-status" class="run-status muted">idle</span>
</div>

<details open><summary>Goal</summary>
<div class="body" style="font-size:13px;line-height:1.6;color:var(--fg)">{esc(goal)}</div>
</details>

{f'<details><summary>Thoughts ({len(thoughts)} chars)</summary><div class="body"><pre>{esc(thoughts)}</pre></div></details>' if thoughts else ''}

<details><summary>Tool chain ({len(tools)} tools)</summary>
<div class="body"><ul class="tools">{tools_html}</ul></div></details>

<details><summary>Code ({len(code)} chars) <button class="copy-btn" onclick="copyCode(event, '{code_id}')">copy</button></summary>
<div class="body"><pre id="{code_id}">{esc(code)}</pre></div></details>

<details><summary>Settle state</summary><div class="body"><pre>{esc(settle)}</pre></div></details>
<details><summary>Verify args</summary><div class="body"><pre>{esc(verify)}</pre></div></details>
<details><summary>Simulate args</summary><div class="body"><pre>{esc(sim)}</pre></div></details>

<details><summary>Failure modes ({len(failures)})</summary>
<div class="body"><ul class="failures">{failures_html}</ul></div></details>

{f'<details><summary>Extension notes</summary><div class="body" style="font-size:12px">{esc(extension_notes)}</div></details>' if extension_notes else ''}

<section>
<h3>Run history ({len(runs)})</h3>
{runs_html or '<div class="dim">no runs yet — click ▶ Build above</div>'}
</section>

<section>
<h3>Feedback</h3>
<div class="tag-row">{chips_html}</div>
<textarea id="fb-text" placeholder="Notes — what's broken, what's surprising, what to try next…"></textarea>
<div class="run-row" style="margin-top:0.5rem">
  <button onclick="saveFeedback()">💾 Save feedback</button>
  <span class="kbd-hints"><kbd>Cmd+Enter</kbd> to save</span>
</div>
<div style="margin-top:0.7rem">{fb_html if fb_html else '<div class="dim">no feedback yet</div>'}</div>
</section>

<div id="toast" class="toast"></div>

<script>
const LABEL = {json.dumps(label)};

function toggleChip(el) {{ el.classList.toggle('on'); }}

function copyCode(evt, id) {{
  evt.preventDefault();
  evt.stopPropagation();
  const el = document.getElementById(id);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent || '').then(() => toast('Copied', 'ok'));
}}

function toast(msg, cls) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (cls ? ' ' + cls : '');
  clearTimeout(window._toastT);
  window._toastT = setTimeout(() => t.className = 'toast', 2400);
}}

async function run(mode) {{
  const status = document.getElementById('run-status');
  const buttons = document.querySelectorAll('.run-row button');
  buttons.forEach(b => b.disabled = true);
  status.innerHTML = '<span class="muted">running ' + mode + '…</span>';
  try {{
    const r = await fetch('/run/' + LABEL + '?mode=' + mode, {{ method: 'POST' }});
    const j = await r.json();
    let parts = [];
    if (j.instantiated) parts.push('<span class="ok">build ' + j.n_ok + '/' + j.n_calls + '</span>');
    else parts.push('<span class="fail">build failed</span>');
    if (j.form_ok === true) parts.push('<span class="ok">form ✓</span>');
    if (j.form_ok === false) parts.push('<span class="fail">form ✗</span>');
    if (j.func_ok === true) parts.push('<span class="ok">func ✓</span>');
    if (j.func_ok === false) parts.push('<span class="fail">func ✗</span>');
    status.innerHTML = parts.join(' · ') || '<span class="fail">' + (j.error || 'unknown') + '</span>';
    toast(j.ok ? 'Run complete' : ('Failed: ' + (j.error || '')), j.ok ? 'ok' : 'fail');
    setTimeout(() => location.reload(), 800);
  }} catch (e) {{
    status.innerHTML = '<span class="fail">' + e + '</span>';
    toast('Error: ' + e, 'fail');
  }} finally {{
    buttons.forEach(b => b.disabled = false);
  }}
}}

async function saveFeedback() {{
  const text = document.getElementById('fb-text').value.trim();
  if (!text) {{ toast('Empty feedback'); return; }}
  const tags = Array.from(document.querySelectorAll('.chip.on')).map(c => c.dataset.tag);
  try {{
    const r = await fetch('/feedback/' + LABEL, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ text, tags }}),
    }});
    if (r.ok) {{
      document.getElementById('fb-text').value = '';
      document.querySelectorAll('.chip.on').forEach(c => c.classList.remove('on'));
      toast('Saved', 'ok');
      setTimeout(() => location.reload(), 500);
    }} else {{ toast('Save failed', 'fail'); }}
  }} catch (e) {{ toast('Error: ' + e, 'fail'); }}
}}

// ── Keyboard shortcuts + nav filter ─────────────────────────────────────
document.addEventListener('keydown', (e) => {{
  // Cmd/Ctrl-Enter on textarea = save
  if (e.target.id === 'fb-text' && (e.metaKey || e.ctrlKey) && e.key === 'Enter') {{
    e.preventDefault(); saveFeedback(); return;
  }}
  // Don't intercept when typing
  if (['TEXTAREA', 'INPUT'].includes(e.target.tagName)) return;

  if (e.key === '/') {{ e.preventDefault(); document.getElementById('search').focus(); return; }}
  if (e.key === 'j' || e.key === 'k') {{
    e.preventDefault();
    const links = Array.from(document.querySelectorAll('nav a:not([style*="display: none"])'));
    const cur = links.findIndex(a => a.classList.contains('sel'));
    const next = e.key === 'j' ? Math.min(links.length - 1, cur + 1) : Math.max(0, cur - 1);
    if (links[next]) location.href = links[next].href;
  }}
  if (e.key === 'b') {{ run('build'); }}
  if (e.key === 'v') {{ run('build_verify'); }}
  if (e.key === 'f') {{ run('build_verify_simulate'); }}
}});

function applyFilter() {{
  const q = (document.getElementById('search').value || '').toLowerCase().trim();
  const activeStatuses = Array.from(document.querySelectorAll('nav .filter.on'))
    .map(f => f.dataset.status);
  document.querySelectorAll('nav a').forEach(a => {{
    const id = (a.dataset.id || '').toLowerCase();
    const status = a.dataset.status || '';
    const matchQ = !q || id.includes(q);
    const matchS = activeStatuses.length === 0 || activeStatuses.includes(status);
    a.style.display = (matchQ && matchS) ? '' : 'none';
  }});
}}
document.querySelectorAll('nav .filter').forEach(f => {{
  f.addEventListener('click', () => {{ f.classList.toggle('on'); applyFilter(); }});
}});
const sb = document.getElementById('search');
if (sb) sb.addEventListener('input', applyFilter);

// Auto-scroll the nav so the selected canonical is visible (when navigating
// from the URL or via j/k).
const sel = document.querySelector('nav a.sel');
if (sel) sel.scrollIntoView({{ block: 'nearest' }});

// Browser-side prefetch on hover — clicking a different canonical loads
// faster because the page is already in the HTTP cache.
document.querySelectorAll('nav a').forEach(a => {{
  a.addEventListener('mouseenter', () => {{
    const link = document.createElement('link');
    link.rel = 'prefetch';
    link.href = a.href;
    document.head.appendChild(link);
  }}, {{ once: true }});
}});
</script>
</main>
"""


def build_report() -> dict:
    """Aggregate every canonical + audit + runs + feedback into one JSON
    blob. Useful for offline analysis (export to CSV, plot trends, etc.).
    """
    audit = load_func_gate_audit()
    out = {"generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
           "templates_dir": str(TEMPLATES_DIR), "canonicals": []}
    for label in list_canonicals():
        tmpl = load_template(label) or {}
        runs = load_runs(label)
        fb = load_feedback(label)
        s = latest_run_summary(label, audit)
        out["canonicals"].append({
            "id": label,
            "goal": (tmpl.get("goal") or "")[:300],
            "extends": tmpl.get("extends"),
            "verified_status": tmpl.get("verified_status"),
            "tools_used": tmpl.get("tools_used") or [],
            "n_tools": len(tmpl.get("tools_used") or []),
            "n_failure_modes": len(tmpl.get("failure_modes") or []),
            "form_passes": s["form_passes"],
            "form_total": s["form_total"],
            "form_wilson": [wilson_lower(s["form_passes"], s["form_total"]),
                            wilson_upper(s["form_passes"], s["form_total"])] if s["form_total"] else None,
            "func_passes": s["func_passes"],
            "func_total": s["func_total"],
            "func_wilson": [wilson_lower(s["func_passes"], s["func_total"]),
                            wilson_upper(s["func_passes"], s["func_total"])] if s["func_total"] else None,
            "audit_func_ok": s["audit"].get("success") if s["audit"] else None,
            "n_runs": len(runs),
            "n_feedback": len(fb),
        })
    return out


def render_summary() -> str:
    audit = load_func_gate_audit()
    canonicals = list_canonicals()
    rows = []
    counters = {"func-ok": 0, "form-ok": 0, "build-fail": 0, "untested": 0}
    for label in canonicals:
        runs = load_runs(label)
        latest = runs[-1] if runs else None
        a = audit.get(label)
        if latest and latest.get("func_ok") is True:
            cls, status = "ok", "func ✓"
        elif latest and latest.get("form_ok") is True:
            cls, status = "warn", "form ✓"
        elif latest and latest.get("instantiated") is False:
            cls, status = "fail", "build ✗"
        elif a and a.get("success"):
            cls, status = "ok", "func ✓"
        elif a:
            cls, status = "fail", "func ✗"
        else:
            cls, status = "muted", "untested"
        if cls == "ok": counters["func-ok"] += 1
        elif cls == "warn": counters["form-ok"] += 1
        elif cls == "fail": counters["build-fail"] += 1
        else: counters["untested"] += 1
        s = latest_run_summary(label, audit)
        n_fb = len(load_feedback(label))
        n_runs = s["runs"]
        wilson_str = ""
        if s["func_total"] >= 1:
            lo = wilson_lower(s["func_passes"], s["func_total"]) * 100
            hi = wilson_upper(s["func_passes"], s["func_total"]) * 100
            wilson_str = f'[{lo:.0f}%, {hi:.0f}%]'
        rows.append(
            f'<tr><td><a href="/?id={label}">{label}</a></td>'
            f'<td><span class="cell-{cls}">{status}</span></td>'
            f'<td class="muted">{n_runs}</td>'
            f'<td class="muted">{n_fb}</td>'
            f'<td class="muted">{wilson_str}</td>'
            f'<td>{esc(((load_template(label) or {{}}).get("goal") or "")[:90])}</td></tr>'
        )
    counter_html = (f'<span class="ok">● {counters["func-ok"]} func-ok</span>'
                    f' · <span class="warn">○ {counters["form-ok"]} form-ok</span>'
                    f' · <span class="fail">✗ {counters["build-fail"]} build-fail</span>'
                    f' · <span class="muted">· {counters["untested"]} untested</span>')
    return PAGE_HEAD + f"""<body>
<header><h1>Canonical Summary</h1>
<span class="stats">{len(canonicals)} canonicals · {counter_html}</span>
<span class="spacer"></span>
<a style="color:var(--link)" href="/">← back to review</a>
</header>
<style>
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th, td {{ padding: 0.3rem 0.6rem; border-bottom: 1px solid var(--border);
            text-align: left; }}
  th {{ color: var(--fg-dim); font-weight: 600; text-transform: uppercase;
        font-size: 10.5px; letter-spacing: 0.05em; background: var(--panel); }}
  .cell-ok {{ color: var(--ok); }}
  .cell-warn {{ color: var(--warn); }}
  .cell-fail {{ color: var(--fail); }}
  .cell-muted {{ color: var(--fg-muted); }}
  td a {{ color: var(--link); text-decoration: none; }}
  td a:hover {{ text-decoration: underline; }}
</style>
<main style="padding:0">
<table>
<thead><tr><th>ID</th><th>Status</th><th># runs</th><th># notes</th>
  <th>Wilson 95% (func)</th><th>Goal</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
</main></body>
"""
    canonicals = list_canonicals()
    if not canonicals:
        return PAGE_HEAD + f"<body><h1>No canonicals in {esc(str(TEMPLATES_DIR))}</h1></body>"
    if selected not in canonicals:
        selected = canonicals[0]
    audit = load_func_gate_audit()
    func_audit_passes = sum(1 for v in audit.values() if v.get("success"))

    # Compute a global summary
    summaries = [latest_run_summary(c, audit) for c in canonicals]
    total_runs = sum(s["runs"] for s in summaries)
    func_passes = sum(s["func_passes"] for s in summaries)
    func_total = sum(s["func_total"] for s in summaries)

    head_stats = (
        f'<span class="stats">'
        f'{len(canonicals)} canonicals · '
        f'audit {func_audit_passes}/{len(audit)} func ✓ · '
        f'logged runs {func_passes}/{func_total} func ✓ ({total_runs} total)'
        f'</span>'
    )

    return (
        PAGE_HEAD
        + f'<body><header><h1>Canonical Review</h1>{head_stats}'
        + '<span class="spacer"></span>'
        + '<a href="/summary" style="color:var(--link);font-size:12px">summary ↗</a>'
        + '<span class="kbd-hints">'
        + '<kbd>/</kbd> search · <kbd>j</kbd>/<kbd>k</kbd> next/prev · '
        + '<kbd>b</kbd>/<kbd>v</kbd>/<kbd>f</kbd> build/verify/full'
        + '</span></header>'
        + '<div class="layout">'
        + '<nav>'
        + '<div class="search-row">'
        + '<input id="search" class="search" placeholder="Filter (e.g. CP-08, ur10)" />'
        + '<div class="filter-row">'
        + '<span class="filter" data-status="func-ok">func ✓</span>'
        + '<span class="filter" data-status="form-ok">form ✓</span>'
        + '<span class="filter" data-status="build-fail">build ✗</span>'
        + '<span class="filter" data-status="untested">untested</span>'
        + '</div></div>'
        + f'<div class="list">{render_nav_items(canonicals, audit, selected)}</div>'
        + '</nav>'
        + render_main(selected, audit)
        + "</div></body>"
    )


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Log only POSTs (runs/feedback) to stderr
        if self.command == "POST":
            sys.stderr.write(f"[viewer] {self.command} {self.path}\n")

    def _send_html(self, body: str, code: int = 200):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _send_json(self, obj, code: int = 200):
        b = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index"):
            qs = parse_qs(u.query)
            selected = (qs.get("id") or [None])[0]
            self._send_html(render_page(selected))
            return
        if u.path == "/summary":
            self._send_html(render_summary())
            return
        if u.path == "/api/list":
            self._send_json({"canonicals": list_canonicals()})
            return
        if u.path == "/api/runs":
            qs = parse_qs(u.query)
            label = (qs.get("id") or [None])[0]
            if not label:
                self._send_json({"error": "id required"}, 400)
                return
            self._send_json({"runs": load_runs(label)})
            return
        if u.path == "/api/audit":
            self._send_json({"audit": load_func_gate_audit()})
            return
        if u.path == "/api/report":
            self._send_json(build_report())
            return
        self._send_html("<h1>404</h1>", 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith("/run/"):
            label = u.path[len("/run/"):]
            qs = parse_qs(u.query)
            mode = (qs.get("mode") or ["build"])[0]
            if mode not in ("build", "build_verify", "build_verify_simulate"):
                self._send_json({"ok": False, "error": f"invalid mode {mode}"}, 400)
                return
            res = trigger_run(label, mode)
            self._send_json(res)
            return
        if u.path.startswith("/feedback/"):
            label = u.path[len("/feedback/"):]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
            entry = {
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "author": getpass.getuser(),
                "text": (payload.get("text") or "")[:8000],
                "tags": list(payload.get("tags") or [])[:20],
            }
            save_feedback(label, entry)
            self._send_json({"ok": True})
            return
        self._send_json({"error": "not found"}, 404)


def main():
    print("Canonical Review Interface")
    print(f"  templates: {TEMPLATES_DIR}")
    print(f"  feedback:  {FEEDBACK_DIR}")
    print(f"  runs:      {RUNS_DIR}")
    print(f"  func_gate: {FUNC_GATE_LOG}")
    print(f"  serving:   http://127.0.0.1:{PORT}/")
    print(f"")
    print("  /             single-page UI")
    print("  /api/list     JSON: canonicals[]")
    print("  /api/runs?id= JSON: per-canonical run history")
    print("  /api/audit    JSON: func-gate audit log")
    print("")
    HTTPServer = ThreadingHTTPServer  # threading so a /run can stream while UI loads
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
