#!/usr/bin/env python3
"""Passively critique a completed robot episode with a local vision model.

The critic is deliberately off the control path: it reads saved frames and a
trace after the simulator exits.  Its full review is retained as evidence,
while only short, phase-scoped semantic lessons are written to the memory file
that the Gemini coach may read on the next episode.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "gemini_robotics_er2_robolab"
VALID_PHASES = {
    "global",
    "approach_banana",
    "descend",
    "grasp",
    "lift",
    "above_plate",
    "release",
}
# Dynamic memory is semantic guidance only.  Low-level actuation remains the
# responsibility of the validated local trajectory executor.
DISALLOWED_GUIDANCE = re.compile(
    r"\b(joint(?:s| angle)?|radians?|degrees?|torque|velocity|motor|actuator)\b",
    re.IGNORECASE,
)
COACH_GATE_TEMPLATES: dict[str, tuple[str, str]] = {
    "approach_banana": (
        "Compare the gripper approach position with the visible banana center.",
        "Execute the next phase only after a fresh observation confirms the approach is aligned; otherwise retry or abort.",
    ),
    "descend": (
        "Verify the fingertip center is aligned with the banana and has safe vertical clearance.",
        "Execute grasp only when fresh visual and measured alignment agree; otherwise retry or abort.",
    ),
    "grasp": (
        "Verify the jaws visibly enclose the banana and measured closure is consistent with contact.",
        "Execute lift only when the grasp gate passes; otherwise retry the grasp observation or abort.",
    ),
    "lift": (
        "Verify the banana moved upward with the gripper in the fresh post-lift observation.",
        "Execute transport only after measured physical lift passes; otherwise abort.",
    ),
    "above_plate": (
        "Track the carried banana relative to the visible plate center during transport.",
        "Execute transport, then require a fresh observation to verify centering before allowing release.",
    ),
    "release": (
        "Verify the banana center is visibly inside the plate footprint and the gripper still holds it.",
        "Execute release only when the fresh observation confirms centering; otherwise retry the observation or abort.",
    ),
    "global": (
        "Compare the final visible object relation with the geometric task outcome.",
        "Accept success only when visual and measured outcomes agree; otherwise mark the episode failed.",
    ),
}


def _json_object(text: str) -> dict[str, Any]:
    """Extract the outermost JSON object from reasoning-model output."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"critic returned no JSON object: {text[:240]!r}")
    return json.loads(text[start : end + 1])


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"critic HTTP {error.code}: {detail}") from error


def _ordered_frames(artifact_dir: Path) -> list[Path]:
    candidates = sorted(artifact_dir.glob("[0-9][0-9]_*.jpg"))
    return [path for path in candidates if path.is_file()]


def _image_part(path: Path) -> dict[str, Any]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((448, 448), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=True)
    encoded = base64.standard_b64encode(buffer.getvalue()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
    }


