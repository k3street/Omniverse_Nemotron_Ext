"""Phase 88b — Production patch sandboxing for high-risk patches.

Sandbox-policy engine, risk classifier, and dry-run isolation layer.
Actually executing a sandboxed subprocess stays scaffold (raises
NotImplementedError in non-dry-run mode) — the pure-Python policy and
classification logic is fully implemented here.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 88b.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Tuple

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "88b"
PHASE_TITLE = "Production patch sandboxing for high-risk patches"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 88b",
    }


# ---------------------------------------------------------------------------
# Risk level type
# ---------------------------------------------------------------------------

PatchRiskLevel = Literal["minimal", "low", "moderate", "high", "critical"]

# Ordered list used for threshold comparisons (index = severity rank).
_RISK_ORDER: list[str] = ["minimal", "low", "moderate", "high", "critical"]


def _risk_ge(level: PatchRiskLevel, threshold: PatchRiskLevel) -> bool:
    """Return True if *level* is at least as severe as *threshold*."""
    return _RISK_ORDER.index(level) >= _RISK_ORDER.index(threshold)


# ---------------------------------------------------------------------------
# SandboxPolicy
# ---------------------------------------------------------------------------

@dataclass
class SandboxPolicy:
    """Execution policy applied to a patch inside the sandbox.

    Attributes
    ----------
    allow_network:
        Whether outbound network calls are permitted.
    allow_filesystem_writes:
        Whether the patch may write to the filesystem.
    allow_subprocess:
        Whether the patch may spawn child processes.
    memory_limit_mb:
        Maximum RSS memory in megabytes before the sandbox is killed.
    cpu_time_s:
        Maximum CPU-time budget in seconds.
    wall_time_s:
        Maximum wall-clock time budget in seconds.
    allowed_modules:
        Explicit allowlist of importable module names.  An empty list
        means *no* module is allowed; the sandbox layer must enforce this.
    """

    allow_network: bool = False
    allow_filesystem_writes: bool = False
    allow_subprocess: bool = False
    memory_limit_mb: int = 512
    cpu_time_s: int = 30
    wall_time_s: int = 60
    allowed_modules: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PatchRiskAssessment
# ---------------------------------------------------------------------------

@dataclass
class PatchRiskAssessment:
    """Result of classifying a patch's risk.

    Attributes
    ----------
    risk_level:
        Categorical risk tier.
    score:
        Numeric score used to derive *risk_level*.
    signals:
        Human-readable list of signals that contributed to the score.
    requires_sandbox:
        True when risk_level >= "moderate".
    requires_human_approval:
        True when risk_level >= "high".
    """

    risk_level: PatchRiskLevel
    score: float
    signals: List[str]
    requires_sandbox: bool
    requires_human_approval: bool


# ---------------------------------------------------------------------------
# PatchRiskClassifier
# ---------------------------------------------------------------------------

class PatchRiskClassifier:
    """Classify a patch string into a risk tier with a numeric score.

    The classifier uses three keyword tiers:

    * **HIGH_RISK_KEYWORDS** — each hit adds 10 points.
    * **MODERATE_RISK_KEYWORDS** — each hit adds 3 points.
    * **LOW_RISK_KEYWORDS** — each hit adds 0.5 points.

    Risk levels based on total score:

    * ``critical``  — score >= 30
    * ``high``      — score >= 15
    * ``moderate``  — score >= 5
    * ``low``       — score >= 1
    * ``minimal``   — score < 1
    """

    HIGH_RISK_KEYWORDS: tuple[str, ...] = (
        "exec(",
        "eval(",
        "subprocess",
        "__import__",
        "open(",
        "shutil.rmtree",
        "os.system",
        "delete_prim",
        "drop_table",
        "rm -rf",
        "DELETE FROM",
        "system(",
    )

    MODERATE_RISK_KEYWORDS: tuple[str, ...] = (
        "write_text",
        "with open",
        "requests.",
        "urllib.",
        "socket.",
        "globals(",
        "setattr(",
    )

    LOW_RISK_KEYWORDS: tuple[str, ...] = (
        "print(",
        "logging.",
        "math.",
        "json.",
        "datetime.",
    )

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(self, patch_code: str) -> PatchRiskAssessment:
        """Classify *patch_code* and return a ``PatchRiskAssessment``.

        Parameters
        ----------
        patch_code:
            Raw patch code string to evaluate.

        Returns
        -------
        PatchRiskAssessment
            Full assessment including risk level, numeric score, and
            per-signal explanations.
        """
        signals: List[str] = []
        score: float = 0.0

        for kw in self.HIGH_RISK_KEYWORDS:
            count = patch_code.count(kw)
            if count:
                score += count * 10
                signals.append(f"HIGH: '{kw}' found {count}x (+{count * 10:.1f})")

        for kw in self.MODERATE_RISK_KEYWORDS:
            count = patch_code.count(kw)
            if count:
                score += count * 3
                signals.append(f"MODERATE: '{kw}' found {count}x (+{count * 3:.1f})")

        for kw in self.LOW_RISK_KEYWORDS:
            count = patch_code.count(kw)
            if count:
                score += count * 0.5
                signals.append(f"LOW: '{kw}' found {count}x (+{count * 0.5:.1f})")

        if score >= 30:
            risk_level: PatchRiskLevel = "critical"
        elif score >= 15:
            risk_level = "high"
        elif score >= 5:
            risk_level = "moderate"
        elif score >= 1:
            risk_level = "low"
        else:
            risk_level = "minimal"

        requires_sandbox = _risk_ge(risk_level, "moderate")
        requires_human_approval = _risk_ge(risk_level, "high")

        return PatchRiskAssessment(
            risk_level=risk_level,
            score=score,
            signals=signals,
            requires_sandbox=requires_sandbox,
            requires_human_approval=requires_human_approval,
        )


# ---------------------------------------------------------------------------
# SANDBOX_POLICIES — default policy per risk tier
# ---------------------------------------------------------------------------

SANDBOX_POLICIES: Dict[PatchRiskLevel, SandboxPolicy] = {
    "minimal": SandboxPolicy(
        allow_network=True,
        allow_filesystem_writes=True,
        allow_subprocess=True,
        memory_limit_mb=1024,
        cpu_time_s=60,
        wall_time_s=120,
        allowed_modules=[
            "math", "json", "datetime", "logging", "print",
            "os", "sys", "re", "collections", "itertools",
        ],
    ),
    "low": SandboxPolicy(
        allow_network=False,
        allow_filesystem_writes=True,
        allow_subprocess=False,
        memory_limit_mb=768,
        cpu_time_s=45,
        wall_time_s=90,
        allowed_modules=[
            "math", "json", "datetime", "logging",
            "os.path", "re", "collections", "itertools",
        ],
    ),
    "moderate": SandboxPolicy(
        allow_network=False,
        allow_filesystem_writes=False,
        allow_subprocess=False,
        memory_limit_mb=512,
        cpu_time_s=30,
        wall_time_s=60,
        allowed_modules=[
            "math", "json", "datetime", "logging",
            "re", "collections",
        ],
    ),
    "high": SandboxPolicy(
        allow_network=False,
        allow_filesystem_writes=False,
        allow_subprocess=False,
        memory_limit_mb=256,
        cpu_time_s=15,
        wall_time_s=30,
        allowed_modules=[
            "math", "json", "datetime",
        ],
    ),
    "critical": SandboxPolicy(
        allow_network=False,
        allow_filesystem_writes=False,
        allow_subprocess=False,
        memory_limit_mb=128,
        cpu_time_s=5,
        wall_time_s=10,
        allowed_modules=[],
    ),
}


# ---------------------------------------------------------------------------
# PatchSandbox — dry-run isolation layer
# ---------------------------------------------------------------------------

class PatchSandbox:
    """Prepare and (dry-run) execute a patch under a given ``SandboxPolicy``.

    In ``dry_run=True`` mode (the default), ``execute()`` returns a
    simulated success dict without running any code.  In non-dry-run mode
    ``execute()`` raises ``NotImplementedError`` — actual subprocess
    sandboxing (seccomp, Firecracker, gVisor) is out of scope for this
    pure-Python layer.

    Parameters
    ----------
    policy:
        Sandbox policy governing permitted operations.
    dry_run:
        When True (default), execution is simulated.
    """

    # Regex to find import statements in patch code (one per line).
    _IMPORT_RE = re.compile(
        r"^\s*(?:import\s+[\w.,\ \t]+|from\s+\w[\w.]*\s+import\s+[\w.,\ \t*]+)",
        re.MULTILINE,
    )

    def __init__(self, policy: SandboxPolicy, dry_run: bool = True) -> None:
        self._policy = policy
        self._dry_run = dry_run

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, patch_code: str) -> Dict[str, Any]:
        """Return a preparation summary dict without executing the patch.

        Parameters
        ----------
        patch_code:
            Raw patch code that *would* be executed.

        Returns
        -------
        dict
            Keys: ``policy``, ``dry_run``, ``would_execute``, ``restrictions``.
        """
        restrictions: List[str] = []
        if not self._policy.allow_network:
            restrictions.append("network_blocked")
        if not self._policy.allow_filesystem_writes:
            restrictions.append("filesystem_writes_blocked")
        if not self._policy.allow_subprocess:
            restrictions.append("subprocess_blocked")
        restrictions.append(f"memory_limit_mb={self._policy.memory_limit_mb}")
        restrictions.append(f"cpu_time_s={self._policy.cpu_time_s}")
        restrictions.append(f"wall_time_s={self._policy.wall_time_s}")

        return {
            "policy": {
                "allow_network": self._policy.allow_network,
                "allow_filesystem_writes": self._policy.allow_filesystem_writes,
                "allow_subprocess": self._policy.allow_subprocess,
                "memory_limit_mb": self._policy.memory_limit_mb,
                "cpu_time_s": self._policy.cpu_time_s,
                "wall_time_s": self._policy.wall_time_s,
                "allowed_modules": list(self._policy.allowed_modules),
            },
            "dry_run": self._dry_run,
            "would_execute": not self._dry_run,
            "restrictions": restrictions,
        }

    def execute(self, patch_code: str) -> Dict[str, Any]:
        """Execute the patch inside the sandbox (or simulate if dry_run).

        Parameters
        ----------
        patch_code:
            Raw patch code to execute.

        Returns
        -------
        dict
            In dry-run mode: ``{"success": True, "dry_run": True,
            "simulated": True, "output": None}``.

        Raises
        ------
        NotImplementedError
            When ``dry_run=False`` — actual subprocess sandbox execution
            is not implemented in this pure-Python layer.
        """
        if not self._dry_run:
            raise NotImplementedError(
                "Non-dry-run sandbox execution requires a subprocess+seccomp, "
                "Firecracker, or gVisor backend, which is outside the scope of "
                "this pure-Python SPEC/SANDBOX layer (Phase 88b scaffold)."
            )

        # Dry-run: simulate a successful execution without running the code.
        return {
            "success": True,
            "dry_run": True,
            "simulated": True,
            "output": None,
        }

    def validate_imports(self, patch_code: str) -> List[str]:
        """Return all import statements found in *patch_code*.

        Parameters
        ----------
        patch_code:
            Raw patch code to scan.

        Returns
        -------
        list[str]
            Each element is a stripped import statement (``import X`` or
            ``from X import Y``).
        """
        return [m.strip() for m in self._IMPORT_RE.findall(patch_code)]

    def is_module_allowed(self, module_name: str) -> bool:
        """Return True if *module_name* is in the policy's allowed list.

        Parameters
        ----------
        module_name:
            Module name to check, e.g. ``"json"`` or ``"os.path"``.

        Returns
        -------
        bool
        """
        return module_name in self._policy.allowed_modules


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def select_policy_for_patch(
    patch_code: str,
) -> Tuple[PatchRiskAssessment, SandboxPolicy]:
    """Classify *patch_code* and return the matching (assessment, policy).

    Parameters
    ----------
    patch_code:
        Raw patch code string.

    Returns
    -------
    tuple[PatchRiskAssessment, SandboxPolicy]
        The full risk assessment plus the canonical sandbox policy for
        the assessed risk level.
    """
    classifier = PatchRiskClassifier()
    assessment = classifier.assess(patch_code)
    policy = SANDBOX_POLICIES[assessment.risk_level]
    return assessment, policy
