from pathlib import Path

from scripts.run_gemini_groot_campaign import command_for_variation, plan_variations


def test_campaign_plan_is_reproducible_bounded_and_diverse():
    first = plan_variations(
        12,
        seed=4,
        banana_xy=0.06,
        plate_xy=0.05,
        yaw_degrees=90.0,
        light_min=1800.0,
        light_max=8500.0,
    )
    second = plan_variations(
        12,
        seed=4,
        banana_xy=0.06,
        plate_xy=0.05,
        yaw_degrees=90.0,
        light_min=1800.0,
        light_max=8500.0,
    )
    assert first == second
    assert len({round(item["banana_yaw_deg"], 4) for item in first}) == 12
    assert all(abs(value) <= 0.06 for item in first for value in item["banana_offset_xy_m"])
    assert all(abs(value) <= 0.05 for item in first for value in item["plate_offset_xy_m"])
    assert all(1800.0 <= item["sphere_light_intensity"] <= 8500.0 for item in first)


def test_campaign_command_points_at_success_gated_training_output():
    variation = plan_variations(
        1,
        seed=1,
        banana_xy=0.02,
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
    )
    assert "--training-episode-dir" in command
    assert command[command.index("--episode-index") + 1] == "9"
    assert "--banana-yaw-deg" in command
    assert "--light-intensity" in command
    assert "--randomize-background" in command
    assert "--headless" in command
