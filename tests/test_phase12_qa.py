"""Phase 12 — Agent-Driven QA infrastructure tests.

Covers:
* Session prompt assembly (section ordering + content inclusion + modifier echo)
* Persona-specific modifier clamps
* Modifier randomization distribution sanity
* Persona / task / supporting-doc files exist and are non-empty
* Judge rubric integrity (5 criteria, weights sum to 100, weighted_total math)
* Aggregate report structure and per-persona rollups
* Launcher smoke-test in --dry-run (no subprocess) writes JSONL transcript

All tests are L0 (no external services). Mocks subprocess for the launcher.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from unittest.mock import patch

import pytest

# Skip cleanly if pytest is invoked from a stripped checkout
pytest.importorskip("pytest")

from scripts.qa import (
    aggregate_results as agg,
    build_session_prompt as bsp,
    judge_session as judge,
    launch_campaign as launcher,
)


pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# QA asset fixtures (paths to the real docs/qa dir)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
QA_DIR = REPO_ROOT / "docs" / "qa"


@pytest.fixture()
def qa_dir() -> Path:
    return QA_DIR


# ---------------------------------------------------------------------------
# QA assets exist and are non-trivial
# ---------------------------------------------------------------------------


class TestQaAssetsExist:
    def test_qa_dir_exists(self, qa_dir: Path):
        assert qa_dir.exists() and qa_dir.is_dir()

    def test_supporting_docs_present(self, qa_dir: Path):
        for name in ("modifiers.md", "interaction_rules.md", "session_template.md", "judge_rubric.md"):
            p = qa_dir / name
            assert p.exists(), f"missing {p}"
            assert len(p.read_text(encoding="utf-8")) > 200, f"{name} suspiciously empty"

    def test_personas_dir_has_at_least_15(self, qa_dir: Path):
        personas = list((qa_dir / "personas").glob("*.md"))
        assert len(personas) >= 15, f"expected at least 15 personas, got {len(personas)}"

    def test_each_persona_is_readable(self, qa_dir: Path):
        for path in (qa_dir / "personas").glob("*.md"):
            text = path.read_text(encoding="utf-8")
            assert len(text) > 80, f"persona {path.name} suspiciously short"
            # Must be addressed in second-person
            assert "You are" in text, f"persona {path.name} not in second-person voice"

    def test_tasks_dir_has_required_samples(self, qa_dir: Path):
        tasks = {p.stem for p in (qa_dir / "tasks").glob("*.md")}
        for required in ("M-01", "E-01", "S-01", "P-01", "K-01"):
            assert required in tasks, f"required sample task {required} missing"
        assert len(tasks) >= 5

    def test_each_task_has_success_criterion(self, qa_dir: Path):
        for path in (qa_dir / "tasks").glob("*.md"):
            text = path.read_text(encoding="utf-8").lower()
            assert "success criterion" in text, f"task {path.name} lacks Success criterion"


# ---------------------------------------------------------------------------
# Modifier randomization
# ---------------------------------------------------------------------------


class TestModifiers:
    def test_modifier_dimensions_are_complete(self):
        for dim in ("patience", "emotion", "time_pressure", "vocabulary_drift", "attention"):
            assert dim in bsp.MODIFIER_VALUES
            assert len(bsp.MODIFIER_VALUES[dim]) >= 3

    def test_random_modifiers_returns_valid_values(self):
        rng = random.Random(0)
        m = bsp.random_modifiers("01_maya", rng=rng)
        assert m.patience in bsp.MODIFIER_VALUES["patience"]
        assert m.emotion in bsp.MODIFIER_VALUES["emotion"]
        assert m.time_pressure in bsp.MODIFIER_VALUES["time_pressure"]
        assert m.vocabulary_drift in bsp.MODIFIER_VALUES["vocabulary_drift"]
        assert m.attention in bsp.MODIFIER_VALUES["attention"]

    def test_persona_clamp_alex_never_reads_fully(self):
        rng = random.Random(0)
        for _ in range(200):
            m = bsp.random_modifiers("08_alex", rng=rng)
            assert m.attention != "reads_fully"

    def test_persona_clamp_thomas_always_reads_fully(self):
        rng = random.Random(123)
        for _ in range(200):
            m = bsp.random_modifiers("07_thomas", rng=rng)
            assert m.attention == "reads_fully"
            assert m.vocabulary_drift == "consistent"

    def test_modifier_distribution_covers_all_values(self):
        rng = random.Random(42)
        seen = {dim: set() for dim in bsp.MODIFIER_VALUES}
        for _ in range(500):
            m = bsp.random_modifiers("01_maya", rng=rng)
            seen["patience"].add(m.patience)
            seen["emotion"].add(m.emotion)
            seen["time_pressure"].add(m.time_pressure)
            seen["vocabulary_drift"].add(m.vocabulary_drift)
            seen["attention"].add(m.attention)
        for dim, values in bsp.MODIFIER_VALUES.items():
            assert seen[dim] == set(values), f"dim {dim} did not cover all values: {seen[dim]} vs {values}"

    def test_modifiers_dataclass_serializes(self):
        rng = random.Random(7)
        m = bsp.random_modifiers("01_maya", rng=rng)
        d = m.as_dict()
        assert set(d.keys()) == {"patience", "emotion", "time_pressure", "vocabulary_drift", "attention"}


# ---------------------------------------------------------------------------
# Session prompt assembly
# ---------------------------------------------------------------------------


class TestSessionPromptAssembly:
    def test_section_markers_appear_in_order(self, qa_dir: Path):
        prompt = bsp.build_session_prompt(
            "01_maya",
            "M-01",
            modifiers=bsp.Modifiers(
                patience=3,
                emotion="frustrated",
                time_pressure="deadline_today",
                vocabulary_drift="consistent",
                attention="reads_fully",
            ),
            qa_dir=qa_dir,
        )
        positions = [prompt.find(marker) for marker in bsp.SECTION_MARKERS]
        assert -1 not in positions, f"missing marker; positions={positions}"
        assert positions == sorted(positions), "section markers not in expected order"

    def test_prompt_includes_persona_text(self, qa_dir: Path):
        prompt = bsp.build_session_prompt(
            "01_maya",
            "M-01",
            modifiers=bsp.Modifiers(3, "baseline", "relaxed", "consistent", "reads_fully"),
            qa_dir=qa_dir,
        )
        assert "Maya Chen" in prompt
        assert "IsaacLab" in prompt

    def test_prompt_includes_task_text(self, qa_dir: Path):
        prompt = bsp.build_session_prompt(
            "01_maya",
            "M-01",
            modifiers=bsp.Modifiers(3, "baseline", "relaxed", "consistent", "reads_fully"),
            qa_dir=qa_dir,
        )
        assert "Franka" in prompt
        assert "Success criterion" in prompt

    def test_prompt_echoes_modifiers(self, qa_dir: Path):
        mods = bsp.Modifiers(7, "stressed", "panic", "swearing_when_frustrated", "skips_to_code")
        prompt = bsp.build_session_prompt("01_maya", "M-01", modifiers=mods, qa_dir=qa_dir)
        assert "Patience: 7" in prompt
        assert "Emotional baseline: stressed" in prompt
        assert "Time pressure: panic" in prompt
        assert "Vocabulary drift: swearing_when_frustrated" in prompt
        assert "Reading attention: skips_to_code" in prompt

    def test_prompt_starts_with_persona_block(self, qa_dir: Path):
        prompt = bsp.build_session_prompt(
            "01_maya",
            "M-01",
            modifiers=bsp.Modifiers(3, "baseline", "relaxed", "consistent", "reads_fully"),
            qa_dir=qa_dir,
        )
        # Persona must come BEFORE the first section marker
        first_marker_idx = prompt.find(bsp.SECTION_MARKERS[0])
        assert "You are" in prompt[:first_marker_idx]

    def test_prompt_missing_persona_raises(self, qa_dir: Path):
        with pytest.raises(FileNotFoundError):
            bsp.build_session_prompt(
                "99_doesnotexist",
                "M-01",
                modifiers=bsp.Modifiers(3, "baseline", "relaxed", "consistent", "reads_fully"),
                qa_dir=qa_dir,
            )

    def test_prompt_missing_task_raises(self, qa_dir: Path):
        with pytest.raises(FileNotFoundError):
            bsp.build_session_prompt(
                "01_maya",
                "Z-99",
                modifiers=bsp.Modifiers(3, "baseline", "relaxed", "consistent", "reads_fully"),
                qa_dir=qa_dir,
            )


# ---------------------------------------------------------------------------
# Judge rubric integrity
# ---------------------------------------------------------------------------


class TestJudgeRubric:
    def test_five_criteria(self):
        assert len(judge.CRITERIA) == 5

    def test_weights_sum_to_100(self):
        assert sum(judge.CRITERIA.values()) == 100

    def test_criterion_names_match_rubric_doc(self, qa_dir: Path):
        rubric_text = (qa_dir / "judge_rubric.md").read_text(encoding="utf-8").lower()
        for crit in judge.CRITERIA:
            humanized = crit.replace("_", " ")
            assert humanized in rubric_text, f"{crit} not mentioned in rubric doc"

    def test_weighted_total_perfect_score(self):
        scores = {k: 5 for k in judge.CRITERIA}
        assert judge.weighted_total(scores) == 100

    def test_weighted_total_minimum_score(self):
        scores = {k: 1 for k in judge.CRITERIA}
        assert judge.weighted_total(scores) == 20

    def test_weighted_total_mixed(self):
        scores = {
            "technical_accuracy": 4,
            "actionability": 5,
            "persona_calibration": 3,
            "response_economy": 4,
            "hallucination_absence": 5,
        }
        # 30*4 + 25*5 + 20*3 + 15*4 + 10*5 = 120+125+60+60+50 = 415; /5 = 83
        assert judge.weighted_total(scores) == 83

    def test_weighted_total_rejects_out_of_range(self):
        scores = {k: 5 for k in judge.CRITERIA}
        scores["actionability"] = 6
        with pytest.raises(ValueError):
            judge.weighted_total(scores)

    def test_weighted_total_rejects_missing(self):
        scores = {k: 3 for k in list(judge.CRITERIA)[:4]}
        with pytest.raises(ValueError):
            judge.weighted_total(scores)


# ---------------------------------------------------------------------------
# Self-verdict scrubbing
# ---------------------------------------------------------------------------


class TestSelfVerdictScrubbing:
    def test_strips_explicit_score_lines(self):
        lines = [
            "I think this answer is great",
            "Overall verdict: 4/5",
            "Let me try the next step.",
            "I would rate this 3 out of 10",
        ]
        kept = judge.scrub_self_verdicts(lines)
        assert "I think this answer is great" in kept
        assert "Let me try the next step." in kept
        assert all("4/5" not in ln for ln in kept)
        assert all("rate this" not in ln.lower() for ln in kept)

    def test_keeps_legitimate_reactions(self):
        lines = ["Hmm, the OmniGraph node names look wrong.", "ok cool, trying that now"]
        assert judge.scrub_self_verdicts(lines) == lines


# ---------------------------------------------------------------------------
# Judge backend (stub)
# ---------------------------------------------------------------------------


class TestJudgeStubBackend:
    def test_stub_grade_returns_full_schema(self):
        backend = judge.StubJudgeBackend()
        result = backend.grade("ignored prompt")
        assert "scores" in result
        assert set(result["scores"]) == set(judge.CRITERIA)
        assert "completion" in result
        assert "missing_tools" in result
        assert "failure_modes" in result

    def test_judge_session_with_stub(self, tmp_path: Path):
        # Build a minimal transcript on disk
        transcript_path = tmp_path / "01_maya__M-01.jsonl"
        with transcript_path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "event": "session_start",
                "persona": "01_maya",
                "task": "M-01",
                "modifiers": {"patience": 3, "emotion": "baseline"},
                "prompt": "...",
            }) + "\n")
            fh.write(json.dumps({
                "event": "claude_stdout_line",
                "text": "I tried importing the URDF and it segfaulted.",
            }) + "\n")
            fh.write(json.dumps({
                "event": "session_end",
                "rc": 0,
                "duration_s": 10.0,
                "estimated_cost_usd": 0.05,
            }) + "\n")

        verdict = judge.judge_session(transcript_path, backend=judge.StubJudgeBackend())
        assert verdict["session_id"] == "01_maya__M-01"
        assert verdict["weighted_total"] == 60  # all 3s -> 60
        assert verdict["completion"] in judge.VALID_COMPLETIONS

    def test_judge_session_validates_completion_value(self, tmp_path: Path):
        transcript_path = tmp_path / "01_maya__M-01.jsonl"
        transcript_path.write_text(
            json.dumps({"event": "session_start", "persona": "01_maya", "task": "M-01"}) + "\n",
            encoding="utf-8",
        )

        class BadBackend:
            def grade(self, _prompt):
                return {
                    "scores": {k: 3 for k in judge.CRITERIA},
                    "completion": "kinda",
                }

        with pytest.raises(ValueError):
            judge.judge_session(transcript_path, backend=BadBackend())


# ---------------------------------------------------------------------------
# Judge output parser
# ---------------------------------------------------------------------------


class TestJudgeOutputParser:
    def test_parses_bare_json_object(self):
        raw = json.dumps({"scores": {k: 4 for k in judge.CRITERIA}})
        out = judge._parse_judge_output(raw)
        assert out["scores"]["actionability"] == 4

    def test_unwraps_claude_envelope(self):
        inner = {"scores": {k: 5 for k in judge.CRITERIA}}
        envelope = {"result": json.dumps(inner)}
        out = judge._parse_judge_output(json.dumps(envelope))
        assert out["scores"]["technical_accuracy"] == 5

    def test_extracts_json_from_chatty_text(self):
        raw = "Here is my verdict:\n" + json.dumps({"scores": {k: 2 for k in judge.CRITERIA}})
        out = judge._parse_judge_output(raw)
        assert out["scores"]["technical_accuracy"] == 2

    def test_empty_output_raises(self):
        with pytest.raises(ValueError):
            judge._parse_judge_output("")


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------


SAMPLE_VERDICTS = [
    {
        "session_id": "01_maya__M-01",
        "scores": {
            "technical_accuracy": 5,
            "actionability": 4,
            "persona_calibration": 4,
            "response_economy": 4,
            "hallucination_absence": 5,
        },
        "weighted_total": 87,
        "completion": "completed",
        "missing_tools": [],
        "failure_modes": [],
    },
    {
        "session_id": "01_maya__M-02",
        "scores": {
            "technical_accuracy": 3,
            "actionability": 2,
            "persona_calibration": 3,
            "response_economy": 4,
            "hallucination_absence": 4,
        },
        "weighted_total": 60,
        "completion": "partial",
        "missing_tools": ["physx_validate_asset"],
        "failure_modes": ["wall-of-text", "missed-version-difference"],
    },
    {
        "session_id": "08_alex__A-01",
        "scores": {
            "technical_accuracy": 4,
            "actionability": 1,
            "persona_calibration": 2,
            "response_economy": 1,
            "hallucination_absence": 5,
        },
        "weighted_total": 47,
        "completion": "abandoned",
        "missing_tools": ["one_click_simulate"],
        "failure_modes": ["wall-of-text"],
    },
]


class TestAggregate:
    def test_session_count(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        assert report.session_count == 3

    def test_completion_rate(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        assert report.completion_rate == pytest.approx(1 / 3)

    def test_completion_counts(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        assert report.completion_counts["completed"] == 1
        assert report.completion_counts["partial"] == 1
        assert report.completion_counts["abandoned"] == 1

    def test_weighted_totals(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        assert report.weighted_total_mean == pytest.approx((87 + 60 + 47) / 3)
        assert report.weighted_total_median == 60

    def test_per_persona_rollup(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        assert "01_maya" in report.per_persona
        assert report.per_persona["01_maya"]["session_count"] == 2
        assert report.per_persona["01_maya"]["completed"] == 1
        assert report.per_persona["01_maya"]["completion_rate"] == 0.5

    def test_per_task_rollup(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        assert "M-01" in report.per_task
        assert report.per_task["M-01"]["completed"] == 1

    def test_top_failure_modes_counts_correctly(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        modes = dict(report.top_failure_modes)
        assert modes["wall-of-text"] == 2
        assert modes["missed-version-difference"] == 1

    def test_top_missing_tools(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        names = [t for t, _ in report.top_missing_tools]
        assert "physx_validate_asset" in names
        assert "one_click_simulate" in names

    def test_render_text_runs(self):
        report = agg.aggregate(SAMPLE_VERDICTS)
        text = agg.render_text(report)
        assert "Campaign: 3 sessions" in text
        assert "Top failure modes" in text
        assert "physx_validate_asset" in text

    def test_aggregate_empty(self):
        report = agg.aggregate([])
        assert report.session_count == 0
        assert report.completion_rate == 0.0
        assert report.weighted_total_mean == 0.0

    def test_load_verdicts_from_dir(self, tmp_path: Path):
        for i, v in enumerate(SAMPLE_VERDICTS):
            (tmp_path / f"v{i}.json").write_text(json.dumps(v), encoding="utf-8")
        loaded = agg.load_verdicts_from_dir(tmp_path)
        assert len(loaded) == 3

    def test_load_verdicts_from_jsonl(self, tmp_path: Path):
        path = tmp_path / "all.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for v in SAMPLE_VERDICTS:
                fh.write(json.dumps(v) + "\n")
        loaded = agg.load_verdicts_from_jsonl(path)
        assert len(loaded) == 3


# ---------------------------------------------------------------------------
# Launcher (dry-run + plan)
# ---------------------------------------------------------------------------


class TestLauncher:
    def test_list_personas_and_tasks(self):
        personas = launcher.list_personas()
        tasks = launcher.list_tasks()
        assert "01_maya" in personas
        assert "M-01" in tasks

    def test_full_plan_is_cartesian(self):
        plan = launcher.build_full_plan()
        n_personas = len(launcher.list_personas())
        n_tasks = len(launcher.list_tasks())
        assert len(plan) == n_personas * n_tasks

    def test_dry_run_writes_transcript(self, tmp_path: Path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        result = launcher.run_session(
            launcher.CampaignItem(persona="01_maya", task="M-01"),
            run_dir=run_dir,
            dry_run=True,
            rng=random.Random(0),
        )
        assert result.transcript_path.exists()
        with result.transcript_path.open() as fh:
            events = [json.loads(ln) for ln in fh if ln.strip()]
        kinds = [e["event"] for e in events]
        assert kinds[0] == "session_start"
        assert kinds[-1] == "session_end"
        assert any(e["event"] == "claude_stdout_line" for e in events)

    def test_run_campaign_dry_run_writes_manifest(self, tmp_path: Path):
        plan = [
            launcher.CampaignItem(persona="01_maya", task="M-01"),
            launcher.CampaignItem(persona="02_erik", task="E-01"),
        ]
        results = launcher.run_campaign(
            plan,
            runs_dir=tmp_path,
            run_id="run_test",
            dry_run=True,
            rng=random.Random(0),
        )
        assert len(results) == 2
        manifest_path = tmp_path / "run_test" / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["session_count"] == 2
        assert manifest["dry_run"] is True

    def test_estimate_cost_fallback(self):
        assert launcher.estimate_cost("not json") == launcher.COST_PER_SESSION_FALLBACK

    def test_estimate_cost_extracts_total_cost_usd(self):
        raw = json.dumps({"total_cost_usd": 0.42})
        assert launcher.estimate_cost(raw) == 0.42

    def test_load_plan_roundtrip(self, tmp_path: Path):
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(
            json.dumps([
                {"persona": "01_maya", "task": "M-01"},
                {"persona": "02_erik", "task": "E-01"},
            ]),
            encoding="utf-8",
        )
        plan = launcher.load_plan(plan_path)
        assert plan[0].persona == "01_maya"
        assert plan[1].task == "E-01"

    def test_load_plan_rejects_bad_entry(self, tmp_path: Path):
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps([{"persona": "x"}]), encoding="utf-8")
        with pytest.raises(ValueError):
            launcher.load_plan(plan_path)

    def test_real_subprocess_path_uses_subprocess_run(self, tmp_path: Path):
        """Smoke-test the non-dry-run path WITHOUT actually spawning Claude.

        We patch subprocess.run so the launcher never touches the real binary.
        """

        class FakeProc:
            stdout = json.dumps({"total_cost_usd": 0.07, "result": "ok"})
            stderr = ""
            returncode = 0

        with patch.object(launcher.subprocess, "run", return_value=FakeProc()) as mocked:
            result = launcher.run_session(
                launcher.CampaignItem(persona="01_maya", task="M-01"),
                run_dir=tmp_path,
                dry_run=False,
                rng=random.Random(0),
            )
            assert mocked.call_count == 1
            assert result.estimated_cost_usd == 0.07
            assert result.rc == 0
