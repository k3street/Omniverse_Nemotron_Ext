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


def test_a_digit_in_each_loop_needs_the_digits_on_different_halves():
    feats = {"features": [{"id": "loops", "text": "a digit in each loop", "measure": "digits_in_loops", "stages": ["open"],
                           "pairs": {"thumb": "thumb_loop", "index": "finger_loop"}, "min_n": 0.3}]}
    spec = {"grasp_plan": {"loop_grip": {"thumb_loop": "A", "finger_loop": "B"}}}
    def row(by_body):
        return {"stage": "open", "contact_n": {"thumb": 2.0, "index": 2.0}, "contact_n_by_body": by_body}
    pinch = [row({"A": {"thumb": 2.0, "index": 2.0}, "B": {"thumb": 0.0, "index": 0.0}})]          # both on one half
    f = grip_findings(feats, grip_measures(pinch, {}), spec)[0]
    assert f["pass"] is False and "index on B 0.0 N" in f["evidence"]
    loops = [row({"A": {"thumb": 1.5, "index": 0.0}, "B": {"thumb": 0.0, "index": 0.8}})]
    assert grip_findings(feats, grip_measures(loops, {}), spec)[0]["pass"] is True
    # no per-body record (an older log), or no loop grip named: unmeasured, not a pass
    assert grip_findings(feats, grip_measures([{"stage": "open", "contact_n": {"thumb": 2.0}}], {}), spec)[0]["pass"] is None
    assert grip_findings(feats, grip_measures(loops, {}), {"grasp_plan": {}})[0]["pass"] is None


def test_a_refrigerator_door_is_credited_by_how_far_it_opened():
    spec = {"objects": [{"asset_id": "model_fridge_1", "class": "refrigerator", "target_member": "E_door_1"}], "grasp_replay": {}}
    assert measured_cap("refrigerator", spec, {"held": True, "door": {"seal_broken": False, "max_deg": 0.5}})[0] == 0
    cap, why = measured_cap("refrigerator", spec, {"held": False, "door": {"seal_broken": True, "max_deg": 32.0, "pass": True}})
    assert cap == 1 and any("32.0 deg" in w for w in why)
    assert measured_cap("refrigerator", spec, {"held": True, "door": {"seal_broken": True, "max_deg": 75.0}})[0] == 2


def test_a_microwave_is_credited_by_its_button_and_its_door():
    spec = {"objects": [{"asset_id": "model_microwave_1", "class": "microwave", "target_member": "E_door_1"}], "grasp_replay": {}}
    assert measured_cap("microwave", spec, {"held": False, "door": {"tripped": False}})[0] == 0
    assert measured_cap("microwave", spec, {"held": False, "door": {"tripped": True, "popped_deg": 9.0, "max_deg": 9.5}})[0] == 1
    assert measured_cap("microwave", spec, {"held": False, "door": {"tripped": True, "popped_deg": 9.0, "max_deg": 85.0,
                                                                     "opened_by_hand": True}})[0] == 2
    # 90 deg on a door that crept there by itself is not the hand's swing
    assert measured_cap("microwave", spec, {"held": False, "door": {"tripped": True, "popped_deg": 13.5, "max_deg": 90.0,
                                                                     "opened_by_hand": False}})[0] == 1
    assert measured_cap("microwave", spec, {"held": False, "door": {"tripped": True, "popped_deg": 19.8, "max_deg": 66.2,
                                                                     "opened_by_hand": True}})[0] == 1


def test_the_hand_left_off_the_door_while_it_pressed():
    feats = {"features": [{"id": "off", "text": "off the door", "measure": "no_contact", "stages": ["press", "pressed"], "max_n": 0.5}]}
    log = [{"stage": "press", "contact_n": {"index": 0.0, "middle": 0.1, "palm": 0.0}}, {"stage": "pressed", "contact_n": {"index": 0.2, "palm": 0.0}}]
    assert grip_findings(feats, grip_measures(log, {}), {})[0]["pass"] is True
    log.append({"stage": "pressed", "contact_n": {"index": 0.0, "thumb": 3.0, "palm": 0.0}})
    assert grip_findings(feats, grip_measures(log, {}), {})[0]["pass"] is False


