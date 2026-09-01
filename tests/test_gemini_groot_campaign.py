from pathlib import Path

import json

from scripts.run_gemini_groot_campaign import (
    command_for_variation,
    contact_admission_for_episode,
    plan_variations,
    world_effect_acceptance_for_attempt,
)


def test_campaign_plan_is_reproducible_bounded_and_diverse():
    first = plan_variations(
        12,
        seed=4,
        object_xy=0.06,
        plate_xy=0.05,
        yaw_degrees=90.0,
        light_min=1800.0,
        light_max=8500.0,
    )
    second = plan_variations(
        12,
        seed=4,
        object_xy=0.06,
        plate_xy=0.05,
        yaw_degrees=90.0,
        light_min=1800.0,
        light_max=8500.0,
    )
    assert first == second
    assert len({round(item["movable_object_yaw_deg"], 4) for item in first}) == 12
    assert all(
        abs(value) <= 0.06
        for item in first
        for value in item["movable_object_offset_xy_m"]
    )
    assert all(abs(value) <= 0.05 for item in first for value in item["plate_offset_xy_m"])
    assert all(1800.0 <= item["sphere_light_intensity"] <= 8500.0 for item in first)


def test_campaign_command_points_at_success_gated_training_output():
    variation = plan_variations(
        1,
        seed=1,
        object_xy=0.02,
        plate_xy=0.02,
        yaw_degrees=30.0,
        light_min=2000.0,
        light_max=3000.0,
        start_index=9,
    )[0]
    command = command_for_variation(
        variation,
        episode_dir=Path("/tmp/episodes"),
        artifact_dir=Path("/tmp/attempt"),
        headless=True,
        randomize_background=True,
        movable_object_asset="bagel_06",
        movable_object_label="bagel",
        instruction="Use the observed object axis to align the gripper.",
        task="BlocksInBinTask",
    )
    assert "--training-episode-dir" in command
    assert "--guarded-world-effect-execution" in command
    assert command[command.index("--task") + 1] == "BlocksInBinTask"
    assert command[command.index("--episode-index") + 1] == "9"
    assert "--movable-object-yaw-deg" in command
    assert command[command.index("--movable-object-asset") + 1] == "bagel_06"
    assert command[command.index("--movable-object-label") + 1] == "bagel"
    assert command[command.index("--instruction") + 1] == (
        "Use the observed object axis to align the gripper."
    )
    assert "--light-intensity" in command
    assert "--randomize-background" in command
    assert command[command.index("--viz") + 1] == "none"
    assert "--no-periodic-motion-observations" in command
    assert "--no-ros2-sensor-ingress" in command


def test_campaign_can_rotate_across_scene_role_assets():
    variations = plan_variations(
        4,
        seed=0,
        object_xy=0.01,
        plate_xy=0.01,
        yaw_degrees=10.0,
        light_min=2000.0,
        light_max=3000.0,
        movable_object_assets=("red_block", "blue_block"),
        target_receptacle_assets=("grey_bin",),
    )

    assert {item["movable_object_asset"] for item in variations} == {
        "red_block",
        "blue_block",
    }
    assert {item["target_receptacle_asset"] for item in variations} == {
        "grey_bin"
    }


def test_campaign_counts_only_manifest_rows_with_passing_contact_gate(tmp_path):
    manifest = tmp_path / "collection_manifest.jsonl"
    rows = [
        {"episode_index": 3, "contact_telemetry": {"passed": False}},
        {
            "episode_index": 4,
            "contact_telemetry": {
                "passed": True,
                "coverage": 1.0,
                "touch_samples": 12,
            },
        },
    ]
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    assert contact_admission_for_episode(tmp_path, 3)["passed"] is False
    assert contact_admission_for_episode(tmp_path, 4)["touch_samples"] == 12
    assert contact_admission_for_episode(tmp_path, 5) is None


def test_campaign_reads_world_effect_acceptance(tmp_path):
    assert world_effect_acceptance_for_attempt(tmp_path) is None
    (tmp_path / "episode_acceptance.json").write_text(
        json.dumps({"accepted": True, "rejection_reasons": []})
    )
    assert world_effect_acceptance_for_attempt(tmp_path)["accepted"] is True
