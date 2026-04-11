"""
Simulator execution harness.

Runs Python code snippets in isolated subprocesses with appropriate
environment setup and parses output for PhysX / Omniverse error patterns.

Execution modes (auto-selected):
  python_only   plain Python, no sim libs
  usd_python    real OpenUSD (pxr) via standalone build — venv Python
  isaac_sim     full headless Isaac Sim via python.sh — real omni.* + pxr + PhysX

No mocks. All omni.* / isaaclab / isaacsim code runs through the real simulator.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── Known installation paths ──────────────────────────────────────────────────

OPENUSD_PYTHON_PATH = os.environ.get("OPENUSD_PYTHON_PATH", "/opt/nvidia/omniverse/openusd/lib/python")
_ISAAC_SIM_PATH_ENV = os.environ.get("ISAAC_SIM_PATH", "")

VENV_PYTHON = sys.executable  # Safely default to the host Python environment running the extension

# Direct Isaac Sim roots to probe first (fastest, before any directory walking)
_ISAAC_SIM_DIRECT = [
    Path(_ISAAC_SIM_PATH_ENV) if _ISAAC_SIM_PATH_ENV else Path("/nonexistent/path/fallback"),
    Path("/opt/nvidia/isaac-sim"),
    Path("/isaac-sim"),
]
# Parent dirs whose versioned subdirs may contain an Isaac Sim install
_ISAAC_SIM_PARENT_DIRS = [
    Path("${HOME}/.local/share/ov/pkg"),
]


def find_isaac_sim() -> Path | None:
    """Return path to Isaac Sim installation root, or None."""
    # 1. Check direct paths first
    for candidate in _ISAAC_SIM_DIRECT:
        if candidate.exists() and (candidate / "python.sh").exists():
            return candidate
    # 2. Walk versioned subdirs of parent package dirs
    for base in _ISAAC_SIM_PARENT_DIRS:
        if not base.exists():
            continue
        for sub in sorted(base.iterdir(), reverse=True):
            if sub.is_dir() and "isaac" in sub.name.lower():
                if (sub / "python.sh").exists() or (sub / "kit" / "python.sh").exists():
                    return sub
    return None


ISAAC_SIM_ROOT = find_isaac_sim()
# isaac_sim mode needs SimulationApp startup (~30-60s) so use a longer timeout
ISAAC_SIM_DEFAULT_TIMEOUT = 120

# ── Isaac Sim headless preamble ───────────────────────────────────────────────
# Injected before user code in isaac_sim mode.
# SimulationApp MUST be the first Omniverse import.
_ISAAC_SIM_PREAMBLE = '''
import sys, os
# Headless Isaac Sim — no display required
from isaacsim import SimulationApp
_simulation_app = SimulationApp({"headless": True, "hide_ui": True})
# pxr and omni.* are now safe to import
from pxr import Usd, UsdGeom, UsdPhysics, UsdLux, Gf, Sdf, Vt, Tf
import omni.usd
'''
_ISAAC_SIM_POSTAMBLE = '''
# Clean shutdown
try:
    _simulation_app.close()
except Exception:
    pass
'''

_OMNI_ERROR_RE    = re.compile(r"\[Error\]",   re.IGNORECASE)
_OMNI_WARN_RE     = re.compile(r"\[Warning\]", re.IGNORECASE)
_PHYSX_ERROR_RE   = re.compile(
    r"physx.*?error|px.*?error|physics.*?simulation.*?error|"
    r"articulation.*?error|joint.*?error",
    re.IGNORECASE,
)
_TRACEBACK_RE      = re.compile(r"Traceback \(most recent call last\):")
_BASH_ERROR_RE     = re.compile(r"There was an error running python")  # python.sh error_exit
_EXCEPTION_LINE_RE = re.compile(
    r"^(?:RuntimeError|ValueError|AttributeError|TypeError|"
    r"ImportError|NameError|AssertionError|NotImplementedError|"
    r"KeyError|IndexError|ModuleNotFoundError):",
    re.MULTILINE,
)
_SEGFAULT_RE      = re.compile(r"segmentation fault|core dumped|SIGSEGV", re.IGNORECASE)

# Isaac Sim antipatterns that cause hard crashes
ANTIPATTERNS: dict[str, tuple[re.Pattern, str]] = {
    "nested_rigid_body": (
        re.compile(r"RigidBodyAPI\.Apply\(.+?\).*\n(?:.*\n){0,20}.*RigidBodyAPI\.Apply", re.DOTALL),
        "Nested RigidBodyAPI causes Isaac Sim crash — apply only to topmost prim",
    ),
    "missing_apply": (
        # Fires only on direct constructor calls — ClassName(prim) — which bypass
        # the required .Apply() step.  Does NOT fire on:
        #   UsdPhysics.RigidBodyAPI.Apply(prim)   ← correct usage
        #   UsdPhysics.RigidBodyAPI.Get(stage, p)  ← reading existing schema
        #   # comments mentioning RigidBodyAPI     ← documentation
        re.compile(r"\b(CollisionAPI|RigidBodyAPI|DriveAPI|MassAPI|ArticulationRootAPI)\s*\("),
        "Physics API instantiated directly — use ClassName.Apply(prim) instead of ClassName(prim)",
    ),
    "bad_joint_path": (
        re.compile(r'body0\s*=\s*["\'](?!/)[^/]'),
        "Joint body0/body1 must be absolute USD paths starting with /",
    ),
    "hardcoded_env_path": (
        re.compile(r'["\'](?:/home/|/root/|C:\\\\Users\\\\)[^"\']*\.usd["\']'),
        "Hardcoded absolute path in code — use relative paths or config",
    ),
}


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class SimResult:
    """Structured result from running code in a simulator subprocess."""
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool            = False
    duration_s: float          = 0.0
    execution_mode: str        = "unknown"
    errors: list[str]          = field(default_factory=list)
    warnings: list[str]        = field(default_factory=list)
    has_traceback: bool        = False
    has_physx_error: bool      = False
    has_segfault: bool         = False
    antipatterns_found: dict   = field(default_factory=dict)  # name → detail

    @property
    def passed(self) -> bool:
        return (
            not self.timed_out
            and self.returncode == 0
            and not self.has_traceback
            and not self.has_physx_error
            and not self.has_segfault
            and not self.errors
        )

    @property
    def summary(self) -> str:
        if self.timed_out:
            return "TIMEOUT"
        if self.has_segfault:
            return "SEGFAULT"
        if self.has_traceback:
            # Extract last few lines of traceback
            lines = (self.stdout + "\n" + self.stderr).splitlines()
            tb, in_tb = [], False
            for line in lines:
                if "Traceback" in line:
                    in_tb = True
                if in_tb:
                    tb.append(line)
                    if len(tb) > 20:
                        break
            return "TRACEBACK:\n" + "\n".join(tb)
        if self.errors:
            return "ERRORS: " + " | ".join(self.errors[:3])
        if self.returncode != 0:
            return f"EXIT {self.returncode}: {(self.stderr or self.stdout)[:300]}"
        if self.antipatterns_found:
            return "ANTIPATTERNS: " + ", ".join(self.antipatterns_found)
        return "OK"

    def as_log_block(self) -> str:
        """Full log block for agent feedback."""
        parts = [
            f"=== Execution Mode: {self.execution_mode} | "
            f"Return: {self.returncode} | Duration: {self.duration_s:.2f}s ===",
        ]
        if self.stdout.strip():
            parts.append("--- STDOUT ---\n" + self.stdout.strip())
        if self.stderr.strip():
            parts.append("--- STDERR ---\n" + self.stderr.strip())
        if self.antipatterns_found:
            parts.append("--- ANTIPATTERNS ---")
            for name, detail in self.antipatterns_found.items():
                parts.append(f"  {name}: {detail}")
        return "\n".join(parts)


# ── Code analysis helpers ─────────────────────────────────────────────────────

def detect_requirements(code: str) -> set[str]:
    """Detect what simulator libraries the code needs."""
    needs: set[str] = set()
    if re.search(r"\bfrom\s+pxr\b|import\s+pxr\b|from\s+pxr\.", code):
        needs.add("usd")
    if re.search(r"\bomni\.", code):
        needs.add("omniverse")
    if re.search(r"\bisaac_sim\b|\bomni\.isaac\b", code):
        needs.add("isaac_sim")
    if re.search(r"\bisaaclab\b|\bisaac_lab\b|from\s+isaaclab\b", code):
        needs.add("isaac_lab")
    if re.search(r"\bcadquery\b|import\s+cq\b", code):
        needs.add("cadquery")
    if re.search(r"\bFreeCAD\b", code):
        needs.add("freecad")
    return needs


def extract_code_blocks(text: str) -> list[str]:
    """Extract fenced Python code blocks from markdown text."""
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.DOTALL)
    if not blocks:
        blocks = re.findall(r"```\n(.*?)```", text, re.DOTALL)
    return [b.strip() for b in blocks if b.strip()]


def validate_syntax(code: str) -> tuple[bool, str]:
    """Returns (is_valid, error_message)."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"