def test_the_press_is_read_on_the_button_not_the_door():
    from task_critic import grip_findings, grip_measures
    log = [{"stage": "pressed", "contact_n": {"index": 0.0, "middle": 0.0, "ring": 0.0, "pinky": 0.0, "thumb": 0.0, "palm": 0.0},
            "contact_n_by_body": {"E_switch_11": {"index": 8.2, "middle": 0.0, "ring": 0.0, "pinky": 0.0, "thumb": 0.0, "palm": 0.0},
                                  "E_body_12": {"index": 0.0, "middle": 0.0, "ring": 0.0, "pinky": 0.0, "thumb": 0.0, "palm": 0.0}}}] * 5
    gm = grip_measures(log, {"door": {"button_body": "E_switch_11"}})
    feat = {"id": "index_presses_the_button", "text": "the index presses the button", "measure": "digit_on_body", "digit": "index", "body_role": "button",
            "stages": ["pressed"], "min_n": 5.0}
    f = grip_findings({"features": [feat]}, gm, {})[0]
    assert f["pass"] is True and "8.2 N" in f["evidence"]
    gm["digit_n_by_body"]["pressed"]["E_switch_11"]["index"] = 0.0
    assert grip_findings({"features": [feat]}, gm, {})[0]["pass"] is False


def test_a_drill_is_credited_for_a_screw_driven_home():
    drill = {"asset_id": "drill_1", "class": "power_drill", "attachments": [{"name": "Bit"}]}
    spec = {"objects": [drill], "screw_drive": {"fixture": "Screw"}, "grasp_replay": {}}
    home = {"held": True, "screw": {"pass": True, "turns": 15.8, "drove_mm": 26.8, "needed_mm": 26.75, "held": True}}
    assert measured_cap("power_drill", spec, home)[0] is None                 # the record caps nothing: the judges place it
    short = {"held": True, "screw": {"pass": False, "turns": 6.0, "drove_mm": 10.1, "needed_mm": 26.75, "held": True}}
    cap, why = measured_cap("power_drill", spec, short)
    assert cap == 3 and any("10.1 of 26.75" in w for w in why)
    still = {"held": True, "screw": {"pass": False, "turns": 0.0, "drove_mm": 0.0, "needed_mm": 26.75, "held": True}}
    assert measured_cap("power_drill", spec, still)[0] == 1
    assert measured_cap("power_drill", {"objects": [{"asset_id": "drill_1"}], "grasp_replay": {}}, {"held": True, "tool": {"pass": True}})[0] == 3


def test_the_trigger_is_read_over_the_features_own_stages():
    from task_critic import grip_findings, grip_measures
    log = [{"stage": "pull", "tool": {"throttle": 0.0}}] * 3 + [{"stage": "drive", "tool": {"throttle": 0.33}}] * 5
    gm = grip_measures(log, {"tool": {"trigger_peak_fraction": 0.0}})
    feat = {"id": "full_trigger_travel", "text": "the trigger pulled", "measure": "trigger_fraction", "stages": ["pull", "run", "drive"], "min": 0.9}
    f = grip_findings({"features": [feat]}, gm, {})[0]
    assert f["pass"] is False and "33%" in f["evidence"]                 # the drive's third, not the summary's 0 %


