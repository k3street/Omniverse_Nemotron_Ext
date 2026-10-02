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