def check_antipatterns(code: str) -> dict[str, str]:
    """Scan code for Isaac Sim antipatterns. Returns {name: description}."""
    found: dict[str, str] = {}
    for name, (pattern, description) in ANTIPATTERNS.items():
        if pattern.search(code):
            found[name] = description
    return found


# ── Output parsing ────────────────────────────────────────────────────────────

def parse_output(stdout: str, stderr: str) -> tuple[list[str], list[str], bool, bool, bool]:
    """
    Parse simulator output for errors and warnings.

    Returns:
        (errors, warnings, has_traceback, has_physx_error, has_segfault)
    """
    combined = stdout + "\n" + stderr
    errors: list[str] = []
    warnings: list[str] = []

    for line in combined.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _OMNI_ERROR_RE.search(stripped):
            errors.append(stripped)
        elif _BASH_ERROR_RE.search(stripped):
            errors.append(stripped)
        elif _PHYSX_ERROR_RE.search(stripped):
            errors.append(stripped)
        elif _EXCEPTION_LINE_RE.search(stripped):
            errors.append(stripped)
        elif _OMNI_WARN_RE.search(stripped):
            warnings.append(stripped)

    has_traceback    = bool(_TRACEBACK_RE.search(combined))
    has_physx_error  = bool(_PHYSX_ERROR_RE.search(combined))
    has_segfault     = bool(_SEGFAULT_RE.search(combined))
    return errors, warnings, has_traceback, has_physx_error, has_segfault