def test_the_index_kept_off_the_panel_round_the_button():
    from task_critic import grip_findings, grip_measures
    row = lambda n: {"stage": "pressed", "contact_n_by_body": {"E_body_12": {"index": n}, "E_switch_11": {"index": 8.7}}}
    feat = {"id": "index_off_the_panel", "text": "off the panel", "measure": "digit_on_body", "digit": "index", "body_role": "cabinet",
            "stages": ["pressed"], "max_n": 2.0}
    jammed = grip_measures([row(15.1)] * 4, {"door": {"button_body": "E_switch_11", "cabinet_body": "E_body_12"}})
    clean = grip_measures([row(0.3)] * 4, {"door": {"button_body": "E_switch_11", "cabinet_body": "E_body_12"}})
    assert grip_findings({"features": [feat]}, jammed, {})[0]["pass"] is False
    assert grip_findings({"features": [feat]}, clean, {})[0]["pass"] is True


def test_a_switch_the_hand_set_lifts_the_drills_cap():
    drill = {"asset_id": "drill_1", "class": "power_drill", "attachments": [{"name": "Bit"}], "joint_state": {"direction": 0.0}}
    spec = {"objects": [drill], "screw_drive": {"fixture": "Screw"}, "grasp_replay": {}}
    screw = {"pass": True, "turns": 15.8, "drove_mm": 26.75, "needed_mm": 26.75, "held": True}
    authored = {"held": True, "screw": screw, "tool": {"switch_set_by_hand": False}}
    by_hand = {"held": True, "screw": screw, "tool": {"switch_set_by_hand": True}}
    assert measured_cap("power_drill", spec, authored)[0] == 2
    assert measured_cap("power_drill", spec, by_hand)[0] is None


def test_rung_two_is_the_chuck_run_to_the_law_not_the_triggers_reach():
    spec = {"objects": [{"asset_id": "drill_1", "class": "power_drill", "joint_state": {"direction": 0.003}}], "grasp_replay": {}}
    short_pull = {"held": True, "tool": {"pass": False, "ran_per_law": True, "pull_reach_ok": False}}
    no_run = {"held": True, "tool": {"pass": False, "ran_per_law": False, "pull_reach_ok": True}}
    assert measured_cap("power_drill", spec, short_pull)[0] == 2        # capped by the authored switch, not by the 35 % pull
    assert measured_cap("power_drill", spec, no_run)[0] == 1


def test_a_stage_name_reused_before_the_grasp_is_measured_after_it():
    from task_critic import _stage_rows, grip_measures
    pw = {"p": [0.0, 0.0, 0.0], "q_wxyz": [1.0, 0.0, 0.0, 0.0]}

    def row(stage, x):
        return {"stage": stage, "palm_world": pw, "tool": {"body": {"p": [x, 0.0, 0.0], "q_wxyz": [1.0, 0.0, 0.0, 0.0]}}}
    # a press (its 'release' with the hand far from the drill), then the grasp and its own 'release'
    log = [row("press", 0.3), row("release", 0.3), row("hold", 0.25), row("reach", 0.2), row("close", 0.01), row("lift", 0.01),
           row("release", 0.012), row("hold", 0.012)]
    assert [r["tool"]["body"]["p"][0] for r in _stage_rows(log, "release")] == [0.012]
    assert [r["tool"]["body"]["p"][0] for r in _stage_rows(log, "press")] == [0.3]        # only before the grasp: all of it
    gm = grip_measures(log, {})
    assert gm["in_hand"]["release"]["slide_mm"] < 1.0


def test_a_drill_take_with_a_hole_short_of_its_depth_stops_at_rung_three():
    spec = {"objects": [{"asset_id": "drill_1", "attachments": [{"name": "Bit"}]}], "screw_drive": {"head": [0, 0, 0]}}
    res = {"held": True, "tool": {"ran_per_law": True}, "hole": {"pass": False, "depth_mm": 0.0, "needed_mm": 18.0, "diameter_mm": 3.175, "bit_mm": 3.175,
                                                  "thrust_peak_n": 10.4, "turns": 5.5, "held": True}}
    cap, why = measured_cap("power_drill", spec, res)
    assert cap == 3 and any("the hole went 0.0 of 18.0 mm" in w for w in why)
