#!/usr/bin/env python3
"""Autonomous visual QA for ingested assets (BACKLOG #0).

Runs a machine-checkable approval rubric over an entry's orbit renders
using LOCAL vision judges — Cosmos-Reason2 (physical plausibility, served
by vLLM) and Gemma (identity/integrity, served by Ollama) — with cloud
Claude as an optional tiebreak when the locals disagree. Every check
writes its evidence onto the entry (`visual_qa` block).

Verdicts are FAIL-CLOSED: anything short of unanimous healthy-judge
agreement on identity and integrity, plus a clean mechanical rubric,
routes to the human review hub. The machine never certifies what it
cannot measure.

Usage:
    python scripts/visual_qa.py <asset_id> [...] [--all-pending]

Env:
    VISUAL_QA_COSMOS_URL   default http://127.0.0.1:8021/v1
    VISUAL_QA_COSMOS_MODEL default nvidia/Cosmos-Reason2-2B
    VISUAL_QA_GEMMA_MODEL  default gemma4
    OLLAMA_URL             default http://127.0.0.1:11434
    ANTHROPIC_API_KEY      enables the Claude tiebreak
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_asset import QUEUE_DIR  # noqa: E402

PRIORS_PATH = REPO / "workspace" / "knowledge" / "asset_class_priors.json"

COSMOS_URL = os.environ.get("VISUAL_QA_COSMOS_URL", "http://127.0.0.1:8021/v1")
COSMOS_MODEL = os.environ.get("VISUAL_QA_COSMOS_MODEL", "nvidia/Cosmos-Reason2-2B")
GEMMA_MODEL = os.environ.get("VISUAL_QA_GEMMA_MODEL", "gemma4")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

def _judge_schema() -> dict:
    """Judge output contract. asset_class is CONSTRAINED to the prior class
    keys via structured output — small local models pick reliably from an
    enum where they free-associate badly from prose."""
    keys = list(json.loads(PRIORS_PATH.read_text())["classes"])
    return {
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "asset_class": {"anyOf": [{"type": "string", "enum": keys},
                                      {"type": "null"}],
                            "description": "Best matching class key, or null"},
            "confidence": {"type": "number",
                           "description": "0..1 (1 = certain)"},
            "integrity_ok": {"type": "boolean"},
            "integrity_notes": {"type": "string"},
        },
        "required": ["object_name", "asset_class", "confidence",
                     "integrity_ok", "integrity_notes"],
        "additionalProperties": False,
    }


def _class_list() -> str:
    classes = json.loads(PRIORS_PATH.read_text())["classes"]
    return "\n".join(
        f"- {k}: keywords {v['keywords']}, plausible max dimension "
        f"{v['max_dim_m'][0]}-{v['max_dim_m'][1]} m"
        for k, v in classes.items())


def _prompt(dims_m: list[float] | None = None) -> str:
    # renders carry no absolute scale — the measured size is evidence the
    # judges must have (a 6 cm 'bottle' shape is a vial)
    dims = ""
    if dims_m:
        dims = ("The asset's measured bounding box is "
                f"{dims_m[0]:.3f} x {dims_m[1]:.3f} x {dims_m[2]:.3f} m. ")
    return (
        "These are orbit views of one 3D asset rendered for physics-"
        "simulation QA (untextured gray is a renderer limitation, not a "
        "defect). " + dims +
        "Identify the object, pick the best matching class key "
        "from this list (null if none fits), and judge mesh integrity: "
        "missing faces, holes, exploded or floating parts, implausible "
        "proportions.\n\n" + _class_list())


def _b64(path: str) -> str:
    return base64.standard_b64encode(Path(path).read_bytes()).decode()


def _post_json(url: str, payload: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in judge reply: {text[:120]!r}")
    return json.loads(m.group(0))


def judge_gemma(views: list[str], prompt: str) -> dict:
    out = _post_json(f"{OLLAMA_URL}/api/chat", {
        "model": GEMMA_MODEL, "stream": False,
        "messages": [{"role": "user", "content": prompt,
                      "images": [_b64(v) for v in views]}],
        "format": _judge_schema(),
        "options": {"temperature": 0},
    })
    result = json.loads(out["message"]["content"])
    result["judge"] = "gemma"
    result["model"] = GEMMA_MODEL
    return result


def judge_cosmos(views: list[str], prompt: str) -> dict:
    content = [{"type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{_b64(v)}"}}
               for v in views]
    content.append({"type": "text", "text": prompt + (
        "\n\nAnswer as a JSON object with keys: object_name (string), "
        "asset_class (string or null), confidence (0..1), integrity_ok "
        "(boolean), integrity_notes (string).")})
    payload = {
        "model": COSMOS_MODEL, "temperature": 0, "max_tokens": 1024,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "visual_qa", "schema": _judge_schema()}},
    }
    try:
        out = _post_json(f"{COSMOS_URL}/chat/completions", payload)
    except Exception:
        payload.pop("response_format")  # older vLLMs: parse from free text
        out = _post_json(f"{COSMOS_URL}/chat/completions", payload)
    result = _extract_json(out["choices"][0]["message"]["content"])
    result["judge"] = "cosmos"
    result["model"] = COSMOS_MODEL
    return result


def judge_claude(views: list[str], prompt: str) -> dict:
    import anthropic

    content = [{"type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": _b64(v)}} for v in views]
    content.append({"type": "text", "text": prompt})
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5", max_tokens=16000,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema",
                                  "schema": _judge_schema()}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the QA request")
    result = json.loads(
        next(b.text for b in response.content if b.type == "text"))
    result["judge"] = "claude"
    result["model"] = "claude-opus-5"
    return result


def _run_judges(views: list[str], prompt: str,
                entry_class: str | None) -> list[dict]:
    """Both local judges; Claude joins when they disagree with each other
    OR unanimously contradict the entry's class — either way a third
    opinion is what settles it."""
    judges: list[dict] = []
    for fn in (judge_gemma, judge_cosmos):
        try:
            judges.append(fn(views, prompt))
        except Exception as e:
            judges.append({"judge": fn.__name__.split("_")[1],
                           "error": str(e)[:200]})
    healthy = [j for j in judges if "error" not in j]
    classes = {j.get("asset_class") for j in healthy}
    integrity = {bool(j.get("integrity_ok")) for j in healthy}
    disagree = len(healthy) == 2 and (len(classes) > 1 or len(integrity) > 1)
    against_entry = bool(healthy) and classes != {entry_class}
    if ((disagree or against_entry or len(healthy) < 2)
            and os.environ.get("ANTHROPIC_API_KEY")):
        try:
            judges.append(judge_claude(views, prompt))
        except Exception as e:
            judges.append({"judge": "claude", "error": str(e)[:200]})
    return judges


def rubric(entry: dict, judges: list[dict]) -> list[dict]:
    """Named checks with evidence. Every failure is a reason a human sees."""
    checks: list[dict] = []
    report = entry.get("report", {})
    structure = report.get("structure", {})
    callouts = report.get("callouts", [])
    entry_class = report.get("matched_class") or entry.get("class_hint")
    healthy = [j for j in judges if "error" not in j]

    checks.append({
        "check": "judges_healthy",
        "ok": len(healthy) >= 2,
        "evidence": f"{len(healthy)} healthy judges of {len(judges)}: "
                    + ", ".join(j["judge"] + (" ERR" if "error" in j else "")
                                for j in judges)})

    prior_kw = [str(k).lower() for k in json.loads(
        PRIORS_PATH.read_text())["classes"].get(entry_class or "", {}).get(
        "keywords", [])]

    def _identity_vote(j: dict) -> bool:
        # class-key match, or the judge NAMED the object with one of the
        # entry class's keywords (a sibling class key with the right name
        # is agreement, not disagreement)
        if j.get("asset_class") == entry_class:
            return True
        tokens = re.split(r"[\W_]+", str(j.get("object_name", "")).lower())
        return any(k in tokens for k in prior_kw)

    votes = [j for j in healthy if _identity_vote(j)]
    checks.append({
        "check": "identity_agreement",
        "ok": len(healthy) >= 2 and len(votes) == len(healthy)
              and entry_class is not None,
        "evidence": f"entry class '{entry_class}' vs "
                    + ", ".join(f"{j['judge']}:'{j.get('asset_class')}'"
                                f"/'{j.get('object_name')}'"
                                f"({j.get('confidence', 0):.2f})"
                                for j in healthy)})

    bad = [j for j in healthy if not j.get("integrity_ok")]
    checks.append({
        "check": "integrity",
        "ok": len(healthy) >= 2 and not bad,
        "evidence": "; ".join(f"{j['judge']}: {j.get('integrity_notes', '')[:120]}"
                              for j in healthy) or "no healthy judges"})

    scale_callouts = [c for c in callouts
                      if "scale" in str(c).lower() or "size" in str(c).lower()]
    checks.append({
        "check": "scale_in_prior",
        "ok": not scale_callouts,
        "evidence": str(scale_callouts) if scale_callouts
                    else "no scale/size callouts after fixes"})

    masses = structure.get("authored_masses") or []
    prior = json.loads(PRIORS_PATH.read_text())["classes"].get(entry_class or "", {})
    mass_range = prior.get("mass_kg")
    mass_ok = bool(masses)
    mass_note = f"authored masses: {masses}"
    if masses and mass_range:
        total = sum(m.get("mass_kg", 0) for m in masses)
        mass_ok = mass_range[0] <= total <= mass_range[1]
        mass_note += f"; class prior {mass_range} kg"
    checks.append({
        "check": "physics_ready",
        "ok": bool(structure.get("rigid_bodies"))
              and bool(structure.get("collision_prims"))
              and bool(structure.get("material_bindings")) and mass_ok,
        "evidence": f"rigid_bodies={structure.get('rigid_bodies')}, "
                    f"collision={structure.get('collision_prims')}, "
                    f"material_bindings={structure.get('material_bindings')}, "
                    + mass_note})

    errors = [c for c in callouts if isinstance(c, dict)
              and c.get("severity") == "error"]
    checks.append({
        "check": "no_error_callouts",
        "ok": not errors,
        "evidence": str(errors) if errors else "no error-severity callouts"})

    category = entry.get("proposed_category", "")
    checks.append({
        "check": "rigid_scope",
        "ok": category in ("rigid_unverified", "rigid_verified"),
        "evidence": f"category '{category}' — machines may only auto-approve "
                    "rigid assets; articulated always goes to the human, and "
                    "rigid_only_baked is by policy not sim ready"})
    return checks


def _measure_dims(file_path: str | None) -> list[float] | None:
    """World bbox of the CURRENT derivative, in meters (needs pxr)."""
    if not file_path or not Path(file_path).exists():
        return None
    try:
        from pxr import Usd, UsdGeom
        st = Usd.Stage.Open(file_path)
        rng = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        ).ComputeWorldBound(st.GetPseudoRoot()).ComputeAlignedRange()
        if rng.IsEmpty():
            return None
        mpu = UsdGeom.GetStageMetersPerUnit(st)
        s = rng.GetSize()
        return [s[0] * mpu, s[1] * mpu, s[2] * mpu]
    except Exception:
        return None


def qa_entry(asset_id: str) -> str:
    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    views = [v for v in (entry.get("views") or []) if Path(v).exists()]
    if not views:
        thumb = entry.get("thumbnail")
        if thumb and Path(thumb).exists():
            views = [thumb]
    if not views:
        return f"{asset_id}: no renders — re-ingest to produce orbit views"

    report = entry.get("report", {})
    entry_class = report.get("matched_class") or entry.get("class_hint")
    dims = report.get("dimensions_m") or _measure_dims(entry.get("file"))
    judges = _run_judges(views, _prompt(dims), entry_class)
    checks = rubric(entry, judges)
    failed = [c["check"] for c in checks if not c["ok"]]
    verdict = "auto_approve_eligible" if not failed else "human_review"
    entry["visual_qa"] = {
        "verdict": verdict,
        "date": date.today().isoformat(),
        "views_judged": len(views),
        "judges": judges,
        "checks": checks,
        "failed_checks": failed,
    }
    qf.write_text(json.dumps(entry, indent=1))
    names = "+".join(j["judge"] for j in judges if "error" not in j)
    return (f"{asset_id}: {verdict} ({names} on {len(views)} views)"
            + (f" — failed: {', '.join(failed)}" if failed else ""))


def auto_approve_entry(asset_id: str) -> str:
    """Machine sign-off: only after an auto_approve_eligible verdict, only
    rigid categories (the schema also enforces this), reviewer recorded as
    the QA pipeline with its model list. Every Nth machine approval is
    flagged audit_sampled so the hub pulls it in front of a human."""
    from asset_review_hub import do_approve, load_registry

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    vq = entry.get("visual_qa") or {}
    if vq.get("verdict") != "auto_approve_eligible":
        return f"{asset_id}: not eligible ({vq.get('verdict', 'no visual_qa run')})"
    category = entry.get("proposed_category")
    if category not in ("rigid_unverified", "rigid_verified"):
        return f"{asset_id}: category {category} is outside machine scope"
    models = sorted(j["model"] for j in vq.get("judges", [])
                    if "error" not in j)
    every = max(1, int(os.environ.get("VISUAL_QA_AUDIT_EVERY", "5")))
    prior_machine = sum(
        1 for a in load_registry().get("assets", [])
        if a.get("review", {}).get("reviewer_type") == "machine")
    sampled = (prior_machine + 1) % every == 0
    msg = do_approve(
        entry, category, "visual-qa-v1",
        f"machine approval on {vq.get('views_judged')} views; "
        f"checks: {', '.join(c['check'] for c in vq.get('checks', []))}",
        review_extra={"reviewer_type": "machine", "models": models,
                      **({"audit_sampled": True} if sampled else {})})
    return f"{asset_id}: {msg}" + (" [AUDIT SAMPLED]" if sampled else "")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--all-pending" in sys.argv:
        args = sorted(
            p.stem for p in QUEUE_DIR.glob("*.json")
            if json.loads(p.read_text()).get("status") == "pending_review")
    if not args:
        print(__doc__)
        return 1
    approve = "--approve" in sys.argv
    failures = 0
    for asset_id in args:
        try:
            line = qa_entry(asset_id)
            print(line)
            if approve and "auto_approve_eligible" in line:
                print(auto_approve_entry(asset_id))
        except Exception as e:
            failures += 1
            print(f"ERROR {asset_id}: {str(e)[:200]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
