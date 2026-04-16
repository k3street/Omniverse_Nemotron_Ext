"""Assemble a single Claude Code session prompt from persona + rules + modifiers + task.

Layout of inputs (relative to repo root):
    docs/qa/personas/{persona_id}.md
    docs/qa/interaction_rules.md
    docs/qa/tasks/{task_id}.md

Output of this module is a single Python string suitable for `claude -p "<prompt>"`.

CLI:
    python -m scripts.qa.build_session_prompt --persona 01_maya --task M-01
    python -m scripts.qa.build_session_prompt --persona 01_maya --task M-01 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

# Repo root = scripts/qa/.. /..
REPO_ROOT = Path(__file__).resolve().parents[2]
QA_DIR = REPO_ROOT / "docs" / "qa"

# ---------------------------------------------------------------------------
# Modifier deck
# ---------------------------------------------------------------------------

MODIFIER_VALUES: Dict[str, list] = {
    "patience": [2, 3, 5, 10],
    "emotion": ["baseline", "frustrated", "stressed", "excited"],
    "time_pressure": ["relaxed", "deadline_today", "3_weeks_out", "panic"],
    "vocabulary_drift": ["consistent", "slang_when_tired", "swearing_when_frustrated"],
    "attention": ["reads_fully", "first_sentence_only", "skips_to_code"],
}

# Persona-specific clamps (see docs/qa/interaction_rules.md §5)
PERSONA_CLAMPS: Dict[str, Dict[str, list]] = {
    "08_alex": {"attention": ["first_sentence_only", "skips_to_code"]},
    "07_thomas": {
        "attention": ["reads_fully"],
        "vocabulary_drift": ["consistent"],
    },
    "03_kenji": {
        "attention": ["reads_fully"],
        "vocabulary_drift": ["consistent"],
    },
    "15_amir": {"attention": ["reads_fully", "skips_to_code"]},
}


@dataclass(frozen=True)
class Modifiers:
    patience: int
    emotion: str
    time_pressure: str
    vocabulary_drift: str
    attention: str

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


def random_modifiers(persona_id: str, rng: Optional[random.Random] = None) -> Modifiers:
    """Sample a modifier set for a persona, respecting clamps."""
    rng = rng or random.Random()
    clamps = PERSONA_CLAMPS.get(persona_id, {})

    def pick(dim: str):
        values = clamps.get(dim, MODIFIER_VALUES[dim])
        return rng.choice(values)

    return Modifiers(
        patience=int(pick("patience")),
        emotion=str(pick("emotion")),
        time_pressure=str(pick("time_pressure")),
        vocabulary_drift=str(pick("vocabulary_drift")),
        attention=str(pick("attention")),
    )


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required QA asset missing: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_persona(persona_id: str, qa_dir: Optional[Path] = None) -> str:
    qa_dir = qa_dir or QA_DIR
    return _read(qa_dir / "personas" / f"{persona_id}.md")


def load_task(task_id: str, qa_dir: Optional[Path] = None) -> str:
    qa_dir = qa_dir or QA_DIR
    return _read(qa_dir / "tasks" / f"{task_id}.md")


def load_interaction_rules(qa_dir: Optional[Path] = None) -> str:
    qa_dir = qa_dir or QA_DIR
    return _read(qa_dir / "interaction_rules.md")


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

# Section markers — tests assert these appear in this exact order.
SECTION_MARKERS = (
    "=== Interaction Rules ===",
    "=== This Session's Modifiers ===",
    "=== Your Task ===",
    "=== Starting Now ===",
)


def build_session_prompt(
    persona_id: str,
    task_id: str,
    modifiers: Optional[Modifiers] = None,
    *,
    qa_dir: Optional[Path] = None,
    rng: Optional[random.Random] = None,
) -> str:
    """Combine persona + rules + modifiers + task into a single prompt string.

    Section ordering matches `docs/qa/session_template.md` exactly.
    """
    persona = load_persona(persona_id, qa_dir=qa_dir)
    rules = load_interaction_rules(qa_dir=qa_dir)
    task = load_task(task_id, qa_dir=qa_dir)
    mods = modifiers or random_modifiers(persona_id, rng=rng)

    return (
        f"{persona}\n\n"
        f"{SECTION_MARKERS[0]}\n"
        f"{rules}\n\n"
        f"{SECTION_MARKERS[1]}\n"
        f"Patience: {mods.patience} unhelpful replies before you give up on this task\n"
        f"Emotional baseline: {mods.emotion}\n"
        f"Time pressure: {mods.time_pressure}\n"
        f"Vocabulary drift: {mods.vocabulary_drift}\n"
        f"Reading attention: {mods.attention}\n\n"
        f"These describe HOW you behave this session. They do not change WHO you are.\n\n"
        f"{SECTION_MARKERS[2]}\n"
        f"{task}\n\n"
        f"{SECTION_MARKERS[3]}\n"
        f"You are about to open a chat with Isaac Assist (an in-app AI assistant for NVIDIA Isaac Sim).\n"
        f"Write your first message to Isaac Assist. Stay in character. Do not narrate.\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble a Claude Code QA session prompt for one (persona, task) pair."
    )
    parser.add_argument("--persona", required=True, help="Persona id, e.g. 01_maya")
    parser.add_argument("--task", required=True, help="Task id, e.g. M-01")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for modifiers")
    parser.add_argument(
        "--qa-dir",
        type=Path,
        default=None,
        help="Override docs/qa directory (defaults to repo-root/docs/qa)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON {prompt, modifiers} instead of plain prompt text",
    )
    args = parser.parse_args(argv)

    rng = random.Random(args.seed) if args.seed is not None else None
    mods = random_modifiers(args.persona, rng=rng)
    prompt = build_session_prompt(
        args.persona,
        args.task,
        modifiers=mods,
        qa_dir=args.qa_dir,
    )

    if args.json:
        sys.stdout.write(json.dumps({"prompt": prompt, "modifiers": mods.as_dict()}, indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(prompt)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(_cli())
