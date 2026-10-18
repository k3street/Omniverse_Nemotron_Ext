"""The robot's collisions in a take (scripts/collision_measure.py): partners that
are neither the robot nor the target, counted as episodes over ticks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from collision_measure import partners, tally  # noqa: E402


def test_partners_are_what_the_robot_touches_that_is_not_itself_or_the_target():
    pairs = [("/World/Robot/links/psyonic_left_index_L2", "/World/Objects/Target/Bottle"),
             ("/World/Environment/Stations/InspectionBench/Top", "/World/Robot/links/psyonic_left_thumb_L2"),
             ("/World/Robot/links/a", "/World/Robot/links/b"),
             ("/World/Objects/Target/Bottle", "/World/Environment/Stations/InspectionBench/Top")]
    assert partners(pairs, target_paths=["/World/Objects/Target"]) == ["/World/Environment/Stations/InspectionBench/Top"]


def test_an_episode_is_a_partner_touched_after_a_tick_it_was_not():
    bench, wall = "/World/Environment/Stations/Bench/Top", "/World/Environment/Stations/Bin/WallXn"
    t = tally([[], [bench], [bench], [], [bench, wall], [wall], []])
    assert t["touch_ticks"] == 4 and t["episodes"] == 3 and t["by_partner"] == {bench: 2, wall: 1}
    assert tally([[], []]) == {"touch_ticks": 0, "episodes": 0, "by_partner": {}}


def test_a_tangential_push_on_a_rim_is_a_torque_about_the_axis():
    from collision_measure import contact_torque
    # one friction force of 2 N along +y at (0.022, 0, 0): +0.044 N m about +z; a radial normal force adds nothing
    tq = contact_torque([3.0], [[0.022, 0.0, 0.0]], [[-1.0, 0.0, 0.0]], [[1]], [[0]],
                        [[0.0, 2.0, 0.0]], [[0.022, 0.0, 0.0]], [[1]], [[0]], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    assert abs(tq - 0.044) < 1e-12
    # counted only within each pair's range
    tq = contact_torque([0.0, 0.0], [[0, 0, 0]] * 2, [[0, 0, 1]] * 2, [[0]], [[0]],
                        [[0.0, 2.0, 0.0], [0.0, 9.0, 0.0]], [[0.022, 0.0, 0.0]] * 2, [[1]], [[0]], [0, 0, 0], [0, 0, 1])
    assert abs(tq - 0.044) < 1e-12
