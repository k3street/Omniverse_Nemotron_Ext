"""Phase 103 contract tests — User-facing onboarding tutorial.

Gate: tutorial markdown exists with all required sections +
      tutorial-step state machine works.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
TUTORIAL_MD = REPO_ROOT / "docs" / "onboarding_tutorial.md"

REQUIRED_H2_SECTIONS = [
    "Welcome",
    "Prerequisites",
    "Step 1: First Connection",
    "Step 2: Your First Scene",
    "Step 3: Adding a Robot",
    "Step 4: Running a Pick-and-Place",
    "Step 5: Domain Randomisation",
    "Step 6: Workflows",
    "Troubleshooting",
    "Next Steps",
]


def _parse_sections(md_text: str) -> dict[str, str]:
    """Return a mapping of heading text → body text for every ## heading."""
    sections: dict[str, str] = {}
    # Split on lines that start with ## (h2) but not ###
    parts = re.split(r"^## (.+)$", md_text, flags=re.MULTILINE)
    # parts[0] = preamble, then alternating heading / body
    it = iter(parts[1:])
    for heading, body in zip(it, it):
        sections[heading.strip()] = body.strip()
    return sections


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_103_metadata():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 103
    assert md["status"] == "landed"
    assert "title" in md


def test_tutorial_markdown_file_exists():
    assert TUTORIAL_MD.exists(), f"Missing tutorial markdown at {TUTORIAL_MD}"


def test_tutorial_markdown_has_h1_title():
    text = TUTORIAL_MD.read_text(encoding="utf-8")
    assert "# Isaac Assist Onboarding Tutorial" in text


def test_tutorial_markdown_has_all_required_h2_sections():
    text = TUTORIAL_MD.read_text(encoding="utf-8")
    sections = _parse_sections(text)
    missing = [h for h in REQUIRED_H2_SECTIONS if h not in sections]
    assert not missing, f"Missing h2 sections: {missing}"


def test_tutorial_markdown_sections_have_real_content():
    """Every required h2 section must have >20 chars of prose body."""
    text = TUTORIAL_MD.read_text(encoding="utf-8")
    sections = _parse_sections(text)
    thin = [h for h in REQUIRED_H2_SECTIONS if len(sections.get(h, "")) < 20]
    assert not thin, f"Sections with <20 chars of content: {thin}"


def test_tutorial_steps_count():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import TUTORIAL_STEPS
    assert len(TUTORIAL_STEPS) >= 6, f"Expected >=6 tutorial steps, got {len(TUTORIAL_STEPS)}"


def test_every_step_has_nonempty_expected_tools():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import TUTORIAL_STEPS
    for step in TUTORIAL_STEPS:
        assert len(step.expected_tools) > 0, (
            f"Step {step.step_id} '{step.title}' has empty expected_tools"
        )


def test_onboarding_tracker_mark_complete_advances_current_step():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import OnboardingTracker
    tracker = OnboardingTracker()
    assert tracker.current_step == 1
    tracker.mark_complete(1)
    assert tracker.current_step == 2, (
        f"Expected current_step=2 after completing step 1, got {tracker.current_step}"
    )


def test_onboarding_tracker_is_complete_only_after_all_steps():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import (
        OnboardingTracker,
        TUTORIAL_STEPS,
    )
    tracker = OnboardingTracker()
    assert not tracker.is_complete()
    for step in TUTORIAL_STEPS[:-1]:
        tracker.mark_complete(step.step_id)
        assert not tracker.is_complete(), (
            f"is_complete() returned True too early after step {step.step_id}"
        )
    tracker.mark_complete(TUTORIAL_STEPS[-1].step_id)
    assert tracker.is_complete()


def test_onboarding_tracker_next_step_skips_completed():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import OnboardingTracker
    tracker = OnboardingTracker()
    # Complete steps 1 and 2
    tracker.mark_complete(1)
    tracker.mark_complete(2)
    nxt = tracker.next_step()
    assert nxt is not None
    assert nxt.step_id == 3, f"Expected next step 3, got {nxt.step_id}"


def test_onboarding_tracker_next_step_returns_none_when_done():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import (
        OnboardingTracker,
        TUTORIAL_STEPS,
    )
    tracker = OnboardingTracker()
    for step in TUTORIAL_STEPS:
        tracker.mark_complete(step.step_id)
    assert tracker.next_step() is None


def test_onboarding_tracker_progress_correct_pct():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import (
        OnboardingTracker,
        TUTORIAL_STEPS,
    )
    tracker = OnboardingTracker()
    total = len(TUTORIAL_STEPS)
    tracker.mark_complete(TUTORIAL_STEPS[0].step_id)
    p = tracker.progress()
    assert p["completed"] == 1
    assert p["total"] == total
    expected_pct = round(1 / total * 100.0, 1)
    assert p["pct"] == expected_pct, f"Expected pct={expected_pct}, got {p['pct']}"
    assert p["current_step"] == 2


def test_read_tutorial_markdown_returns_nonempty():
    from service.isaac_assist_service.multimodal.onboarding_tutorial import read_tutorial_markdown
    content = read_tutorial_markdown()
    assert isinstance(content, str)
    assert len(content) > 100, "Tutorial markdown content is too short"
    assert "Isaac Assist" in content
