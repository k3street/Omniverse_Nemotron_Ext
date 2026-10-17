"""The curricula read into backlog tasks (scripts/curriculum_intake.py): a
lesson's requirements in order, the first unmet one actionable, tasks shared
by the lessons they block, closed when met and superseded when no lesson
needs them, the occupation's latest revision winning over its earlier ones."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import curriculum_intake as ci  # noqa: E402


class _Lib:
    def __init__(self, hits=()):
        self.hits = list(hits)

    def find(self, terms):
        return [h for h in self.hits if any(t in h["text"] for t in terms)]


REG = {"interactions": [
    {"id": "rigid_contact", "kind": "contact", "builtin": True, "terms": ["place"], "proven_by": None},
    {"id": "cut_sheet_scissors", "kind": "material", "terms": ["sheet is cut"], "needs": "a sheet that parts"},
]}
BIND = {"planners": {"pinch_place": {"module": "pinch_place_planner"}, "bimanual_carry": {"needs": "two arms"}},
        "measures": {"released": "move.released", "_homehero_owned": ["pause_respected"]}, "lessons": {}, "occupations": {}}


def _occ_lesson(**kw):
    L = {"key": "homehero.occupation.lab.tray", "kind": "homehero_occupation", "rank": 1, "order": 0, "title": "carry the tray",
         "family": "pick_hold_place", "text": "Carry the tray to the bench and place it", "group": "lab",
         "roles": [{"role": "carry_tray", "requirements": {"kind": "movable", "search_terms": ["tray"]}}],
         "hands": 2, "moves_between_stations": True, "into_receptacle": False,
         "criteria": ["released", "pause_respected", "payload_retained"], "criteria_notes": {},
         "scene_request": {"id": "lab_v1"}, "source": {"kind": "homehero_occupation", "title": "Lab"}, "handoff": True}
    L.update(kw)
    return L


def test_requirements_come_in_order_and_homehero_owned_criteria_are_not_ours(monkeypatch):
    monkeypatch.setattr(ci, "_proven", lambda pb: (False, "no take"))
    reqs = ci.requirements(_occ_lesson(), BIND, REG, _Lib())
    steps = [r["step"] for r in reqs]
    assert steps == sorted(steps, key=ci.STEPS.index)
    ids = [r["id"] for r in reqs]
    assert "asset:lab:carry_tray" in ids and "planner:bimanual_carry" in ids and "planner:base_motion" in ids
    assert "measure:payload_retained" in ids and "measure:pause_respected" not in ids
    assert next(r for r in reqs if r["id"] == "measure:released")["met"]
    assert ids[-1] == "handoff:lab"


def test_an_approved_candidate_asks_for_qualification_and_none_asks_for_a_source(monkeypatch):
    monkeypatch.setattr(ci, "_proven", lambda pb: (False, "no take"))
    got = ci.requirements(_occ_lesson(), BIND, REG, _Lib([{"asset_id": "simple_tray", "text": "simple tray", "verified": True}]))[0]
    assert not got["met"] and got["work"].startswith("qualify one of ['simple_tray']")
    none = ci.requirements(_occ_lesson(), BIND, REG, _Lib())[0]
    assert none["work"].startswith("source or generate")


def test_a_lesson_without_a_binding_is_mapped_by_terms_and_says_so(monkeypatch):
    monkeypatch.setattr(ci, "_proven", lambda pb: (False, "no take"))
    L = {"key": "nemotron.ladder.scissors.r3", "kind": "nemotron_use_ladder", "rank": 4, "order": 3, "title": "scissors rung 3",
         "text": "closed across a sheet held by the other hand: the sheet is cut", "asset": "scissors",
         "source": {"kind": "nemotron_use_ladder"}, "no_take": True}
    reqs = ci.requirements(L, BIND, REG, _Lib([{"asset_id": "scissor", "text": "scissors", "verified": True}]))
    cut = next(r for r in reqs if r["id"] == "interaction:cut_sheet_scissors")
    assert not cut["met"] and cut["matched_by_terms"] and cut["work"] == "a sheet that parts"
    assert not any(r["step"] == "take" for r in reqs)                  # a rung's take is the critic's task


def test_tasks_are_upserted_closed_when_met_and_superseded_when_unneeded():
    backlog = {"tasks": [
        {"id": "cur:asset:lab:carry_tray", "status": "in_progress", "title": "old"},
        {"id": "cur:planner:base_motion", "status": "proposed"},
        {"id": "cur:scene:gone", "status": "proposed"},
        {"id": "hh_lab", "kind": "homehero_occupation", "status": "proposed"},
    ]}
    tasks = {"asset:lab:carry_tray": {"id": "cur:asset:lab:carry_tray", "kind": "asset", "title": "asset for the tray", "work": "w",
                                      "evidence": "e", "priority": 10000, "blocks": ["L1", "L1"], "actionable": True,
                                      "matched_by_terms": False, "sources": ["homehero_occupation"]},
             "interaction:cut": {"id": "cur:interaction:cut", "kind": "interaction", "title": "cut", "work": "w", "evidence": "e",
                                 "priority": 40010, "blocks": ["L2"], "actionable": True, "matched_by_terms": True, "sources": ["nemotron_use_ladder"]}}
    counts = ci.update_backlog(backlog, tasks, {"planner:base_motion": "a base path"}, "2026-10-17")
    by = {t["id"]: t for t in backlog["tasks"]}
    assert counts == {"added": 1, "updated": 1, "closed": 1, "superseded": 1}
    assert by["cur:asset:lab:carry_tray"]["status"] == "in_progress" and by["cur:asset:lab:carry_tray"]["title"] == "asset for the tray"
    assert by["cur:asset:lab:carry_tray"]["blocks"] == ["L1"] and by["cur:asset:lab:carry_tray"]["parents"] == ["hh_lab"]
    assert by["cur:planner:base_motion"]["status"] == "done" and "a base path" in by["cur:planner:base_motion"]["closed_why"]
    assert by["cur:scene:gone"]["status"] == "superseded"
    assert by["cur:interaction:cut"]["status"] == "proposed" and "confirm" in by["cur:interaction:cut"]


def test_next_is_the_in_progress_or_most_urgent_actionable_task():
    backlog = {"tasks": [
        {"id": "cur:a", "status": "proposed", "priority": 10, "actionable": False},
        {"id": "cur:b", "status": "proposed", "priority": 20, "actionable": True},
        {"id": "cur:c", "status": "proposed", "priority": 15, "actionable": True},
        {"id": "other", "status": "proposed", "priority": 0, "actionable": True},
    ]}
    assert ci.next_task(backlog)["id"] == "cur:c"
    backlog["tasks"].append({"id": "cur:d", "status": "in_progress", "priority": 90, "actionable": True})
    assert ci.next_task(backlog)["id"] == "cur:d"


def test_an_occupation_s_latest_revision_wins_even_under_a_new_studio_id(tmp_path, monkeypatch):
    import json
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"curriculum_sha256": "old", "occupation_id": "lab_handler", "occupation_title": "Lab handler",
                             "proposal_provenance": {"created_at": "2026-10-17T14:23:26+00:00"}, "lessons": []}))
    monkeypatch.setattr(ci, "REPO", tmp_path)
    monkeypatch.setattr(ci, "_studio_reviewed", lambda url: [{"plan": {"curriculum_sha256": "new", "occupation_id": "occupation_45d4",
                                                                       "occupation_title": "Lab handler", "lessons": []},
                                                              "origin": "studio:d (reviewed)", "at": 1792252700.0}])
    b = {"sources": {"occupation_deliveries": "none", "occupation_handoff_curricula": [str(a)], "studio": "x"}}
    cur, sup = ci.occupation_plans(b)
    assert [c["plan"]["curriculum_sha256"] for c in cur] == ["new"] and cur[0]["occupation"] == "lab_handler"
    assert [c["plan"]["curriculum_sha256"] for c in sup] == ["old"]


def test_next_follows_an_open_blocker():
    backlog = {"tasks": [
        {"id": "cur:hole", "status": "in_progress", "priority": 40410, "actionable": True, "blocked_by": ["grip_thrust"]},
        {"id": "grip_thrust", "status": "proposed"},
        {"id": "cur:asset", "status": "proposed", "priority": 10000, "actionable": True},
    ]}
    assert ci.next_task(backlog)["id"] == "grip_thrust"
    backlog["tasks"][1]["status"] = "done"
    assert ci.next_task(backlog)["id"] == "cur:hole"


def test_a_bound_role_is_met_only_by_a_passing_preflight(monkeypatch):
    monkeypatch.setattr(ci, "_proven", lambda pb: (pb is not None and pb["scene"] == "good", f"{pb and pb['scene']}"))
    b = dict(BIND, occupations={"lab": {"roles": {"carry_tray": {"asset": "simple_tray", "preflight": {"scene": "good", "record": "pass"}}}}})
    assert ci.requirements(_occ_lesson(), b, REG, _Lib())[0]["met"]
    b["occupations"]["lab"]["roles"]["carry_tray"]["preflight"]["scene"] = "bad"
    r = ci.requirements(_occ_lesson(), b, REG, _Lib())[0]
    assert not r["met"] and r["work"].startswith("pass a preflight take of simple_tray")
    b["occupations"]["lab"]["roles"]["carry_tray"] = "simple_tray"
    assert not ci.requirements(_occ_lesson(), b, REG, _Lib())[0]["met"]