def _compact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Keep authoritative decisions/metrics without spending vision context."""
    if trace.get("schema_version") != 1:
        return trace
    stages: list[dict[str, Any]] = []
    for stage in trace.get("stages", []):
        if not isinstance(stage, dict):
            continue
        decision = stage.get("coach_decision", {})
        stages.append({
            "phase": stage.get("phase"),
            "frame": stage.get("frame"),
            "coach_decision": {
                "decision": decision.get("decision") if isinstance(decision, dict) else None,
                "grasp_ready": decision.get("grasp_ready") if isinstance(decision, dict) else None,
                "confidence": decision.get("confidence") if isinstance(decision, dict) else None,
            },
            "retry_performed": stage.get("retry_performed", False),
            "demonstrated_steps": stage.get("demonstrated_steps"),
            "eef_target_error_m": stage.get("eef_target_error_m"),
            "banana_after_xyz": stage.get("banana_after_xyz"),
            "terminal": stage.get("terminal"),
        })
    return {
        "schema_version": trace.get("schema_version"),
        "task": trace.get("task"),
        "coach_model": trace.get("coach_model"),
        "sim_version": trace.get("sim_version"),
        "physics_steps_are_local": trace.get("physics_steps_are_local"),
        "stages": stages,
        "residual_centering": trace.get("residual_centering"),
        "final": trace.get("final", {}),
    }


def _critic_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "outcome_consistent": {"type": "boolean"},
            "overall_score": {"type": "number"},
            "summary": {"type": "string"},
            "stage_findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "phase": {"type": "string"},
                        "finding": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["info", "warning", "critical"],
                        },
                    },
                    "required": ["phase", "finding", "severity"],
                    "additionalProperties": False,
                },
            },
            "recommended_coach_adjustments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "phase": {"type": "string", "enum": sorted(VALID_PHASES)},
                        "observation_check": {"type": "string"},
                        "decision_rule": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["phase", "observation_check", "decision_rule", "evidence"],
                    "additionalProperties": False,
                },
            },
            "executor_findings": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "outcome_consistent",
            "overall_score",
            "summary",
            "stage_findings",
            "recommended_coach_adjustments",
            "executor_findings",
        ],
        "additionalProperties": False,
    }


def build_payload(
    model: str,
    frames: list[Path],
    trace: dict[str, Any],
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            "You are an independent PASSIVE robotics episode critic. You never "
            "control the robot. Review the following chronological frames and "
            "the measured execution trace for the task: pick up the yellow "
            "banana and put it on the white plate. Distinguish visual-coach "
            "reasoning issues from local trajectory-executor issues. Recommend "
            "only semantic observation/decision improvements for the Gemini "
            "coach; never recommend joint values, motor commands, torques, or "
            "low-level motion. Each coach adjustment must contain an observation_check "
            "and decision_rule that Gemini can apply at a semantic phase. Treat the "
            "measured trace as authoritative: never invent a retry, failure, contact, "
            "oscillation, or correction that is not recorded there. Ground every "
            "recommendation in a trace metric or event. A geometric success does not "
            "make transport precision perfect. Return JSON only.\n\nMeasured trace:\n"
            + json.dumps(_compact_trace(trace), indent=2)[:12000]
        ),
    }]
    for index, frame in enumerate(frames):
        content.append({
            "type": "text",
            "text": f"Chronological frame {index + 1}/{len(frames)}: {frame.name}",
        })
        content.append(_image_part(frame))
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": content}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "robot_sequence_critique", "schema": _critic_schema()},
        },
    }
    return payload


def _metric_support(trace: dict[str, Any], phase: str) -> tuple[bool, str]:
    """Return whether a recorded event supports changing this coach phase."""
    if trace.get("schema_version") != 1 or not isinstance(trace.get("stages"), list):
        return False, "no structured episode trace"
    stages = {
        stage.get("phase"): stage for stage in trace["stages"] if isinstance(stage, dict)
    }
    stage = stages.get(phase)
    final = trace.get("final", {}) if isinstance(trace.get("final"), dict) else {}
    if phase in {"approach_banana", "descend"}:
        error = float((stage or {}).get("eef_target_error_m", 0.0))
        return error > 0.02, f"recorded EEF target error={error:.4f} m"
    if phase == "grasp":
        retry = bool((stage or {}).get("retry_performed", False))
        lift_ok = bool(final.get("tests", {}).get("lift", False))
        return retry or not lift_ok, f"recorded retry={retry}, physical_lift_pass={lift_ok}"
    if phase == "lift":
        lift_ok = bool(final.get("tests", {}).get("lift", False))
        return not lift_ok, f"recorded physical_lift_pass={lift_ok}"
    if phase in {"above_plate", "release"}:
        xy_error = float(final.get("banana_plate_xy_error_m", 0.0))
        target_error = float((stage or {}).get("eef_target_error_m", 0.0))
        residual = trace.get("residual_centering", {})
        if phase == "above_plate" and isinstance(residual, dict) and residual.get("enabled"):
            target_error = float(residual.get("xy_error_after_m", target_error))
        supported = xy_error > 0.04 or (phase == "above_plate" and target_error > 0.03)
        return supported, (
            f"recorded banana-plate XY error={xy_error:.4f} m, "
            f"EEF target error={target_error:.4f} m"
        )
    if phase == "global":
        passed = bool(final.get("all_tests_passed", False))
        return not passed, f"recorded all_tests_passed={passed}"
    return False, "phase has no validation rule"


def sanitize_guidance(
    critique: dict[str, Any], trace: dict[str, Any], task: str, model: str
) -> dict[str, Any]:
    """Reduce untrusted critic prose to trace-grounded semantic coach memory."""
    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    adjustments = critique.get("recommended_coach_adjustments", [])
    if not isinstance(adjustments, list):
        adjustments = []
    for item in adjustments[:12]:
        if not isinstance(item, dict):
            continue
        phase = str(item.get("phase", "")).strip()
        observation_check = " ".join(str(item.get("observation_check", "")).split())[:240]
        decision_rule = " ".join(str(item.get("decision_rule", "")).split())[:240]
        evidence = " ".join(str(item.get("evidence", "")).split())[:300]
        reason = ""
        metric_ok, metric_evidence = _metric_support(trace, phase)
        if phase not in VALID_PHASES:
            reason = "unknown phase"
        elif not observation_check or not decision_rule or not evidence:
            reason = "missing observation check, decision rule, or evidence"
        elif DISALLOWED_GUIDANCE.search(observation_check + " " + decision_rule):
            reason = "low-level actuation language is not allowed"
        elif not metric_ok:
            reason = metric_evidence
        if reason:
            rejected.append({
                "phase": phase,
                "observation_check": observation_check,
                "decision_rule": decision_rule,
                "reason": reason,
            })
        elif len(accepted) < 6:
            safe_observation, safe_rule = COACH_GATE_TEMPLATES[phase]
            accepted.append({
                "phase": phase,
                "observation_check": safe_observation,
                "decision_rule": safe_rule,
                "critic_evidence": evidence,
                "validated_metric": metric_evidence,
            })
    return {
        "schema_version": 2,
        "task": task,
        "embodiment": "RoboLab DROID Franka + Robotiq",
        "source_model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "control_authority": "none",
        "applies_on": "next_episode",
        "lessons": accepted,
        "rejected_lessons": rejected,
    }


def critique_sequence(
    artifact_dir: Path,
    endpoint: str,
    model: str,
    timeout: float,
    task: str,
) -> tuple[Path, Path]:
    frames = _ordered_frames(artifact_dir)
    if len(frames) < 2:
        raise RuntimeError(f"need at least two chronological frames in {artifact_dir}")
    trace_path = artifact_dir / "sequence_trace.json"
    trace = json.loads(trace_path.read_text()) if trace_path.is_file() else {
        "task": task,
        "note": "No structured trace was captured for this legacy run; infer only from frames.",
        "frame_names": [path.name for path in frames],
    }
    payload = build_payload(model, frames, trace)
    url = endpoint.rstrip("/") + "/chat/completions"
    try:
        response = _post_json(url, payload, timeout)
    except Exception:
        # Older vLLM builds may not support structured response schemas.
        payload.pop("response_format", None)
        response = _post_json(url, payload, timeout)
    message = response["choices"][0]["message"]["content"]
    critique = _json_object(message)
    critique_record = {
        "critic_model": model,
        "endpoint": endpoint,
        "frame_names": [path.name for path in frames],
        "trace": trace,
        "critique": critique,
    }
    critique_path = artifact_dir / "passive_critique.json"
    critique_path.write_text(json.dumps(critique_record, indent=2) + "\n")

    guidance = sanitize_guidance(critique, trace, task, model)
    guidance_path = artifact_dir / "critic_guidance.json"
    guidance_path.write_text(json.dumps(guidance, indent=2) + "\n")
    return critique_path, guidance_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8021/v1")
    parser.add_argument("--model", default="nvidia/Cosmos-Reason2-2B")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--task", default="BananaOnPlateTask")
    args = parser.parse_args()
    critique_path, guidance_path = critique_sequence(
        args.artifact_dir.expanduser().resolve(),
        args.endpoint,
        args.model,
        args.timeout,
        args.task,
    )
    guidance = json.loads(guidance_path.read_text())
    print(
        f"[passive-critic] wrote {critique_path} and {guidance_path}; "
        f"accepted_lessons={len(guidance['lessons'])} "
        f"rejected_lessons={len(guidance['rejected_lessons'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
