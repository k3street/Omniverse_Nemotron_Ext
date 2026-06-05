"""Phase 106 — Post-release retrospective + roadmap tests.

Gate: template renders correctly, priority classifier is deterministic.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_data():
    """Return a minimal RetrospectiveData for render tests."""
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        ActionItem,
        ReleaseMetrics,
        RetrospectiveData,
    )

    metrics = ReleaseMetrics(
        tests_passed=120,
        tests_failed=0,
        phases_landed_pct=95.0,
        p95_latency_ms=42.5,
        error_rate_pct=0.01,
    )
    return RetrospectiveData(
        release_version="v1.0.0",
        release_date="2026-05-13",
        primary_goals=["Ship Phase 106", "Pass all gates"],
        success_metrics=["All tests green", "p95 < 100 ms"],
        went_well=["Deployment was smooth", "Docs ready on day one"],
        didnt_go_well=["One flaky test in CI"],
        surprises=["User adoption 2× forecast"],
        metrics=metrics,
        action_items=[
            ActionItem(
                owner="alice",
                action="Fix the flaky test",
                priority="P1",
                due="2026-05-20",
            )
        ],
        next_quarter=["Improve p95 to < 30 ms", "Add multi-language support"],
        backlog=["Dark mode", "Mobile app"],
        acknowledgments=["Alice", "Bob"],
    )


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


def test_phase_106_metadata():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        get_phase_metadata,
    )

    md = get_phase_metadata()
    assert md["phase"] == 106


def test_phase_106_status_landed():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        get_phase_metadata,
    )

    md = get_phase_metadata()
    assert md["status"] == "landed"


# ---------------------------------------------------------------------------
# 2. Template file existence and required h2 sections
# ---------------------------------------------------------------------------


def test_template_file_exists():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        _DEFAULT_TEMPLATE_PATH,
    )

    assert _DEFAULT_TEMPLATE_PATH.exists(), (
        f"Template not found at {_DEFAULT_TEMPLATE_PATH}"
    )


def test_template_has_required_h2_sections():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        _DEFAULT_TEMPLATE_PATH,
    )

    text = _DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8")
    required_sections = [
        "## Release Summary",
        "## What Went Well",
        "## What Didn't Go Well",
        "## Surprises",
        "## Metrics",
        "## Action Items",
        "## Roadmap",
        "## Acknowledgments",
    ]
    for section in required_sections:
        assert section in text, f"Missing required section: {section!r}"


def test_template_has_roadmap_subsections():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        _DEFAULT_TEMPLATE_PATH,
    )

    text = _DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "### Next Quarter" in text
    assert "### Backlog" in text


def test_template_has_action_items_table_header():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        _DEFAULT_TEMPLATE_PATH,
    )

    text = _DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "| Owner |" in text
    assert "| Action |" in text
    assert "| Priority |" in text
    assert "| Due |" in text


# ---------------------------------------------------------------------------
# 3. classify_priority — deterministic
# ---------------------------------------------------------------------------


def test_classify_priority_p0_security():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("security audit failure") == "P0"


def test_classify_priority_p0_outage():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("Fix the production outage immediately") == "P0"


def test_classify_priority_p0_data_loss():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("Prevent data_loss in migration script") == "P0"


def test_classify_priority_p0_regression():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("Resolve the regression in joint limits") == "P0"


def test_classify_priority_p1_bug():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("fix the auth bug") == "P1"


def test_classify_priority_p1_blocker():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("This is a release blocker — fix CI") == "P1"


def test_classify_priority_p2_refactor():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("refactor logging module") == "P2"


def test_classify_priority_p2_improvement():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("improvement to the dashboard UX") == "P2"


def test_classify_priority_p3_fallback():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    assert classify_priority("polish docs for v1.1") == "P3"


def test_classify_priority_custom_urgency_keywords():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    # "critical" is not in the default list — but we supply it as a custom keyword
    assert classify_priority("critical deployment issue", urgency_keywords=["critical"]) == "P0"


def test_classify_priority_empty_urgency_keywords_disables_p0():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        classify_priority,
    )

    # With empty keyword list, "security" no longer triggers P0
    result = classify_priority("security check needed", urgency_keywords=[])
    assert result in ("P1", "P2", "P3")  # must NOT be P0


# ---------------------------------------------------------------------------
# 4. RetrospectiveBuilder.render — correctness
# ---------------------------------------------------------------------------


def test_render_fills_release_version():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        RetrospectiveBuilder,
    )

    data = _make_minimal_data()
    builder = RetrospectiveBuilder()
    rendered = builder.render(data)
    assert "v1.0.0" in rendered
    assert "{release_version}" not in rendered


def test_render_fills_release_date():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        RetrospectiveBuilder,
    )

    data = _make_minimal_data()
    builder = RetrospectiveBuilder()
    rendered = builder.render(data)
    assert "2026-05-13" in rendered
    assert "{release_date}" not in rendered


def test_render_formats_went_well_as_bullets():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        RetrospectiveBuilder,
    )

    data = _make_minimal_data()
    builder = RetrospectiveBuilder()
    rendered = builder.render(data)
    assert "- Deployment was smooth" in rendered
    assert "- Docs ready on day one" in rendered


def test_render_formats_action_items_as_table_rows():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        RetrospectiveBuilder,
    )

    data = _make_minimal_data()
    builder = RetrospectiveBuilder()
    rendered = builder.render(data)
    assert "| alice |" in rendered
    assert "| P1 |" in rendered
    assert "2026-05-20" in rendered


def test_render_formats_metrics_as_sub_bullets():
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        RetrospectiveBuilder,
    )

    data = _make_minimal_data()
    builder = RetrospectiveBuilder()
    rendered = builder.render(data)
    assert "**tests_passed**" in rendered
    assert "**tests_failed**" in rendered
    assert "**phases_landed_pct**" in rendered
    assert "**p95_latency_ms**" in rendered
    assert "**error_rate_pct**" in rendered


def test_render_no_placeholder_tokens_remain():
    """After render, no {key} tokens from the data fields should be left."""
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        RetrospectiveBuilder,
    )

    data = _make_minimal_data()
    builder = RetrospectiveBuilder()
    rendered = builder.render(data)
    data_placeholders = [
        "{release_version}",
        "{release_date}",
        "{primary_goals}",
        "{success_metrics}",
        "{went_well}",
        "{didnt_go_well}",
        "{surprises}",
        "{metrics}",
        "{action_items}",
        "{next_quarter}",
        "{backlog}",
        "{acknowledgments}",
    ]
    for ph in data_placeholders:
        assert ph not in rendered, f"Placeholder {ph!r} was not replaced"


def test_render_handles_empty_lists_gracefully():
    """render must not crash when optional list fields are empty."""
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        ReleaseMetrics,
        RetrospectiveBuilder,
        RetrospectiveData,
    )

    metrics = ReleaseMetrics(
        tests_passed=0,
        tests_failed=0,
        phases_landed_pct=0.0,
        p95_latency_ms=0.0,
        error_rate_pct=0.0,
    )
    data = RetrospectiveData(
        release_version="v0.0.1",
        release_date="2026-01-01",
        primary_goals=[],
        success_metrics=[],
        went_well=[],
        didnt_go_well=[],
        surprises=[],
        metrics=metrics,
        action_items=[],
        next_quarter=[],
        backlog=[],
        acknowledgments=[],
    )
    builder = RetrospectiveBuilder()
    rendered = builder.render(data)
    # Should not crash and should still produce non-empty output
    assert len(rendered) > 100
    # Empty lists produce the sentinel phrase
    assert "_None recorded._" in rendered


def test_render_with_custom_template_path():
    """Builder accepts a custom template path and renders from it."""
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        RetrospectiveBuilder,
        ReleaseMetrics,
        RetrospectiveData,
    )

    minimal_template = "# {release_version} Retro\nDate: {release_date}\n"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(minimal_template)
        tmp_path = Path(f.name)

    try:
        metrics = ReleaseMetrics(
            tests_passed=1,
            tests_failed=0,
            phases_landed_pct=100.0,
            p95_latency_ms=10.0,
            error_rate_pct=0.0,
        )
        data = RetrospectiveData(
            release_version="v99.0.0",
            release_date="2099-12-31",
            primary_goals=[],
            success_metrics=[],
            went_well=[],
            didnt_go_well=[],
            surprises=[],
            metrics=metrics,
        )
        builder = RetrospectiveBuilder(template_path=tmp_path)
        rendered = builder.render(data)
        assert "v99.0.0" in rendered
        assert "2099-12-31" in rendered
    finally:
        tmp_path.unlink(missing_ok=True)


def test_render_multiple_action_items():
    """Multiple action items each produce a separate table row."""
    from service.isaac_assist_service.multimodal.post_release_retrospective import (
        ActionItem,
        RetrospectiveBuilder,
    )

    data = _make_minimal_data()
    data.action_items = [
        ActionItem(owner="alice", action="Fix regression in loader", priority="P0", due="2026-05-15"),
        ActionItem(owner="bob", action="refactor cache layer", priority="P2", due="2026-06-01"),
        ActionItem(owner="carol", action="Polish docs", priority="P3", due="2026-06-30"),
    ]
    builder = RetrospectiveBuilder()
    rendered = builder.render(data)
    assert "| alice |" in rendered
    assert "| bob |" in rendered
    assert "| carol |" in rendered
    assert "P0" in rendered
    assert "P2" in rendered
    assert "P3" in rendered
