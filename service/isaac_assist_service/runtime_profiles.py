"""Runtime profiles for Isaac Sim / Isaac Lab compatibility.

The code suggestion layer must not mix Isaac Sim 5.1-era recipes with
Isaac Sim 6.0 / Isaac Lab 3.x APIs.  This module centralizes the active
runtime identity and the metadata checks used by retrievers, validators,
and harness selectors.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class RuntimeProfile:
    key: str
    isaac_sim_version: str
    isaac_lab_version: str
    code_pattern_version: str
    default_template_scope: str
    ros2_omnigraph_namespace: str
    extension_folder: str
    knowledge_files: tuple[str, ...]
    template_policy: str
    qa_policy: str
    launch_selector: str
    api_scope: tuple[str, ...]
    notes: str


ISAAC_SIM_51 = RuntimeProfile(
    key="isaacsim-5.1",
    isaac_sim_version="5.1.0",
    isaac_lab_version="2.x",
    code_pattern_version="5.1.0",
    default_template_scope="5.1.0",
    ros2_omnigraph_namespace="isaacsim.ros2.bridge",
    extension_folder="exts/isaac_5.1",
    knowledge_files=(
        "workspace/knowledge/code_patterns_5.1.0.jsonl",
        "workspace/knowledge/knowledge_5.1.0.jsonl",
        "workspace/knowledge/negative_patterns_5.1.0.jsonl",
    ),
    template_policy="Legacy unscoped templates are treated as 5.1-only unless explicitly tagged otherwise.",
    qa_policy="Existing QA tasks, personas, and controller matrices are 5.1-baseline unless tagged with runtime_profiles or isaac_sim_versions.",
    launch_selector="./launch_isaac.sh --version 5.1",
    api_scope=(
        "Use isaacsim.ros2.bridge.* OmniGraph node type strings.",
        "Use 5.1-verified isaacsim.* imports and pxr/omni.usd fallbacks.",
        "Do not use 6.0-only isaacsim.ros2.nodes.* node type strings.",
    ),
    notes="Isaac Sim 5.1 recipes use isaacsim.* modules and ROS2 OmniGraph nodes under isaacsim.ros2.bridge.",
)

ISAAC_SIM_60 = RuntimeProfile(
    key="isaacsim-6.0",
    isaac_sim_version="6.0.0",
    isaac_lab_version="3.x",
    code_pattern_version="6.0.0",
    default_template_scope="6.0.0",
    ros2_omnigraph_namespace="isaacsim.ros2.nodes",
    extension_folder="exts/isaac_6.0",
    knowledge_files=(
        "workspace/knowledge/code_patterns_6.0.0.jsonl",
        "workspace/knowledge/knowledge_6.0.0.jsonl",
        "workspace/knowledge/negative_patterns_6.0.0.jsonl",
    ),
    template_policy="Only templates explicitly tagged for isaacsim-6.0, 6.0, 6.0.0, or any may be used.",
    qa_policy="6.0 QA evidence must be tagged, copied into a 6.0-specific task, or recorded as a migration finding.",
    launch_selector="./launch_isaac.sh --version 6.0",
    api_scope=(
        "Use isaacsim.ros2.nodes.* OmniGraph node type strings for new ROS2 graph code.",
        "Prefer 6.0-tagged docs/templates/patterns; use pxr/omni.usd for conservative cross-version USD edits.",
        "Do not promote 5.1 bridge recipes to known-good 6.0 code without validation.",
    ),
    notes="Isaac Sim 6.0 / Isaac Lab 3.x recipes must be explicitly verified for 6.0 APIs.",
)


_PROFILES = {
    ISAAC_SIM_51.key: ISAAC_SIM_51,
    ISAAC_SIM_60.key: ISAAC_SIM_60,
    "5.1": ISAAC_SIM_51,
    "5.1.0": ISAAC_SIM_51,
    "isaacsim_5.1": ISAAC_SIM_51,
    "6.0": ISAAC_SIM_60,
    "6.0.0": ISAAC_SIM_60,
    "isaacsim_6.0": ISAAC_SIM_60,
}


def get_runtime_profile(profile: Optional[str] = None) -> RuntimeProfile:
    """Resolve the active runtime profile from explicit input or env vars."""
    explicit = (
        profile
        or os.environ.get("ISAAC_RUNTIME_PROFILE")
        or os.environ.get("ISAAC_VERSION")
        or ""
    ).strip()
    if explicit:
        return _PROFILES.get(explicit, _PROFILES.get(explicit.lower(), ISAAC_SIM_51))

    path = (
        os.environ.get("ISAAC_SIM_ROOT")
        or os.environ.get("ISAAC_SIM_PATH")
        or ""
    )
    if "6.0" in path or "/IsaacSim/" in path or path.endswith("/IsaacSim"):
        return ISAAC_SIM_60
    return ISAAC_SIM_51


def detect_isaac_version() -> str:
    return get_runtime_profile().isaac_sim_version


def runtime_scope_summary(profile: RuntimeProfile) -> Dict[str, Any]:
    """Return the policy surface that defines one runtime compatibility lane."""
    return {
        "profile": profile.key,
        "isaac_sim_version": profile.isaac_sim_version,
        "isaac_lab_version": profile.isaac_lab_version,
        "code_pattern_version": profile.code_pattern_version,
        "extension_folder": profile.extension_folder,
        "knowledge_files": list(profile.knowledge_files),
        "template_policy": profile.template_policy,
        "qa_policy": profile.qa_policy,
        "launch_selector": profile.launch_selector,
        "ros2_omnigraph_namespace": profile.ros2_omnigraph_namespace,
        "api_scope": list(profile.api_scope),
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(v) for v in value]
    return [str(value)]


def _matches_any(value: str, candidates: list[str]) -> bool:
    if not candidates:
        return False
    major_minor = ".".join(value.split(".")[:2])
    for c in candidates:
        c = c.strip()
        if c in ("*", "any"):
            return True
        if c == value or c == major_minor:
            return True
        if c.endswith(".x") and major_minor.startswith(c[:-2]):
            return True
    return False


def metadata_matches_profile(
    metadata: Dict[str, Any],
    profile: RuntimeProfile,
    *,
    unscoped_default: str = "5.1.0",
) -> bool:
    """Return True if a template/pattern metadata object belongs to profile.

    Existing unscoped templates/patterns are treated as 5.1-era by default.
    New 6.0/Lab 3 assets must opt in with one of:
      - runtime_profiles: ["isaacsim-6.0"]
      - isaac_sim_versions: ["6.0.0"]
      - version_scope: "6.0.0"
    """
    runtime_profiles = _as_list(metadata.get("runtime_profiles") or metadata.get("runtime_profile"))
    if runtime_profiles:
        return profile.key in runtime_profiles or "*" in runtime_profiles or "any" in runtime_profiles

    sim_versions = _as_list(metadata.get("isaac_sim_versions") or metadata.get("isaac_sim_version"))
    if sim_versions:
        return _matches_any(profile.isaac_sim_version, sim_versions)

    version_scope = _as_list(metadata.get("version_scope"))
    if version_scope:
        return _matches_any(profile.isaac_sim_version, version_scope)

    return _matches_any(profile.isaac_sim_version, [unscoped_default])


def prompt_runtime_rules(profile: RuntimeProfile) -> str:
    """Concise prompt block that tells coding agents which API lane to stay in."""
    if profile.key == ISAAC_SIM_60.key:
        return f"""\
ACTIVE RUNTIME PROFILE: {profile.key}
- Isaac Sim: {profile.isaac_sim_version}; Isaac Lab: {profile.isaac_lab_version}.
- Use only docs, templates, and code patterns tagged for Isaac Sim 6.0 / Isaac Lab 3.x unless the user explicitly asks for migration advice.
- ROS2 OmniGraph node type namespace is `{profile.ros2_omnigraph_namespace}`. Do not emit `isaacsim.ros2.bridge.*` node type strings for 6.0 code.
- Treat unscoped legacy templates as 5.1-only. If no 6.0 verified template exists, say so and generate conservative USD/pxr code or ask to validate before saving it as known-good."""
    return f"""\
ACTIVE RUNTIME PROFILE: {profile.key}
- Isaac Sim: {profile.isaac_sim_version}; Isaac Lab: {profile.isaac_lab_version}.
- Use Isaac Sim 5.1 verified patterns by default.
- ROS2 OmniGraph node type namespace is `{profile.ros2_omnigraph_namespace}`. Do not emit Isaac Sim 6.0-only `isaacsim.ros2.nodes.*` node type strings for 5.1 code."""
