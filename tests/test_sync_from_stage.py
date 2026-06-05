"""Phase 22 — sync_from_stage: scaffold + SPEC/PARSER layer tests."""
import dataclasses

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Legacy scaffold tests (preserved)
# ---------------------------------------------------------------------------


def test_classify_franka_prim():
    from service.isaac_assist_service.multimodal.sync_stage import _classify_prim

    prim = {"reference_url": "/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"}
    assert _classify_prim(prim) == "franka_panda"


def test_classify_cube_by_usd_type():
    from service.isaac_assist_service.multimodal.sync_stage import _classify_prim

    assert _classify_prim({"usd_type": "Cube"}) == "cube"


def test_classify_unknown_returns_none():
    from service.isaac_assist_service.multimodal.sync_stage import _classify_prim

    assert _classify_prim({"usd_type": "Mystery"}) is None


@pytest.mark.asyncio
async def test_sync_from_stage_returns_spec_shape():
    from service.isaac_assist_service.multimodal.sync_stage import sync_from_stage

    spec = await sync_from_stage("test-session")
    assert "intent" in spec
    assert "objects" in spec
    assert "source" in spec


# ---------------------------------------------------------------------------
# Phase 22 SPEC/PARSER layer — new tests
# ---------------------------------------------------------------------------

# -- StagePrimClassifier --


def test_classifier_cube_type_name():
    """Cube type_name should map to LayoutClass 'Cube'."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        PrimRecord,
        StagePrimClassifier,
    )

    clf = StagePrimClassifier()
    record = PrimRecord(path="/World/MyCube", type_name="Cube")
    assert clf.classify(record) == "Cube"


def test_classifier_with_references_returns_reference():
    """Any prim with non-empty references list must classify as 'Reference'."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        PrimRecord,
        StagePrimClassifier,
    )

    clf = StagePrimClassifier()
    record = PrimRecord(
        path="/World/Robot",
        type_name="Xform",
        references=["omniverse://localhost/Isaac/Robots/franka.usd"],
    )
    assert clf.classify(record) == "Reference"


def test_classifier_unknown_type_name():
    """Unknown type_name with no references should fall back to 'unknown'."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        PrimRecord,
        StagePrimClassifier,
    )

    clf = StagePrimClassifier()
    record = PrimRecord(path="/World/Mystery", type_name="SomeFutureType")
    assert clf.classify(record) == "unknown"


def test_classifier_type_name_to_class_has_min_9_entries():
    """TYPE_NAME_TO_CLASS must cover at least 9 USD primitive types."""
    from service.isaac_assist_service.multimodal.sync_stage import StagePrimClassifier

    assert len(StagePrimClassifier.TYPE_NAME_TO_CLASS) >= 9


# -- StageToLayoutSpecParser.parse_record --


def test_parse_record_extracts_position_rotation_scale():
    """parse_record should faithfully copy translate/rotate/scale into LayoutEntry."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        PrimRecord,
        StageToLayoutSpecParser,
    )

    parser = StageToLayoutSpecParser()
    rec = PrimRecord(
        path="/World/Box",
        type_name="Cube",
        translate=(1.0, 2.0, 3.0),
        rotate_xyz_deg=(10.0, 20.0, 30.0),
        scale=(0.5, 0.5, 0.5),
    )
    entry = parser.parse_record(rec)
    assert entry.position == (1.0, 2.0, 3.0)
    assert entry.rotation_deg == (10.0, 20.0, 30.0)
    assert entry.scale == (0.5, 0.5, 0.5)
    assert entry.prim_path == "/World/Box"
    assert entry.class_name == "Cube"


# -- StageToLayoutSpecParser.parse_records --


def test_parse_records_empty_returns_empty_list():
    """parse_records([]) must return []."""
    from service.isaac_assist_service.multimodal.sync_stage import StageToLayoutSpecParser

    parser = StageToLayoutSpecParser()
    assert parser.parse_records([]) == []


def test_parse_records_preserves_order():
    """parse_records must return entries in the same order as input records."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        PrimRecord,
        StageToLayoutSpecParser,
    )

    parser = StageToLayoutSpecParser()
    paths = ["/World/A", "/World/B", "/World/C"]
    records = [PrimRecord(path=p, type_name="Sphere") for p in paths]
    entries = parser.parse_records(records)
    assert [e.prim_path for e in entries] == paths


# -- StageToLayoutSpecParser.parse_records_to_layout_spec --


def test_parse_records_to_layout_spec_returns_dict_with_version_and_entries():
    """Returned dict must have 'version' == 1 and an 'entries' key."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        PrimRecord,
        StageToLayoutSpecParser,
    )

    parser = StageToLayoutSpecParser()
    records = [PrimRecord(path="/World/Cube1", type_name="Cube")]
    spec = parser.parse_records_to_layout_spec(records)
    assert spec["version"] == 1
    assert "entries" in spec
    assert "generated_at" in spec


def test_parse_records_to_layout_spec_entries_count_matches():
    """entries list length must equal the number of input records."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        PrimRecord,
        StageToLayoutSpecParser,
    )

    parser = StageToLayoutSpecParser()
    records = [
        PrimRecord(path="/World/A", type_name="Cube"),
        PrimRecord(path="/World/B", type_name="Sphere"),
        PrimRecord(path="/World/C", type_name="Camera"),
    ]
    spec = parser.parse_records_to_layout_spec(records)
    assert len(spec["entries"]) == 3


# -- StageToLayoutSpecParser.filter_known --


def test_filter_known_drops_unknown_prims():
    """filter_known must exclude records with type_name not in TYPE_NAME_TO_CLASS."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        PrimRecord,
        StageToLayoutSpecParser,
    )

    parser = StageToLayoutSpecParser()
    records = [
        PrimRecord(path="/World/Known", type_name="Cube"),
        PrimRecord(path="/World/Unknown", type_name="FuturePrimType"),
    ]
    filtered = parser.filter_known(records)
    assert len(filtered) == 1
    assert filtered[0].path == "/World/Known"


# -- synthetic_stage_records_demo --


def test_synthetic_stage_records_demo_has_min_5_records():
    """Demo helper must return at least 5 PrimRecords."""
    from service.isaac_assist_service.multimodal.sync_stage import (
        synthetic_stage_records_demo,
    )

    records = synthetic_stage_records_demo()
    assert len(records) >= 5


# -- LayoutEntry dataclass roundtrip --


def test_layout_entry_dataclass_roundtrip():
    """LayoutEntry should round-trip through dataclasses.asdict and back."""
    from service.isaac_assist_service.multimodal.sync_stage import LayoutEntry

    entry = LayoutEntry(
        class_name="Sphere",
        prim_path="/World/Ball",
        position=(0.0, 1.0, 2.0),
        rotation_deg=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        source_attrs={"color": "red"},
        references=[],
    )
    d = dataclasses.asdict(entry)
    assert d["class_name"] == "Sphere"
    assert d["prim_path"] == "/World/Ball"
    assert d["position"] == (0.0, 1.0, 2.0)
    assert d["source_attrs"] == {"color": "red"}
