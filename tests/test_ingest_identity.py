"""Ingest identity and classification (pxr only, no Kit).

A file name is not an identity, and a class keyword is a whole word or
phrase, not a substring of whatever the file happens to be called.
"""
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0
pxr = pytest.importorskip("pxr")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pxr import Usd, UsdGeom, UsdPhysics  # noqa: E402


def _box_usd(path: Path, size: float):
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    cube = UsdGeom.Cube.Define(stage, "/Asset/Body")
    cube.CreateSizeAttr(size)
    stage.GetRootLayer().Save()
    return path


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    import ingest_asset

    q = tmp_path / "queue"
    q.mkdir()
    monkeypatch.setattr(ingest_asset, "QUEUE_DIR", q)
    return q


def _queued(q, asset_id, src):
    import ingest_asset

    (q / f"{asset_id}.json").write_text(json.dumps({
        "asset_id": asset_id, "file": str(src), "source_sha1": ingest_asset._file_sha1(str(src))}))


def test_same_content_under_another_name_is_a_duplicate(tmp_path, queue):
    from ingest_asset import resolve_identity

    a = _box_usd(tmp_path / "Vilya.usda", 0.02)
    b = tmp_path / "Vilya(1).usda"
    b.write_bytes(a.read_bytes())
    _queued(queue, "vilya", a)
    asset_id, reason = resolve_identity(str(b))
    assert asset_id is None and "same content as vilya" in reason


def test_same_name_different_content_gets_its_own_id(tmp_path, queue):
    from ingest_asset import resolve_identity

    shop_a = tmp_path / "a"
    shop_b = tmp_path / "b"
    shop_a.mkdir()
    shop_b.mkdir()
    first = _box_usd(shop_a / "TV_Remote.usda", 0.18)
    second = _box_usd(shop_b / "Tv_Remote.usda", 0.19)  # same slug, different file
    _queued(queue, "tv_remote", first)
    asset_id, reason = resolve_identity(str(second))
    assert reason is None and asset_id.startswith("tv_remote_") and asset_id != "tv_remote"


def test_the_same_file_again_keeps_its_id(tmp_path, queue):
    from ingest_asset import resolve_identity

    a = _box_usd(tmp_path / "Dog_Bowl.usda", 0.2)
    _queued(queue, "dog_bowl", a)
    assert resolve_identity(str(a)) == ("dog_bowl", None)


@pytest.mark.parametrize("name, expected", [
    ("Apple_Watch_Ultra_2.usda", "wristwatch"),        # not food_item via 'apple'
    ("Dog_Bowl.usda", "pet_bowl"),                     # not plate via 'bowl'
    ("Screw_and_flat_washer.usda", "bolt_fastener"),   # not a washing machine
    ("4wd_Hat_module_for_the_Raspberry_Pi.usda", "circuit_board"),
])
def test_multi_word_keywords_match_whole_words(tmp_path, name, expected):
    from ingest_asset import run_report

    r = run_report(str(_box_usd(tmp_path / name, 0.1)), None)
    assert r["matched_class"] == expected


def test_small_parts_get_tight_contact_offsets(tmp_path):
    from ingest_asset import _tune_small_colliders

    stage = Usd.Stage.Open(str(_box_usd(tmp_path / "ring.usda", 0.02)))
    UsdPhysics.CollisionAPI.Apply(stage.GetPrimAtPath("/Asset/Body"))
    assert _tune_small_colliders(stage, "/Asset") == 1
    offset = stage.GetPrimAtPath("/Asset/Body").GetAttribute("physxCollision:contactOffset").Get()
    assert 0.0005 <= offset <= 0.002


def _stage_with(path: Path, boxes: dict):
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    for name, (lo, hi) in boxes.items():
        m = UsdGeom.Mesh.Define(stage, f"/Asset/{name}")
        m.CreatePointsAttr([(x, y, z) for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])])
        m.CreateFaceVertexCountsAttr([4] * 6)
        m.CreateFaceVertexIndicesAttr([0, 2, 3, 1, 4, 5, 7, 6, 0, 1, 5, 4, 2, 6, 7, 3, 0, 4, 6, 2, 1, 3, 7, 5])
    stage.GetRootLayer().Save()
    return path


def test_the_floor_a_model_was_shown_on_is_a_backdrop(tmp_path):
    from ingest_asset import find_backdrops

    f = _stage_with(tmp_path / "screw.usda", {
        "Screw": ((-0.003, -0.003, 0.0), (0.003, 0.003, 0.009)),
        "Plane": ((-0.0475, -0.0475, 0.0), (0.0475, 0.0475, 0.0))})
    assert find_backdrops(str(f)) == ["/Asset/Plane"]


@pytest.mark.parametrize("boxes", [
    # a door panel 16 mm thick beside its handle: thin, but a solid part
    {"Door": ((0.0, 0.0, 0.0), (0.4, 0.016, 2.0)), "Handle": ((0.37, -0.06, 0.8), (0.4, 0.0, 1.4))},
    # a sheet of paper under a pen: flat, but not far wider than the pen
    {"Paper": ((0.0, 0.0, 0.0), (0.21, 0.297, 0.0)), "Pen": ((0.05, 0.05, 0.0), (0.06, 0.19, 0.01))},
])
def test_flat_parts_of_the_object_are_not_backdrops(tmp_path, boxes):
    from ingest_asset import find_backdrops

    assert find_backdrops(str(_stage_with(tmp_path / "a.usda", boxes))) == []