# ── Core runner ───────────────────────────────────────────────────────────────

def run_code(
    code: str,
    mode: str = "auto",
    timeout: int = 45,
    extra_env: dict[str, str] | None = None,
) -> SimResult:
    """
    Run Python code in an isolated subprocess and return a SimResult.

    mode = "auto"        → detect requirements and pick best mode
    mode = "python_only" → bare Python, no simulator libs
    mode = "usd_python"  → real OpenUSD (pxr) via standalone build, venv Python
    mode = "isaac_sim"   → full headless Isaac Sim via python.sh
    """
    # Auto-detect mode — no mocks, every omni.*/isaaclab import runs through real Isaac Sim
    if mode == "auto":
        needs = detect_requirements(code)
        if "omniverse" in needs or "isaac_sim" in needs or "isaac_lab" in needs:
            if not ISAAC_SIM_ROOT:
                raise RuntimeError(
                    "Code requires omni.*/isaaclab but Isaac Sim installation not found. "
                    "Check _ISAAC_SIM_DIRECT paths in sim_harness.py."
                )
            mode = "isaac_sim"
        elif "usd" in needs:
            mode = "usd_python"
        else:
            mode = "python_only"

    _VALID_MODES = {"auto", "python_only", "usd_python", "isaac_sim"}
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Unknown execution mode {mode!r}. Valid: {sorted(_VALID_MODES)}. "
            "mock_omni has been removed — all omni.* code runs through real Isaac Sim."
        )

    # Check static antipatterns before running
    antipatterns = check_antipatterns(code)

    # Build the code string to execute
    if mode == "isaac_sim":
        # Wrap with SimulationApp headless init + clean shutdown
        exec_code = _ISAAC_SIM_PREAMBLE + "\n" + code + "\n" + _ISAAC_SIM_POSTAMBLE
    else:
        exec_code = code

    # Inject USD Python path for usd_python mode (venv Python, standalone OpenUSD build).
    # isaac_sim mode: python.sh sources setup_python_env.sh which sets PYTHONPATH.
    env = os.environ.copy()
    if mode == "usd_python":
        existing_pp = env.get("PYTHONPATH", "")
        if OPENUSD_PYTHON_PATH not in existing_pp and Path(OPENUSD_PYTHON_PATH).exists():
            env["PYTHONPATH"] = (
                f"{OPENUSD_PYTHON_PATH}:{existing_pp}" if existing_pp
                else OPENUSD_PYTHON_PATH
            )
    if extra_env:
        env.update(extra_env)

    # Pick the Python executable
    if mode == "isaac_sim" and ISAAC_SIM_ROOT:
        python_bin_path = ISAAC_SIM_ROOT / "python.sh"
        if not python_bin_path.exists():
            python_bin_path = ISAAC_SIM_ROOT / "kit" / "python.sh"
        # python.sh forwards all args to kit/python/bin/python3,
        # so `python.sh /tmp/script.py` == `python3 /tmp/script.py`
        python_cmd = [str(python_bin_path)]
        use_file = True
    else:
        python_bin = VENV_PYTHON if Path(VENV_PYTHON).exists() else sys.executable
        python_cmd = [python_bin]
        use_file = True

    t0 = time.perf_counter()

    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, prefix="sim_test_"
    ) as fh:
        fh.write(exec_code)
        tmp_path = fh.name

    try:
        cmd = python_cmd + [tmp_path] if use_file else python_cmd + [exec_code]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        duration = time.perf_counter() - t0
        errors, warnings, has_tb, has_physx, has_seg = parse_output(
            proc.stdout, proc.stderr
        )
        return SimResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
            duration_s=round(duration, 2),
            execution_mode=mode,
            errors=errors,
            warnings=warnings,
            has_traceback=has_tb,
            has_physx_error=has_physx,
            has_segfault=has_seg,
            antipatterns_found=antipatterns,
        )

    except subprocess.TimeoutExpired:
        return SimResult(
            returncode=-1,
            stdout="",
            stderr=f"Execution timed out after {timeout}s",
            timed_out=True,
            duration_s=float(timeout),
            execution_mode=mode,
            errors=[f"Timeout after {timeout}s"],
            antipatterns_found=antipatterns,
        )
    except Exception as exc:
        return SimResult(
            returncode=-1,
            stdout="",
            stderr=str(exc),
            errors=[str(exc)],
            execution_mode=mode,
            antipatterns_found=antipatterns,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def run_all_blocks(
    text: str,
    mode: str = "auto",
    timeout: int = 45,
) -> list[tuple[str, SimResult]]:
    """
    Extract all code blocks from markdown text and run each one.

    Returns a list of (code, SimResult) pairs.
    """
    blocks = extract_code_blocks(text)
    if not blocks:
        return []
    return [(code, run_code(code, mode=mode, timeout=timeout)) for code in blocks]
