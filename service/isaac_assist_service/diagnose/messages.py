"""Canonical violation message templates — Swedish + English.

Per spec §G (Opus review): templates are hard-coded, never LLM-paraphrased.
Mirrors multimodal-foundation §1.3 P1 principle for the report surface.

Usage:
    from .messages import format_violation
    msg = format_violation("reach_utilization", "WARNING",
                           value=0.94, lang="sv")

Templates use named placeholders: {value}, {threshold}, {path}, {axis_label},
{delta_m}, {clearance_pct}, etc. Missing placeholders are silently elided so
templates can be added without breaking older callers.
"""
from __future__ import annotations

from typing import Any, Dict


# Swedish (default — user prefers Swedish per memory)
_SV: Dict[str, Dict[str, str]] = {
    "ik_feasible": {
        "CRITICAL": "Ingen IK-lösning för {pose_label}-pose vid {pose}. Roboten kan inte nå punkten.",
    },
    "collision_distance": {
        "CRITICAL": "Robot-konfigurationen vid {pose_label}-pose ligger inne i hinder ({obstacle}). Avstånd: {value:.3f} m.",
        "ERROR":    "Robot-konfigurationen vid {pose_label}-pose är för nära hinder ({obstacle}); {value:.3f} m < {threshold} m.",
    },
    "manipulability": {
        "WARNING": "Manipulability vid {pose_label} är låg ({value:.3f} < {threshold}); robot är nära singularitet.",
    },
    "reach_utilization": {
        "CRITICAL": "{pose_label}-pose är utanför robotens räckvidd ({value:.0%} > {threshold:.0%}).",
        "WARNING":  "{pose_label}-pose är nära robotens räckvidd ({value:.0%}); IK kan misslyckas vid edge-cases.",
    },
    "inside_obstacle_bbox": {
        "CRITICAL": "{pose_label}-positionen ligger inuti '{path}'. Flytta {pose_label}-punkten {delta_m:.2f} m i +{axis_label}.",
    },
    "clearance_pct": {
        "ERROR":   "Transitkorridoren är blockerad ({value:.0f}% fri); robot kommer att stoppa mid-trajectory.",
        "WARNING": "Transitkorridoren är delvis blockerad ({value:.0f}% fri); kontrollern kan behöva alternativ-planering.",
    },
    "cube_in_sensor_zone_at_settle": {
        "ERROR": "Inget kub-objekt hamnar i sensor-zonen vid settle-tick. Kontrollern kommer aldrig claim:a något att picka upp.",
    },
    "mutex_conflict": {
        "ERROR": "Robotarna {robot_a} och {robot_b} har överlappande transit-korridor utan deklarerad mutex. Lägg till MUTEX_PATH eller separera arbetsutrymmen.",
    },
}


# English fallback
_EN: Dict[str, Dict[str, str]] = {
    "ik_feasible": {
        "CRITICAL": "No IK solution at {pose_label} pose {pose}. Robot cannot reach this point.",
    },
    "collision_distance": {
        "CRITICAL": "Robot config at {pose_label} pose is inside obstacle ({obstacle}). Distance: {value:.3f} m.",
        "ERROR":    "Robot config at {pose_label} pose is too close to obstacle ({obstacle}); {value:.3f} m < {threshold} m.",
    },
    "manipulability": {
        "WARNING": "Manipulability at {pose_label} is low ({value:.3f} < {threshold}); robot is near singularity.",
    },
    "reach_utilization": {
        "CRITICAL": "{pose_label} pose is beyond robot reach ({value:.0%} > {threshold:.0%}).",
        "WARNING":  "{pose_label} pose is near robot reach limit ({value:.0%}); IK may fail at edge cases.",
    },
    "inside_obstacle_bbox": {
        "CRITICAL": "{pose_label} position is inside '{path}'. Move {pose_label} point {delta_m:.2f} m along +{axis_label}.",
    },
    "clearance_pct": {
        "ERROR":   "Transit corridor is blocked ({value:.0f}% clear); robot will stall mid-trajectory.",
        "WARNING": "Transit corridor is partially blocked ({value:.0f}% clear); controller may need alternative planning.",
    },
    "cube_in_sensor_zone_at_settle": {
        "ERROR": "No cube reaches the sensor zone at settle-tick. Controller will never claim anything to pick.",
    },
    "mutex_conflict": {
        "ERROR": "Robots {robot_a} and {robot_b} share an overlapping transit corridor without declared mutex. Add MUTEX_PATH or separate workspaces.",
    },
}


