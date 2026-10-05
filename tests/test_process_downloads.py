"""scripts/process_downloads.py: the file handling (no pxr needed)."""
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _usdz(path: Path, payload: bytes) -> Path:
    path.write_bytes(b"PK\x03\x04" + payload)   # a usdz is a zip
    return path


def test_survey_goes_by_content_not_by_name(tmp_path):
    from process_downloads import survey

    dl, lib = tmp_path / "dl", tmp_path / "lib"
    (lib / "Medical").mkdir(parents=True)
    dl.mkdir()
    _usdz(lib / "Medical" / "Bed.usdz", b"bed-one")
    _usdz(dl / "Bed.usdz", b"bed-two")                 # same name, different file: new
    _usdz(dl / "Cot.usdz", b"bed-one")                 # another name, same file: in the library
    _usdz(dl / "Cot(1).usdz", b"bed-two")              # a copy of another download
    (dl / "cube.usda").write_text("cube([10, 10, 10]);")   # an OpenSCAD script, not USD
    kinds = {Path(f["file"]).name: f["kind"] for f in survey(dl, lib)}
    assert kinds == {"Bed.usdz": "new", "Cot.usdz": "in_library", "Cot(1).usdz": "copy", "cube.usda": "not_usd"}


def test_a_same_named_different_file_is_never_overwritten(tmp_path):
    from process_downloads import free_name

    (tmp_path / "Bed.usdz").write_bytes(b"PK one")
    assert free_name(tmp_path, "Bed.usdz", "ed2c36aa") == tmp_path / "Bed_ed2c36.usdz"
    assert free_name(tmp_path, "Cot.usdz", "ed2c36aa") == tmp_path / "Cot.usdz"


def test_filing_prefers_a_pin_then_a_scene_then_the_class(tmp_path, monkeypatch):
    import json

    import process_downloads as pd

    m = tmp_path / "folders.json"
    m.write_text(json.dumps({"default": "Misc", "scenes": "Scenes", "by_asset": {"dryer": "Laundry"},
                             "folders": {"Kitchen_Home": ["appliance_large"], "Doors": ["door"]}}))
    monkeypatch.setattr(pd, "FOLDERS", m)
    assert pd.folder_for({"asset_id": "dryer", "class_hint": "appliance_large"}) == "Laundry"
    assert pd.folder_for({"asset_id": "or", "class_hint": "door", "vlm": {"content_kind": "scene_fragment"}}) == "Scenes"
    assert pd.folder_for({"asset_id": "x", "class_hint": "door"}) == "Doors"
    assert pd.folder_for({"asset_id": "y", "class_hint": "unknown"}) == "Misc"
