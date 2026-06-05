"""Phase 100 — Arena benchmark: hand-crafted vs IA-authored (SPEC layer).

Provides the scenario registry, scoring rubric, and head-to-head
comparator for the Arena benchmark suite.  This module is pure data +
Python — it does NOT require a running Kit instance or GR00T.  The
runtime execution layer (Phase 100 opus-runtime) wraps this module.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 100.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

PHASE_ID = 100
PHASE_TITLE = "Arena benchmark: hand-crafted vs IA-authored"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 100",
    }


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Category = Literal[
    "pick_place",
    "assembly",
    "navigation",
    "inspection",
    "welding",
    "palletizing",
    "sorting",
    "kitting",
]

Difficulty = Literal["L1", "L2", "L3", "L4", "L5"]

Agent = Literal["hand_crafted", "IA_authored"]

Winner = Literal["hand_crafted", "IA_authored", "tie"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ArenaScenarioSpec:
    """Specification for a single Arena benchmark scenario."""

    scenario_id: str
    name: str
    category: Category
    difficulty: Difficulty
    max_score: float
    time_limit_s: float
    required_assets: List[str]
    success_criteria: List[str]
    hand_crafted_reference_score: Optional[float] = None


@dataclass
class BenchmarkResult:
    """Result produced by one agent running one scenario."""

    scenario_id: str
    agent: Agent
    score: float
    time_used_s: float
    success: bool
    notes: str = ""


@dataclass
class HeadToHeadComparison:
    """Head-to-head comparison of hand-crafted vs IA-authored on one scenario."""

    scenario_id: str
    hand_crafted_score: float
    ia_authored_score: float
    winner: Winner
    delta: float          # ia_authored_score - hand_crafted_score
    ia_wins: bool


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

ARENA_SCENARIOS: List[ArenaScenarioSpec] = [
    # ------------------------------------------------------------------ pick_place
    ArenaScenarioSpec(
        scenario_id="pp_01",
        name="single_cube_to_bin",
        category="pick_place",
        difficulty="L1",
        max_score=100.0,
        time_limit_s=30.0,
        required_assets=["Cube_4", "Bin_A"],
        success_criteria=["cube in bin", "no collisions"],
        hand_crafted_reference_score=95.0,
    ),
    ArenaScenarioSpec(
        scenario_id="pp_02",
        name="mixed_skus_4_objects",
        category="pick_place",
        difficulty="L2",
        max_score=100.0,
        time_limit_s=120.0,
        required_assets=["Cube_4", "Cylinder_A", "Sphere_B", "Bin_A", "Bin_B"],
        success_criteria=["all 4 objects in correct bins", "no drops"],
        hand_crafted_reference_score=88.0,
    ),
    ArenaScenarioSpec(
        scenario_id="pp_03",
        name="cluttered_bin_pick",
        category="pick_place",
        difficulty="L3",
        max_score=100.0,
        time_limit_s=180.0,
        required_assets=["ClutteredBin_Large", "Franka_Robot", "Camera_Top"],
        success_criteria=["target object picked", "surrounding objects undisturbed"],
        hand_crafted_reference_score=72.0,
    ),
    ArenaScenarioSpec(
        scenario_id="pp_04",
        name="fragile_glassware",
        category="pick_place",
        difficulty="L4",
        max_score=100.0,
        time_limit_s=240.0,
        required_assets=["GlassObject_Set", "Franka_Robot", "ForceTorqueSensor"],
        success_criteria=[
            "object placed without breakage",
            "peak contact force < 5 N",
        ],
        hand_crafted_reference_score=60.0,
    ),
    # ------------------------------------------------------------------ assembly
    ArenaScenarioSpec(
        scenario_id="as_01",
        name="snap_fit_2_part",
        category="assembly",
        difficulty="L2",
        max_score=100.0,
        time_limit_s=90.0,
        required_assets=["SnapFit_Part_A", "SnapFit_Part_B", "UR10_Robot"],
        success_criteria=["parts mated", "joint locked"],
        hand_crafted_reference_score=85.0,
    ),
    ArenaScenarioSpec(
        scenario_id="as_02",
        name="screw_assembly_3_part",
        category="assembly",
        difficulty="L3",
        max_score=100.0,
        time_limit_s=180.0,
        required_assets=["Baseplate", "Bracket", "Cover", "Screw_M4x10", "UR10_Robot"],
        success_criteria=["all 3 parts fastened", "torque within spec"],
        hand_crafted_reference_score=70.0,
    ),
    ArenaScenarioSpec(
        scenario_id="as_03",
        name="pcb_smt_placement",
        category="assembly",
        difficulty="L4",
        max_score=100.0,
        time_limit_s=300.0,
        required_assets=["PCB_Board", "SMD_Components_Set", "Delta_Robot"],
        success_criteria=[
            "all components placed within 0.1 mm tolerance",
            "no tombstoning",
        ],
        hand_crafted_reference_score=55.0,
    ),
    # ------------------------------------------------------------------ navigation
    ArenaScenarioSpec(
        scenario_id="nav_01",
        name="open_corridor_5m",
        category="navigation",
        difficulty="L1",
        max_score=100.0,
        time_limit_s=20.0,
        required_assets=["Corridor_5m", "Carter_Robot"],
        success_criteria=["reached goal ± 0.1 m", "no wall contact"],
        hand_crafted_reference_score=98.0,
    ),
    ArenaScenarioSpec(
        scenario_id="nav_02",
        name="static_obstacle_field",
        category="navigation",
        difficulty="L2",
        max_score=100.0,
        time_limit_s=60.0,
        required_assets=["Factory_Floor_Static", "Carter_Robot"],
        success_criteria=["reached goal", "path length < 1.3× optimal"],
        hand_crafted_reference_score=88.0,
    ),
    ArenaScenarioSpec(
        scenario_id="nav_03",
        name="dynamic_pedestrian_zone",
        category="navigation",
        difficulty="L3",
        max_score=100.0,
        time_limit_s=120.0,
        required_assets=["Pedestrian_Zone", "Carter_Robot", "Pedestrian_Set_5"],
        success_criteria=["reached goal", "no collisions", "0 e-stops triggered"],
        hand_crafted_reference_score=74.0,
    ),
    # ------------------------------------------------------------------ inspection
    ArenaScenarioSpec(
        scenario_id="insp_01",
        name="dimensional_qc",
        category="inspection",
        difficulty="L2",
        max_score=100.0,
        time_limit_s=90.0,
        required_assets=["Part_DimQC", "Camera_Structured_Light"],
        success_criteria=["all 5 dimensions measured", "error < 0.05 mm"],
        hand_crafted_reference_score=90.0,
    ),
    ArenaScenarioSpec(
        scenario_id="insp_02",
        name="surface_defect_3d",
        category="inspection",
        difficulty="L3",
        max_score=100.0,
        time_limit_s=150.0,
        required_assets=["SurfaceDefect_Panel", "Camera_3D_Scanner", "UR10_Robot"],
        success_criteria=[
            "defect regions detected (IoU > 0.75)",
            "no false negatives on critical zones",
        ],
        hand_crafted_reference_score=68.0,
    ),
    # ------------------------------------------------------------------ welding
    ArenaScenarioSpec(
        scenario_id="weld_01",
        name="linear_seam_500mm",
        category="welding",
        difficulty="L3",
        max_score=100.0,
        time_limit_s=120.0,
        required_assets=["WeldFixture_Linear", "UR10_Robot", "WeldTool"],
        success_criteria=[
            "seam length ≥ 490 mm",
            "lateral deviation < 1 mm",
        ],
        hand_crafted_reference_score=80.0,
    ),
    ArenaScenarioSpec(
        scenario_id="weld_02",
        name="complex_corner_joint",
        category="welding",
        difficulty="L5",
        max_score=100.0,
        time_limit_s=300.0,
        required_assets=["WeldFixture_Corner", "UR10_Robot", "WeldTool", "Camera_Vision"],
        success_criteria=[
            "all 4 corner segments welded",
            "no burn-through",
            "re-entry overlap 5-10 mm",
        ],
        hand_crafted_reference_score=45.0,
    ),
    # ------------------------------------------------------------------ palletizing
    ArenaScenarioSpec(
        scenario_id="pal_01",
        name="uniform_box_stack_4x4",
        category="palletizing",
        difficulty="L2",
        max_score=100.0,
        time_limit_s=180.0,
        required_assets=["Carton_Uniform_Set_16", "Pallet_EUR", "UR10_Robot"],
        success_criteria=["16 boxes placed", "stack height within 5 mm", "no tip-over"],
        hand_crafted_reference_score=88.0,
    ),
    ArenaScenarioSpec(
        scenario_id="pal_02",
        name="mixed_carton_layer",
        category="palletizing",
        difficulty="L3",
        max_score=100.0,
        time_limit_s=240.0,
        required_assets=["Carton_Mixed_Set", "Pallet_EUR", "UR10_Robot", "Camera_Top"],
        success_criteria=[
            "all cartons on pallet",
            "column stability index > 0.8",
        ],
        hand_crafted_reference_score=70.0,
    ),
    # ------------------------------------------------------------------ sorting
    ArenaScenarioSpec(
        scenario_id="sort_01",
        name="color_sort_4_classes",
        category="sorting",
        difficulty="L1",
        max_score=100.0,
        time_limit_s=60.0,
        required_assets=["ColorBlock_Set_20", "Conveyor_Short", "Bin_Set_4"],
        success_criteria=["classification accuracy ≥ 95%", "throughput ≥ 15 parts/min"],
        hand_crafted_reference_score=96.0,
    ),
    ArenaScenarioSpec(
        scenario_id="sort_02",
        name="barcode_routing",
        category="sorting",
        difficulty="L2",
        max_score=100.0,
        time_limit_s=120.0,
        required_assets=["Barcode_Part_Set_30", "Conveyor_Long", "Divert_Gates_3"],
        success_criteria=[
            "read rate ≥ 99%",
            "routing accuracy ≥ 98%",
        ],
        hand_crafted_reference_score=87.0,
    ),
    # ------------------------------------------------------------------ kitting
    ArenaScenarioSpec(
        scenario_id="kit_01",
        name="parts_to_tray_5_slot",
        category="kitting",
        difficulty="L2",
        max_score=100.0,
        time_limit_s=120.0,
        required_assets=["KitTray_5Slot", "Part_Set_5", "Franka_Robot"],
        success_criteria=["all 5 slots filled", "part orientation within 5°"],
        hand_crafted_reference_score=85.0,
    ),
    ArenaScenarioSpec(
        scenario_id="kit_02",
        name="kit_assembly_8_step",
        category="kitting",
        difficulty="L4",
        max_score=100.0,
        time_limit_s=360.0,
        required_assets=["KitTray_8Slot", "Part_Set_8_Mixed", "UR10_Robot", "Camera_Top"],
        success_criteria=[
            "all 8 slots correctly populated",
            "kit validated by vision system",
            "cycle time < 360 s",
        ],
        hand_crafted_reference_score=58.0,
    ),
]

# ---------------------------------------------------------------------------
# Scoring rubric
# ---------------------------------------------------------------------------


def score_against_rubric(
    time_used_s: float,
    time_limit_s: float,
    success: bool,
    max_score: float,
) -> float:
    """Score a benchmark run against the standard time-weighted rubric.

    Rules:
    - If not success → 0.0
    - Otherwise: max_score * (1 - 0.5 * (time_used_s / time_limit_s))
    - Clamped to [0.5 * max_score, max_score]
    """
    if not success:
        return 0.0
    raw = max_score * (1.0 - 0.5 * (time_used_s / time_limit_s))
    low = 0.5 * max_score
    return float(max(low, min(max_score, raw)))


# ---------------------------------------------------------------------------
# Runner — pure comparison logic (no Kit required)
# ---------------------------------------------------------------------------


class ArenaBenchmarkRunner:
    """Deterministic head-to-head comparator for Arena benchmark results."""

    def __init__(self, scenarios: Optional[List[ArenaScenarioSpec]] = None) -> None:
        self._scenarios: List[ArenaScenarioSpec] = scenarios if scenarios is not None else ARENA_SCENARIOS
        self._scenario_map: Dict[str, ArenaScenarioSpec] = {s.scenario_id: s for s in self._scenarios}

    # ------------------------------------------------------------------
    def compare(
        self,
        hand: List[BenchmarkResult],
        ia: List[BenchmarkResult],
    ) -> List[HeadToHeadComparison]:
        """Pair results by scenario_id and produce head-to-head comparisons.

        Tie threshold: |delta| < 0.01 * max_score for that scenario.
        """
        hand_by_id = {r.scenario_id: r for r in hand}
        ia_by_id = {r.scenario_id: r for r in ia}

        comparisons: List[HeadToHeadComparison] = []
        all_ids = sorted(set(hand_by_id) | set(ia_by_id))

        for sid in all_ids:
            h_score = hand_by_id[sid].score if sid in hand_by_id else 0.0
            i_score = ia_by_id[sid].score if sid in ia_by_id else 0.0
            delta = i_score - h_score

            # Determine tie threshold
            spec = self._scenario_map.get(sid)
            max_score = spec.max_score if spec is not None else 100.0
            tie_threshold = 0.01 * max_score

            if abs(delta) < tie_threshold:
                winner: Winner = "tie"
                ia_wins = False
            elif delta > 0:
                winner = "IA_authored"
                ia_wins = True
            else:
                winner = "hand_crafted"
                ia_wins = False

            comparisons.append(
                HeadToHeadComparison(
                    scenario_id=sid,
                    hand_crafted_score=h_score,
                    ia_authored_score=i_score,
                    winner=winner,
                    delta=delta,
                    ia_wins=ia_wins,
                )
            )

        return comparisons

    # ------------------------------------------------------------------
    def category_breakdown(
        self, comparisons: List[HeadToHeadComparison]
    ) -> Dict[str, Dict[str, Any]]:
        """Per-category statistics.

        Returns dict[category] = {n_scenarios, ia_wins, hand_wins, ties, avg_delta}
        """
        buckets: Dict[str, List[HeadToHeadComparison]] = {}
        for cmp in comparisons:
            spec = self._scenario_map.get(cmp.scenario_id)
            cat = spec.category if spec is not None else "unknown"
            buckets.setdefault(cat, []).append(cmp)

        result: Dict[str, Dict[str, Any]] = {}
        for cat, cmps in buckets.items():
            ia_w = sum(1 for c in cmps if c.winner == "IA_authored")
            hand_w = sum(1 for c in cmps if c.winner == "hand_crafted")
            ties = sum(1 for c in cmps if c.winner == "tie")
            avg_d = sum(c.delta for c in cmps) / len(cmps) if cmps else 0.0
            result[cat] = {
                "n_scenarios": len(cmps),
                "ia_wins": ia_w,
                "hand_wins": hand_w,
                "ties": ties,
                "avg_delta": avg_d,
            }
        return result

    # ------------------------------------------------------------------
    def difficulty_breakdown(
        self, comparisons: List[HeadToHeadComparison]
    ) -> Dict[str, Dict[str, Any]]:
        """Per-difficulty statistics.

        Returns dict[difficulty] = {n_scenarios, ia_wins, hand_wins, ties, avg_delta}
        """
        buckets: Dict[str, List[HeadToHeadComparison]] = {}
        for cmp in comparisons:
            spec = self._scenario_map.get(cmp.scenario_id)
            diff = spec.difficulty if spec is not None else "unknown"
            buckets.setdefault(diff, []).append(cmp)

        result: Dict[str, Dict[str, Any]] = {}
        for diff, cmps in buckets.items():
            ia_w = sum(1 for c in cmps if c.winner == "IA_authored")
            hand_w = sum(1 for c in cmps if c.winner == "hand_crafted")
            ties = sum(1 for c in cmps if c.winner == "tie")
            avg_d = sum(c.delta for c in cmps) / len(cmps) if cmps else 0.0
            result[diff] = {
                "n_scenarios": len(cmps),
                "ia_wins": ia_w,
                "hand_wins": hand_w,
                "ties": ties,
                "avg_delta": avg_d,
            }
        return result

    # ------------------------------------------------------------------
    def overall_ia_winrate(self, comparisons: List[HeadToHeadComparison]) -> float:
        """Fraction of scenarios where IA_authored wins outright (not tie)."""
        if not comparisons:
            return 0.0
        wins = sum(1 for c in comparisons if c.winner == "IA_authored")
        return wins / len(comparisons)