# Swedish pose-label aliases (so format strings like "{pose_label}-pose" read naturally)
_POSE_LABELS_SV = {"pick": "pick", "drop": "drop", "home": "home"}
_POSE_LABELS_EN = {"pick": "pick", "drop": "drop", "home": "home"}


def _render(template: str, kwargs: Dict[str, Any]) -> str:
    """Format with missing-key tolerance. Returns template unchanged on KeyError."""
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        # Strip unresolved placeholders and try again
        import re
        cleaned = re.sub(r"\{[^}]+\}", "", template)
        return cleaned.strip()


def format_violation(axis: str, severity: str, lang: str = "sv", **kwargs: Any) -> str:
    """Look up canonical message template by (axis, severity, lang). Return rendered string.

    Args:
      axis: metric axis name (matches schema.THRESHOLDS keys)
      severity: "INFO" / "WARNING" / "ERROR" / "CRITICAL"
      lang: "sv" (default) or "en"
      **kwargs: substitution values; missing keys silently elided
    """
    pool = _SV if lang == "sv" else _EN
    pose_labels = _POSE_LABELS_SV if lang == "sv" else _POSE_LABELS_EN

    # Translate pose_label to language-specific form if present
    if "pose_label" in kwargs:
        kwargs = dict(kwargs)
        kwargs["pose_label"] = pose_labels.get(kwargs["pose_label"], kwargs["pose_label"])

    axis_dict = pool.get(axis)
    if not axis_dict:
        return f"[{axis}/{severity}] {kwargs}"
    template = axis_dict.get(severity)
    if not template:
        # Fall back to any severity in same axis
        for s in ("CRITICAL", "ERROR", "WARNING", "INFO"):
            if s in axis_dict:
                template = axis_dict[s]
                break
    if not template:
        return f"[{axis}/{severity}] {kwargs}"
    return _render(template, kwargs)


def format_for_user(report: Dict[str, Any], lang: str = "sv") -> str:
    """1-3 line plain-language summary of a feasibility report for chat reply.

    Per spec §G: never LLM-paraphrase. Concatenate up to 3 highest-severity
    violations from the report, joined by newlines, prefixed with verdict-
    appropriate icon.
    """
    verdict = report.get("verdict", "feasible")
    icons = {
        "feasible":          "✅" if lang == "sv" else "OK",
        "tightly_feasible":  "⚠️"  if lang == "sv" else "WARN",
        "overconstrained":   "❌" if lang == "sv" else "FAIL",
        "infeasible":        "🛑" if lang == "sv" else "BLOCKED",
    }
    icon = icons.get(verdict, "?")
    headers_sv = {
        "feasible":          "Scenen är feasible.",
        "tightly_feasible":  "Scenen är tight men körbar:",
        "overconstrained":   "Scenen är överconstrained:",
        "infeasible":        "Scenen är infeasible:",
    }
    headers_en = {
        "feasible":          "Scene is feasible.",
        "tightly_feasible":  "Scene is tight but viable:",
        "overconstrained":   "Scene is overconstrained:",
        "infeasible":        "Scene is infeasible:",
    }
    headers = headers_sv if lang == "sv" else headers_en
    lines = [f"{icon} {headers.get(verdict, verdict)}"]

    severity_rank = {"CRITICAL": 4, "ERROR": 3, "WARNING": 2, "INFO": 1}
    violations = sorted(
        report.get("violations") or [],
        key=lambda v: -severity_rank.get(v.get("severity", "INFO"), 0),
    )
    for v in violations[:3]:
        lines.append(f"   • {v.get('message', '?')}")
    return "\n".join(lines)