def test_a_wrong_size_is_corrected_as_a_unit_slip_first(tmp_path):
    from ingest_asset import run_report

    # a 3 cm screw authored in centimetres as metres: x0.01, not the range's middle
    r = run_report(str(_box_usd(tmp_path / "Screw.usda", 3.0)), None)
    assert r["matched_class"] == "bolt_fastener" and r["suggested_scale_correction"] == 0.01


def test_a_file_named_for_one_thing_that_shows_another_is_flagged(tmp_path, queue, monkeypatch):
    import ingest_asset
    import vlm_classify

    thumb = tmp_path / "t.png"
    thumb.write_bytes(b"png")
    (queue / "elevator_key.json").write_text(json.dumps({
        "asset_id": "elevator_key", "file": str(tmp_path / "Elevator_key.usda"), "thumbnail": str(thumb),
        "class_source": "filename_guess", "report": {"matched_class": "key"}}))
    monkeypatch.setattr(vlm_classify, "QUEUE_DIR", queue)
    monkeypatch.setattr(vlm_classify, "classify_thumbnail", lambda p, views=(), hints=None: {
        "object_name": "elevator call-button panel", "asset_class": "elevator_panel", "confidence": "high",
        "visible_moving_parts": ["buttons"], "content_kind": "single_object"})
    monkeypatch.setattr(vlm_classify, "run_report", lambda f, c: {"matched_class": c})
    monkeypatch.setattr(vlm_classify, "propose_category", lambda r: "prop")
    for name in ("build_wrapper", "apply_rigid_physics", "refresh_renders"):
        monkeypatch.setattr(ingest_asset, name, lambda *a, **k: None)
    msg = vlm_classify.classify_entry("elevator_key")
    entry = json.loads((queue / "elevator_key.json").read_text())
    assert entry["identity_mismatch"] == {"name_says": "key", "content_is": "elevator_panel",
                                          "object_name": "elevator call-button panel", "confidence": "high"}
    assert "NAME MISMATCH" in msg
    # a second look keeps what the name said, not the class it was changed to
    vlm_classify.classify_entry("elevator_key")
    assert json.loads((queue / "elevator_key.json").read_text())["name_class"] == "key"


def _set_file(tmp_path):
    return _stage_with(tmp_path / "set.usda", {
        "Glass": ((-0.013, -0.013, 0.0), (0.013, 0.013, 0.05)),       # a bottle...
        "Liquid": ((-0.011, -0.011, 0.002), (0.011, 0.011, 0.033)),   # ...with its liquid inside
        "Pipette": ((-0.004, 0.03, 0.06), (0.004, 0.06, 0.105)),       # the dropper beside it...
        "Bulb": ((-0.006, 0.04, 0.09), (0.006, 0.05, 0.11)),           # ...and its bulb
    })


def test_a_set_file_splits_into_its_objects_not_its_meshes(tmp_path):
    from ingest_asset import object_groups

    stage = Usd.Stage.Open(str(_set_file(tmp_path)))
    groups = sorted(sorted(m.GetName() for m in g) for g in object_groups(stage, "/Asset"))
    assert groups == [["Bulb", "Pipette"], ["Glass", "Liquid"]]


def test_each_object_of_a_set_is_its_own_rigid_articulation(tmp_path):
    import types

    from ingest_asset import apply_group_physics

    stage = Usd.Stage.Open(str(_set_file(tmp_path)))
    omni = types.ModuleType("omni")
    omni_usd = types.ModuleType("omni.usd")
    omni_usd.get_context = lambda: type("C", (), {"get_stage": lambda self: stage})()
    omni.usd = omni_usd
    sys.modules["omni"], sys.modules["omni.usd"] = omni, omni_usd
    note = apply_group_physics(stage, "/Asset", {"typical_materials": ["glass"]}, 0.1)
    assert "2 separate objects" in note
    roots = [p.GetName() for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
    assert sorted(roots) == ["Glass", "Pipette"]   # the larger mesh of each object
    joints = {UsdPhysics.Joint(p).GetBody1Rel().GetTargets()[0].name: UsdPhysics.Joint(p).GetBody0Rel().GetTargets()[0].name
              for p in stage.Traverse() if p.IsA(UsdPhysics.FixedJoint)}
    assert joints == {"Liquid": "Glass", "Bulb": "Pipette"}
    # the liquid sits inside the glass: their colliders must not fight the joint
    assert [t.name for t in UsdPhysics.FilteredPairsAPI(stage.GetPrimAtPath("/Asset/Liquid"))
            .GetFilteredPairsRel().GetTargets()] == ["Glass"]
    masses = [stage.GetPrimAtPath(f"/Asset/{n}").GetAttribute("physics:mass").Get()
              for n in ("Glass", "Liquid", "Pipette", "Bulb")]
    assert sum(masses) == pytest.approx(0.1, rel=1e-3)


def test_a_pestle_resting_in_its_mortar_is_a_separate_object(tmp_path):
    from ingest_asset import object_groups

    f = _stage_with(tmp_path / "mortar.usda", {
        "Bowl": ((-0.06, -0.06, 0.0), (0.06, 0.06, 0.07)),
        "Pestle": ((-0.01, -0.01, 0.02), (0.01, 0.06, 0.15)),     # in the bowl, standing out of it
    })
    stage = Usd.Stage.Open(str(f))
    assert sorted(sorted(m.GetName() for m in g) for g in object_groups(stage, "/Asset")) == [["Bowl"], ["Pestle"]]
