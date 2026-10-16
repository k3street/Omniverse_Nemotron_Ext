"""The task critic's grip judgment (task_critic.grip_measures, grip_findings):
what the hand did to the object, from the run's own record, checked against
the class's grip features."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from task_critic import grip_findings, grip_measures, measured_cap, upsert_task  # noqa: E402


def _row(stage, thumb_n=0.0, flex=0.0, index_cmd=0.8, index_at=0.8, halted=0, body_p=(0.0, 0.0, 0.0), on_trigger=None):
    r = {"stage": stage, "thumb_flex": flex,
         "contact_n": {"index": 0.0, "middle": 5.0, "ring": 4.0, "pinky": 3.0, "thumb": thumb_n, "palm": 2.0},
         "hand": {"cmd_rad": [index_cmd, 1.74, 1.74, 1.74, flex], "lock_rad": [index_at, 1.2, 1.3, 1.3, flex],
                  "halted": [halted, 1, 1, 1, 0]},
         "palm_world": {"p": [0.0, 0.0, 0.0], "q_wxyz": [1.0, 0.0, 0.0, 0.0]},
         "tool": {"body": {"p": list(body_p), "q_wxyz": [1.0, 0.0, 0.0, 0.0]}, "on_trigger_n": on_trigger or {"index": 0.0, "middle": 0.0}}}
    return r


FEATURES = {"features": [
    {"id": "fingers_wrap", "text": "fingers hold", "measure": "digit_n", "digits": ["middle", "ring", "pinky"], "stages": ["settle", "run"], "min_n": 1.0},
    {"id": "thumb_opposes", "text": "thumb round", "measure": "thumb_opposes", "stages": ["settle", "run"], "min_flex_rad": 0.4, "min_n": 1.0},
    {"id": "index_alone", "text": "index on trigger", "measure": "control_digits", "stages": ["run"], "only": ["index"]},
    {"id": "full_travel", "text": "whole travel", "measure": "trigger_fraction", "stages": ["run"], "min": 0.9},
    {"id": "still", "text": "still in hand", "measure": "in_hand_motion", "stages": ["run"], "max_turn_deg": 5.0, "max_slide_mm": 5.0},
    {"id": "cut", "text": "unmeasurable here", "measure": "loop_digits", "stages": ["run"]},
]}


def test_a_flat_thumb_propping_the_tool_fails_and_says_so():
    log = [_row("settle", thumb_n=6.0), _row("settle", thumb_n=7.0),
           _row("run", thumb_n=8.0, index_cmd=1.6, index_at=1.04, halted=1, on_trigger={"index": 10.0, "middle": 0.0}),
           _row("run", thumb_n=8.0, index_cmd=1.6, index_at=1.04, halted=1, body_p=(0.0, 0.0, 0.0012), on_trigger={"index": 9.0, "middle": 0.0})]
    gm = grip_measures(log, {"tool": {"trigger_peak_fraction": 0.5}})
    f = {x["id"]: x for x in grip_findings(FEATURES, gm, {})}
    assert f["fingers_wrap"]["pass"] is True
    assert f["thumb_opposes"]["pass"] is False and "flat thumb" in f["thumb_opposes"]["evidence"]
    assert f["index_alone"]["pass"] is True
    assert f["full_travel"]["pass"] is False and "halted" in f["full_travel"]["evidence"]
    assert f["still"]["pass"] is True and "1.2 mm" in f["still"]["evidence"]
    assert f["cut"]["pass"] is None and f["cut"]["evidence"].startswith("unmeasured")


def test_a_wrapped_thumb_a_full_pull_and_a_second_finger_on_the_trigger():
    log = [_row("settle", thumb_n=4.0, flex=0.8), _row("run", thumb_n=5.0, flex=0.8, on_trigger={"index": 12.0, "middle": 3.0}),
           _row("run", thumb_n=5.0, flex=0.8, body_p=(0.0, 0.009, 0.0), on_trigger={"index": 12.0, "middle": 3.0})]
    gm = grip_measures(log, {"tool": {"trigger_peak_fraction": 0.97}})
    f = {x["id"]: x for x in grip_findings(FEATURES, gm, {})}
    assert f["thumb_opposes"]["pass"] is True
    assert f["full_travel"]["pass"] is True
    assert f["index_alone"]["pass"] is False                      # the middle pressed the trigger too
    assert f["still"]["pass"] is False                            # slid 9 mm in the hand


def test_a_critic_run_again_restates_its_open_tasks_instead_of_stacking_them():
    b = {"tasks": [{"from_scene": "s", "judge": "gemma", "rung": 3, "title": "old", "status": "proposed"},
                   {"from_scene": "s", "judge": "gemma", "rung": 2, "title": "done", "status": "done"}]}
    upsert_task(b, {"from_scene": "s", "judge": "gemma", "rung": 3, "title": "new", "status": "proposed"})
    assert [t["title"] for t in b["tasks"]] == ["new", "done"]
    upsert_task(b, {"from_scene": "s", "judge": "gemma", "rung": 2, "title": "again", "status": "proposed"})   # the done one stays
    upsert_task(b, {"from_scene": "s", "kind": "grip", "judge": "record", "title": "grip", "status": "proposed"})
    assert [t["title"] for t in b["tasks"]] == ["new", "done", "again", "grip"]


def test_scissors_reach_rung_two_only_on_a_passing_loop_verdict():
    spec = {"objects": [{"asset_id": "model_scissors_21", "target": True}], "grasp_replay": {}}
    cap, why = measured_cap("scissors", spec, {"held": True, "loop": {"pass": False}})
    assert cap == 1 and any("loop verdict" in w for w in why)
    cap, why = measured_cap("scissors", spec, {"held": True, "loop": {"pass": True}})
    assert cap == 2                                                       # no sheet to cut: rung 3 is not open
    pushed = dict(spec, grasp_replay={"then": ["finger_push"]})
    assert measured_cap("scissors", pushed, {"held": True, "loop": {"pass": True}})[0] == 1
