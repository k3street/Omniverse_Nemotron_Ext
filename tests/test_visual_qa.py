"""Autonomous visual approval (BACKLOG #0): rubric, schema governance,
decal-collision skip, and the vial prior that motivated them."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

SCHEMA = json.loads(
    (REPO / "workspace" / "knowledge" /
     "sim_ready_asset_registry.schema.json").read_text())
PRIORS = json.loads(
    (REPO / "workspace" / "knowledge" /
     "asset_class_priors.json").read_text())["classes"]


def _entry(category="rigid_unverified", mass=0.029, callouts=None,
           cls="vial"):
    return {
        "asset_id": "t", "proposed_category": category,
        "report": {
            "matched_class": cls,
            "callouts": callouts or [],
            "structure": {"rigid_bodies": 1, "collision_prims": 2,
                          "material_bindings": 1,
                          "authored_masses": [{"path": "/World/T",
                                               "mass_kg": mass}]},
        },
    }


def _judge(judge="gemma", cls="vial", name="vial", ok=True, conf=0.9):
    return {"judge": judge, "model": judge, "asset_class": cls,
            "object_name": name, "confidence": conf, "integrity_ok": ok,
            "integrity_notes": ""}


class TestRubric:
    def test_clean_rigid_passes_every_check(self):
        from visual_qa import rubric
        checks = rubric(_entry(), [_judge("gemma"), _judge("cosmos")])
        assert all(c["ok"] for c in checks), [
            c for c in checks if not c["ok"]]

    def test_sibling_class_with_right_name_counts_as_agreement(self):
        from visual_qa import rubric
        judges = [_judge("gemma"),
                  _judge("cosmos", cls="medical_furniture", name="vial")]
        checks = {c["check"]: c for c in rubric(_entry(), judges)}
        assert checks["identity_agreement"]["ok"]

    def test_wrong_class_and_name_fails_identity(self):
        from visual_qa import rubric
        judges = [_judge("gemma"),
                  _judge("cosmos", cls="table", name="dining table")]
        checks = {c["check"]: c for c in rubric(_entry(), judges)}
        assert not checks["identity_agreement"]["ok"]

    def test_integrity_veto(self):
        from visual_qa import rubric
        judges = [_judge("gemma"), _judge("cosmos", ok=False)]
        checks = {c["check"]: c for c in rubric(_entry(), judges)}
        assert not checks["integrity"]["ok"]

    def test_single_judge_is_not_enough(self):
        from visual_qa import rubric
        judges = [_judge("gemma"), {"judge": "cosmos", "error": "down"}]
        checks = {c["check"]: c for c in rubric(_entry(), judges)}
        assert not checks["judges_healthy"]["ok"]
        assert not checks["identity_agreement"]["ok"]

    def test_rigid_only_baked_is_out_of_machine_scope(self):
        from visual_qa import rubric
        checks = {c["check"]: c for c in rubric(
            _entry(category="rigid_only_baked"),
            [_judge("gemma"), _judge("cosmos")])}
        assert not checks["rigid_scope"]["ok"]

    def test_articulated_is_out_of_machine_scope(self):
        from visual_qa import rubric
        checks = {c["check"]: c for c in rubric(
            _entry(category="articulated_unverified"),
            [_judge("gemma"), _judge("cosmos")])}
        assert not checks["rigid_scope"]["ok"]

    def test_mass_outside_class_prior_fails_physics(self):
        from visual_qa import rubric
        checks = {c["check"]: c for c in rubric(
            _entry(mass=1.158),  # the HomeHero vial complaint
            [_judge("gemma"), _judge("cosmos")])}
        assert not checks["physics_ready"]["ok"]

    def test_implied_density_guard(self):
        # in-range mass can still be absurd for the measured size (class
        # midpoints gave a 1.26 kg mouse) — implied density catches it
        from visual_qa import rubric
        e = _entry(mass=0.05)  # top of vial range
        e["report"]["dimensions_m"] = [0.01, 0.01, 0.02]  # tiny: 25000 kg/m3
        checks = {c["check"]: c for c in rubric(
            e, [_judge("gemma"), _judge("cosmos")])}
        assert not checks["physics_ready"]["ok"]
        assert "implied density" in checks["physics_ready"]["evidence"]

    def test_plausible_density_passes(self):
        from visual_qa import rubric
        e = _entry(mass=0.029)
        e["report"]["dimensions_m"] = [0.033, 0.033, 0.057]  # ~470 kg/m3
        checks = {c["check"]: c for c in rubric(
            e, [_judge("gemma"), _judge("cosmos")])}
        assert checks["physics_ready"]["ok"]

    def test_error_callout_blocks(self):
        from visual_qa import rubric
        checks = {c["check"]: c for c in rubric(
            _entry(callouts=[{"severity": "error", "check": "fidelity",
                              "message": "x"}]),
            [_judge("gemma"), _judge("cosmos")])}
        assert not checks["no_error_callouts"]["ok"]


class TestSchemaGovernance:
    BASE = {
        "asset_id": "t", "file": "f.usda", "source_file": "s.usdz",
        "category": "rigid_unverified",
        "audit": {"ready": False, "simulable": True},
    }

    def _validate(self, review, category="rigid_unverified"):
        import jsonschema
        asset = {**self.BASE, "category": category, "review": review}
        jsonschema.validate({"version": 1, "assets": [asset]}, SCHEMA)

    def test_machine_review_on_rigid_validates(self):
        self._validate({"approved": True, "reviewer": "visual-qa-v1",
                        "reviewer_type": "machine", "date": "2026-08-07",
                        "models": ["gemma4", "nvidia/Cosmos-Reason2-2B"]})

    def test_machine_review_on_articulated_rejected(self):
        import jsonschema
        with pytest.raises(jsonschema.ValidationError):
            self._validate(
                {"approved": True, "reviewer": "visual-qa-v1",
                 "reviewer_type": "machine", "date": "2026-08-07",
                 "models": ["a", "b"]},
                category="articulated_unverified")

    def test_machine_review_requires_models(self):
        import jsonschema
        with pytest.raises(jsonschema.ValidationError):
            self._validate({"approved": True, "reviewer": "visual-qa-v1",
                            "reviewer_type": "machine",
                            "date": "2026-08-07"})

    def test_human_review_unchanged(self):
        self._validate({"approved": True, "reviewer": "kimate",
                        "date": "2026-08-07"})


class TestDecalSkip:
    def test_make_sim_ready_skips_label_decal_sticker(self):
        from service.isaac_assist_service.chat.tools.handlers.physics import (
            _gen_make_sim_ready,
        )
        code = _gen_make_sim_ready({"prim_path": "/World/X"})
        for token in ("label", "decal", "sticker"):
            assert token in code

    def test_user_patterns_still_merge(self):
        from service.isaac_assist_service.chat.tools.handlers.physics import (
            _gen_make_sim_ready,
        )
        code = _gen_make_sim_ready({"prim_path": "/World/X",
                                    "skip_name_patterns": ["glasslid"]})
        assert "glasslid" in code and "label" in code


class TestVialPrior:
    def test_vial_class_exists_with_plausible_range(self):
        vial = PRIORS["vial"]
        assert vial["mass_kg"][1] <= 0.06
        assert vial["max_dim_m"][1] <= 0.1
        assert not vial["articulable"]

    def test_vial_keyword_moved_out_of_glass(self):
        assert "vial" not in PRIORS["glass"]["keywords"]
        assert "vial" in PRIORS["vial"]["keywords"]


class TestJudgeSchema:
    def test_class_enum_constrains_to_priors(self):
        from visual_qa import _judge_schema
        schema = _judge_schema()
        enum = schema["properties"]["asset_class"]["anyOf"][0]["enum"]
        assert set(enum) == set(PRIORS)
        assert schema["additionalProperties"] is False


class TestDeformablePath:
    def test_soft_classes_have_valid_types(self):
        valid = {"cloth", "sponge", "rubber", "gel", "rope"}
        soft = {k: v for k, v in PRIORS.items() if v.get("deformable")}
        assert len(soft) >= 9
        for k, v in soft.items():
            assert v["deformable"] in valid, k
            assert not v["articulable"], k

    def test_propose_category_routes_deformable(self):
        from ingest_asset import propose_category
        assert propose_category(
            {"matched_class": "towel", "callouts": [],
             "structure": {}}) == "deformable_unverified"
        assert propose_category(
            {"matched_class": "mug", "callouts": [],
             "structure": {}}) == "rigid_unverified"

    def test_schema_deformable_requires_type(self):
        import jsonschema
        base = {"asset_id": "t", "file": "f.usda", "source_file": "s.usdz",
                "category": "deformable_unverified",
                "audit": {"ready": False, "simulable": True},
                "review": {"approved": False, "reviewer": "x",
                           "date": "2026-08-07"}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"version": 1, "assets": [base]}, SCHEMA)
        jsonschema.validate(
            {"version": 1, "assets": [{**base, "deformable": "cloth"}]},
            SCHEMA)

    def test_machine_cannot_sign_deformable(self):
        import jsonschema
        asset = {"asset_id": "t", "file": "f.usda", "source_file": "s.usdz",
                 "category": "deformable_unverified", "deformable": "cloth",
                 "audit": {"ready": False, "simulable": True},
                 "review": {"approved": True, "reviewer": "visual-qa-v1",
                            "reviewer_type": "machine", "date": "2026-08-07",
                            "models": ["a", "b"]}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"version": 1, "assets": [asset]}, SCHEMA)

    def test_blueprint_softens_registry_deformables(self):
        from unittest.mock import patch

        from service.isaac_assist_service.chat.tools.handlers import (
            scene_blueprints as sb,
        )
        reg = {"assets": [{"asset_id": "towel_x", "file": "/tmp/t.usda",
                           "deformable": "cloth"}]}
        with patch.object(sb, "_load_sim_ready_registry", return_value=reg):
            code = sb._gen_build_scene_from_blueprint({"blueprint": {
                "objects": [{"name": "towel", "sim_ready_asset": "towel_x"}]}})
        compile(code, "<bp>", "exec")
        assert "_soften(_place(" in code
        assert "PhysxDeformableSurfaceAPI" in code  # cloth preset

    def test_newton_world_mesh_welds_and_dedups(self):
        # scan meshes: duplicated vertices + double-sided faces must weld
        # to a clean manifold or the cloth solver diverges
        pytest.importorskip("pxr")
        import tempfile

        from pxr import Usd, UsdGeom
        with tempfile.TemporaryDirectory() as td:
            f = str(Path(td) / "quad.usda")
            stage = Usd.Stage.CreateNew(f)
            UsdGeom.SetStageMetersPerUnit(stage, 1.0)
            mesh = UsdGeom.Mesh.Define(stage, "/quad")
            # two triangles sharing an edge, authored with DUPLICATED
            # vertices and a reversed-winding copy of the first face
            mesh.GetPointsAttr().Set(
                [(0, 0, 0), (1, 0, 0), (0, 1, 0),
                 (0, 0, 0), (1, 0, 0), (0, 1, 0),  # duplicates
                 (1, 1, 0)])
            mesh.GetFaceVertexCountsAttr().Set([3, 3, 3])
            mesh.GetFaceVertexIndicesAttr().Set(
                [0, 1, 2, 5, 4, 3, 4, 6, 5])  # face2 = face1 reversed
            stage.GetRootLayer().Save()
            sys.path.insert(0, str(REPO / "scripts"))
            from verify_asset_newton import _world_mesh
            pts, tris = _world_mesh(f)
            assert len(pts) == 4          # welded
            assert len(tris) // 3 == 2    # reversed duplicate dropped

    def test_newton_script_syntax(self):
        import ast
        for name in ("newton_runtime.py", "verify_asset_newton.py",
                     "pick_place_cloth.py", "vbd_cable_probe.py"):
            ast.parse((REPO / "scripts" / name).read_text())

    def test_newton_15_api_contract(self):
        sources = "\n".join(
            (REPO / "scripts" / name).read_text()
            for name in ("verify_asset_newton.py", "pick_place_cloth.py",
                         "vbd_cable_probe.py")
        )
        for removed in ("CollisionPipelineUnified", "model.collide(",
                        "pipeline.collide(model"):
            assert removed not in sources
        assert "pipeline.contacts()" in (
            REPO / "scripts" / "newton_runtime.py").read_text()
        assert "from newton.utils import transform_twist" not in sources
        assert "body_com" in (
            REPO / "scripts" / "pick_place_cloth.py").read_text()
        assert sources.count('body_frame_origin="start"') == 3
        assert "load_visual_shapes=False" in sources
        assert "subprocess.run(" in sources

    def test_newton_runtime_is_pinned_separately_from_isaac(self):
        req = (REPO / "requirements-newton.txt").read_text()
        assert "newton[importers]==1.5.0" in req
        assert "warp-lang==1.16.0" in req
        assert "newton" not in (REPO / "requirements.txt").read_text().lower()

    def test_newton_evidence_records_runtime_versions(self):
        src = (REPO / "scripts" / "verify_asset_newton.py").read_text()
        assert "runtime_metadata()" in src
        helper = (REPO / "scripts" / "newton_runtime.py").read_text()
        assert '"newton_version"' in helper
        assert '"warp_version"' in helper
        assert "newton.use_coord_layout_targets = True" in helper
        # New measurements must not overwrite the historical unversioned
        # Newton 0.2 fields in ignored queue/registry runtime state.
        assert '["newton_1_5"] = evidence' in src
        assert 'cloth_grasp_test_newton_1_5_{suffix}' in src
        assert '"cross_engine_ok": rests' in src
        assert 'asset["verification"]["newton_1_5"] = evidence' in src

    def test_rigid_library_assets_not_softened(self):
        from unittest.mock import patch

        from service.isaac_assist_service.chat.tools.handlers import (
            scene_blueprints as sb,
        )
        reg = {"assets": [{"asset_id": "mug_x", "file": "/tmp/m.usda"}]}
        with patch.object(sb, "_load_sim_ready_registry", return_value=reg):
            code = sb._gen_build_scene_from_blueprint({"blueprint": {
                "objects": [{"name": "mug", "sim_ready_asset": "mug_x"}]}})
        compile(code, "<bp>", "exec")
        assert "_soften(_place(" not in code


class TestUnknownClassification:
    def test_semantic_match_beats_wrong_class_key(self):
        # the vial case: judges name the object right but pick a sibling
        # key — matches_expected is the identity, keys are lookup indexes
        from visual_qa import rubric
        judges = [
            {**_judge("gemma", cls="bottle", name="bottle"),
             "matches_expected": True},
            {**_judge("cosmos", cls="medical_furniture", name="container"),
             "matches_expected": True},
        ]
        checks = {c["check"]: c for c in rubric(_entry(), judges)}
        assert checks["identity_agreement"]["ok"]

    def test_semantic_mismatch_fails(self):
        from visual_qa import rubric
        judges = [
            {**_judge("gemma", cls="table", name="dining table"),
             "matches_expected": False},
            {**_judge("cosmos", cls="table", name="table"),
             "matches_expected": False},
        ]
        checks = {c["check"]: c for c in rubric(_entry(), judges)}
        assert not checks["identity_agreement"]["ok"]

    def test_provisional_class_fails_closed(self, tmp_path, monkeypatch):
        import visual_qa
        priors = {"classes": {
            "watering_can": {"keywords": ["watering"], "max_dim_m": [0.2, 0.5],
                             "mass_kg": [0.2, 1.5], "articulable": False,
                             "typical_materials": ["plastic_abs"],
                             "source": "vlm"}}}
        pp = tmp_path / "priors.json"
        pp.write_text(json.dumps(priors))
        monkeypatch.setattr(visual_qa, "PRIORS_PATH", pp)
        judges = [{**_judge("gemma", cls="watering_can", name="watering can"),
                   "matches_expected": True},
                  {**_judge("cosmos", cls="watering_can", name="watering can"),
                   "matches_expected": True}]
        checks = {c["check"]: c for c in visual_qa.rubric(
            _entry(cls="watering_can"), judges)}
        assert not checks["prior_confirmed"]["ok"]
        assert "PROVISIONAL" in checks["prior_confirmed"]["evidence"]

    def test_confirmed_class_passes_gate(self):
        from visual_qa import rubric
        checks = {c["check"]: c for c in rubric(
            _entry(), [_judge("gemma"), _judge("cosmos")])}
        assert checks["prior_confirmed"]["ok"]

    def test_register_provisional_class(self, tmp_path, monkeypatch):
        import vlm_classify
        pp = tmp_path / "priors.json"
        pp.write_text(json.dumps({"classes": {}}))
        monkeypatch.setattr(vlm_classify, "PRIORS_PATH", pp)
        result = {"proposed_class_key": "Watering-Can",
                  "object_name": "green watering can",
                  "est_max_dim_m": [0.25, 0.45], "est_mass_kg": [0.2, 1.2],
                  "articulable": False, "primary_material": "plastic_abs",
                  "deformable_type": None}
        key = vlm_classify.register_provisional_class(result, "asset_x")
        assert key == "watering_can"
        cls = json.loads(pp.read_text())["classes"]["watering_can"]
        assert cls["source"] == "vlm"
        assert cls["proposed_by"] == "asset_x"
        assert "watering" in cls["keywords"]
        assert cls["typical_materials"] == ["plastic_abs"]
        # second registration reuses, never duplicates
        assert vlm_classify.register_provisional_class(result, "y") == "watering_can"


class TestRiggedCharacters:
    def test_skeleton_routes_to_character_category(self):
        from ingest_asset import propose_category
        assert propose_category(
            {"matched_class": "human_character", "callouts": [],
             "structure": {},
             "skeleton": {"skeletons": 1, "joints": 78, "animations": 1},
             }) == "character_rigged"

    def test_schema_character_requires_skeleton(self):
        import jsonschema
        base = {"asset_id": "p", "file": "f.usd", "source_file": "s.usd",
                "category": "character_rigged",
                "audit": {"ready": False, "simulable": True},
                "review": {"approved": False, "reviewer": "x",
                           "date": "2026-08-09"}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"version": 1, "assets": [base]}, SCHEMA)
        jsonschema.validate({"version": 1, "assets": [
            {**base, "skeleton": {"joints": 78, "animations": 1}}]}, SCHEMA)

    def test_machine_cannot_sign_characters(self):
        import jsonschema
        asset = {"asset_id": "p", "file": "f.usd", "source_file": "s.usd",
                 "category": "character_rigged",
                 "skeleton": {"joints": 78, "animations": 1},
                 "audit": {"ready": False, "simulable": True},
                 "review": {"approved": True, "reviewer": "visual-qa-v1",
                            "reviewer_type": "machine", "date": "2026-08-09",
                            "models": ["a", "b"]}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"version": 1, "assets": [asset]}, SCHEMA)

    def test_characters_out_of_machine_scope_in_rubric(self):
        from visual_qa import rubric
        checks = {c["check"]: c for c in rubric(
            _entry(category="character_rigged", cls="human_character"),
            [_judge("gemma", cls="human_character", name="person"),
             _judge("cosmos", cls="human_character", name="person")])}
        assert not checks["rigid_scope"]["ok"]


class TestCharacterBlueprint:
    def _gen(self, blueprint):
        from service.isaac_assist_service.chat.tools.handlers import (
            scene_blueprints as sb,
        )
        code = sb._gen_build_scene_from_blueprint({"blueprint": blueprint})
        compile(code, "<bp>", "exec")
        return code

    def test_characters_spawn_with_clip_aliases(self):
        code = self._gen({"characters": [
            {"name": "w", "position": [0, 0, 0], "clip": "walk"},
            {"name": "s", "position": [1, 0, 0.4], "clip": "sit"},
            {"name": "g", "position": [2, 0, 0], "clip": "wave"}]})
        assert "'stand_walk_1'" in code
        assert "'Sit'" in code
        assert "'stand_idle_wave_loop'" in code
        assert "_tl.play()" in code
        assert "GetSessionLayer" in code  # the binding that actually wins

    def test_characters_only_blueprint_is_not_empty(self):
        code = self._gen({"characters": [{"name": "p", "position": [0, 0, 0]}]})
        assert "Empty blueprint" not in code
        assert "_spawn_character('p'" in code

    def test_no_characters_no_timeline(self):
        code = self._gen({"objects": [
            {"name": "cup", "prim_type": "Cylinder", "physics": "manipulable"}]})
        assert "_tl.play()" not in code
        assert "# --- character" not in code  # helper def exists, no calls

    def test_unknown_clip_passes_through_for_runtime_guard(self):
        code = self._gen({"characters": [
            {"name": "x", "position": [0, 0, 0], "clip": "backflip"}]})
        assert "'backflip'" in code  # runtime prints 'unknown clip'


class TestCableGenerator:
    def test_cable_structure(self, tmp_path):
        pytest.importorskip("pxr")
        from pxr import Usd, UsdPhysics

        from make_cable import build_cable
        f = build_cable(tmp_path / "c.usda", length_m=0.5, radius_m=0.005,
                        links=10)
        st = Usd.Stage.Open(str(f))
        bodies = [p for p in st.Traverse()
                  if p.HasAPI(UsdPhysics.RigidBodyAPI)]
        joints = [p for p in st.Traverse()
                  if p.IsA(UsdPhysics.Joint) and "joints/" in str(p.GetPath())]
        roots = [p for p in st.Traverse()
                 if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
        assert len(bodies) == 10
        assert len(joints) == 9
        assert len(roots) == 1  # standalone cable: floating base at link_00
        # D6 joints (SphericalJoint cone limits are IGNORED by omni.physx
        # inside articulations): coincident frames, locked translation,
        # limited+driven rotation
        j = UsdPhysics.Joint(joints[0])
        assert j.GetLocalPos0Attr().Get() is not None
        tx = UsdPhysics.LimitAPI(joints[0], "transX")
        assert tx.GetLowAttr().Get() > tx.GetHighAttr().Get()  # locked
        ry = UsdPhysics.LimitAPI(joints[0], "rotY")
        assert ry.GetHighAttr().Get() == 25.0
        drv = UsdPhysics.DriveAPI(joints[0], "rotY")
        assert drv.GetStiffnessAttr().Get() > 0
        # attachment prims for composing
        assert st.GetPrimAtPath("/Cable/link_00/AttachA")
        assert st.GetPrimAtPath("/Cable/link_09/AttachB")

    def test_link_mass_is_linear_density(self, tmp_path):
        pytest.importorskip("pxr")
        import math

        from pxr import Usd, UsdPhysics

        from make_cable import RUBBER_DENSITY, build_cable
        f = build_cable(tmp_path / "c2.usda", length_m=1.0, radius_m=0.004,
                        links=20)
        st = Usd.Stage.Open(str(f))
        m = UsdPhysics.MassAPI(
            st.GetPrimAtPath("/Cable/link_00")).GetMassAttr().Get()
        physical = RUBBER_DENSITY * math.pi * 0.004 ** 2 * (1.0 / 20)
        # solver-stability floor: gram links crumple under load
        assert abs(m - max(physical, 0.015)) < 1e-6
        meta = st.GetRootLayer().customLayerData["cable"]
        assert abs(meta["physical_mass_kg"] - physical) < 1e-6


class TestCordedAssemblyRouting:
    def test_jointed_cable_assembly_is_articulated_not_deformable(self):
        from ingest_asset import propose_category
        # 'cord' matches the rope_cable prior, but authored joints mean
        # this is an articulation
        assert propose_category({
            "matched_class": "rope_cable", "callouts": [],
            "structure": {"joints": [{"path": "/World/Cord/joints/j_01",
                                      "type": "PhysicsJoint"}]},
        }) == "articulated_unverified"

    def test_jointless_rope_still_deformable(self):
        from ingest_asset import propose_category
        assert propose_category({
            "matched_class": "rope_cable", "callouts": [],
            "structure": {},
        }) == "deformable_unverified"


class TestRoutedCord:
    def _build(self, tmp_path, length=0.8):
        pytest.importorskip("pxr")
        from pxr import Gf, Usd, UsdGeom

        from make_cable import route_cord
        stage = Usd.Stage.CreateNew(str(tmp_path / "r.usda"))
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        got, _pts = route_cord(stage, "/Cord",
                         Gf.Vec3d(0, 0, 0.03), Gf.Vec3d(1, 0, 0),
                         Gf.Vec3d(0.5, 0, 0.01), Gf.Vec3d(-1, 0, 0),
                         length, 0.005, segments=30, ground_z=0.005)
        return stage, got

    def test_arc_length_matches_requested_cord(self, tmp_path):
        _, got = self._build(tmp_path, length=0.8)
        assert abs(got - 0.8) < 0.05   # slack shows as droop, not stretch

    def test_cord_is_static_geometry(self, tmp_path):
        from pxr import UsdPhysics
        stage, _ = self._build(tmp_path)
        segs = [p for p in stage.Traverse() if p.GetName().startswith("seg_")]
        assert len(segs) > 10
        # collidable scene dressing: colliders, but no rigid bodies to solve
        assert all(p.HasAPI(UsdPhysics.CollisionAPI) for p in segs)
        assert not any(p.HasAPI(UsdPhysics.RigidBodyAPI) for p in segs)

    def test_cord_never_dips_below_the_surface(self, tmp_path):
        from pxr import UsdGeom
        stage, _ = self._build(tmp_path)
        cache = UsdGeom.XformCache()
        zs = [cache.GetLocalToWorldTransform(p).ExtractTranslation()[2]
              for p in stage.Traverse() if p.GetName().startswith("seg_")]
        assert min(zs) >= 0.0   # clamped to the surface it lies on

    def test_meander_stays_a_fraction_of_the_run(self, tmp_path):
        # a short cord with lots of slack must not curl like a phone cord
        pytest.importorskip("pxr")
        from pxr import Gf, Usd, UsdGeom

        from make_cable import route_cord
        stage = Usd.Stage.CreateNew(str(tmp_path / "m.usda"))
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        route_cord(stage, "/Cord", Gf.Vec3d(0, 0, 0.02), Gf.Vec3d(1, 0, 0),
                   Gf.Vec3d(0.3, 0, 0.01), Gf.Vec3d(-1, 0, 0),
                   0.9, 0.005, segments=30, ground_z=0.005)
        cache = UsdGeom.XformCache()
        ys = [cache.GetLocalToWorldTransform(p).ExtractTranslation()[1]
              for p in stage.Traverse() if p.GetName().startswith("seg_")]
        assert max(abs(y) for y in ys) <= 0.3 * 0.25   # <= 25% of the run


class TestCordExitPriors:
    def test_classes_declare_where_their_cord_leaves(self):
        for cls in ("desk_lamp", "computer_mouse", "appliance_small"):
            assert PRIORS[cls].get("cord_exit") in ("min", "max"), cls


class TestRoutedAssemblyIsStatic:
    """The bug the user caught: in routed mode compose returned BEFORE the
    joint code, leaving the plug a free rigid body that the static cord's
    capsules shoved away the moment physics started."""

    def _build(self, tmp_path):
        pytest.importorskip("pxr")
        from make_cable import compose
        return compose(
            str(REPO / "workspace/assets_fixed/electric_kettle_simready.usda"),
            str(REPO / "workspace/assets_fixed/power_plug_european_simready.usda"),
            tmp_path / "a.usda", length_m=0.8, radius_m=0.005,
            upright=True, cord_mode="routed", tool_attach="min")

    def test_no_free_rigid_bodies(self, tmp_path):
        from pxr import Usd, UsdPhysics
        st = Usd.Stage.Open(str(self._build(tmp_path)))
        bodies = [p for p in st.Traverse()
                  if p.HasAPI(UsdPhysics.RigidBodyAPI)]
        assert bodies == [], "a routed assembly must be static dressing"
        assert any(p.HasAPI(UsdPhysics.CollisionAPI) for p in st.Traverse())

    def test_cord_tip_meets_the_plug_entry(self, tmp_path):
        from pxr import Gf, Usd, UsdGeom
        from make_cable import attach_frame
        out = self._build(tmp_path)
        st = Usd.Stage.Open(str(out))
        cache = UsdGeom.XformCache()
        segs = sorted((p for p in st.Traverse()
                       if p.GetName().startswith("seg_")),
                      key=lambda p: p.GetName())
        half = UsdGeom.Capsule(segs[-1]).GetHeightAttr().Get() / 2.0
        tip = cache.GetLocalToWorldTransform(segs[-1]).Transform(
            Gf.Vec3d(half, 0, 0))
        ppt, _ = attach_frame(
            str(REPO / "workspace/assets_fixed/power_plug_european_simready.usda"))
        entry = cache.GetLocalToWorldTransform(
            st.GetPrimAtPath("/World/Plug")).Transform(Gf.Vec3d(*ppt))
        assert (tip - entry).GetLength() < 1e-6

    def test_plug_lies_flat_and_level_on_the_surface(self, tmp_path):
        import math

        from pxr import Gf, Usd, UsdGeom
        from make_cable import attach_frame
        st = Usd.Stage.Open(str(self._build(tmp_path)))
        cache = UsdGeom.XformCache()
        m = cache.GetLocalToWorldTransform(st.GetPrimAtPath("/World/Plug"))
        _, pdir = attach_frame(
            str(REPO / "workspace/assets_fixed/power_plug_european_simready.usda"))
        axis = m.TransformDir(Gf.Vec3d(*pdir)).GetNormalized()
        tilt = abs(math.degrees(math.asin(max(-1.0, min(1.0, axis[2])))))
        assert tilt < 1.0, f"plug tilted {tilt:.1f} deg off horizontal"
        r = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                              [UsdGeom.Tokens.default_]).ComputeWorldBound(
            st.GetPrimAtPath("/World/Plug")).ComputeAlignedRange()
        assert r.GetMin()[2] > -0.003          # resting, not sunk
        size = r.GetSize()
        assert size[2] == min(size)            # lying flattest


class TestHumanAttachmentCapture:
    """A person dragging a plug into place in the viewport knows the join
    better than any heuristic — that correction becomes permanent data."""

    def test_stored_attachment_overrides_the_heuristic(self, tmp_path,
                                                       monkeypatch):
        pytest.importorskip("pxr")
        import importlib
        import make_cable
        store = tmp_path / "cord_attachments.json"
        store.write_text(json.dumps({"attachments": {
            "power_plug_european": {"local_point": [0.01, 0.02, 0.03],
                                    "local_dir": [0.0, 0.0, 1.0],
                                    "source": "human"}}}))
        monkeypatch.setattr(make_cable, "REPO", tmp_path.parent)
        # point the module's lookup at our temp store
        real = tmp_path.parent / "workspace" / "knowledge"
        real.mkdir(parents=True, exist_ok=True)
        (real / "cord_attachments.json").write_text(store.read_text())
        importlib.reload(make_cable)
        make_cable.REPO = tmp_path.parent
        pt, d = make_cable.attach_frame(
            str(REPO / "workspace/assets_fixed/power_plug_european_simready.usda"))
        assert [round(v, 3) for v in pt] == [0.01, 0.02, 0.03]
        assert [round(v, 1) for v in d] == [0.0, 0.0, 1.0]

    def test_capture_script_is_wired(self):
        import ast
        src = (REPO / "scripts" / "capture_attachment.py").read_text()
        ast.parse(src)
        assert "cord_attachments.json" in src
        assert "local_point" in src and "local_dir" in src


class TestDynamicCordPhysicalMass:
    """VBD (Newton) converges where PhysX needed a 15 g link-mass floor —
    so a dynamic cord can carry its real linear-density mass."""

    def test_physx_floor_is_opt_out(self, tmp_path):
        pytest.importorskip("pxr")
        from pxr import Usd, UsdPhysics

        from make_cable import RUBBER_DENSITY, build_cable
        import math
        floored = build_cable(tmp_path / "f.usda", length_m=1.0,
                              radius_m=0.004, links=20)
        physical = build_cable(tmp_path / "p.usda", length_m=1.0,
                               radius_m=0.004, links=20, physx_floor=False)

        def first_mass(path):
            st = Usd.Stage.Open(str(path))
            for p in st.Traverse():
                if p.HasAPI(UsdPhysics.MassAPI):
                    return UsdPhysics.MassAPI(p).GetMassAttr().Get()
            return None

        real = RUBBER_DENSITY * math.pi * 0.004 ** 2 * (1.0 / 20)
        assert abs(first_mass(floored) - 0.015) < 1e-6      # PhysX floor
        assert abs(first_mass(physical) - real) < 1e-6      # physical
        assert real < 0.015

    def test_cord_metadata_lets_newton_rebuild_it(self, tmp_path):
        pytest.importorskip("pxr")
        from pxr import Usd

        from make_cable import build_cable
        out = build_cable(tmp_path / "c.usda", length_m=0.8, radius_m=0.005,
                          links=16, physx_floor=False)
        st = Usd.Stage.Open(str(out))          # keep the stage alive
        meta = dict(st.GetRootLayer().customLayerData)["cable"]
        assert meta["length_m"] == 0.8 and meta["radius_m"] == 0.005
        assert meta["links"] == 16 and meta["physx_floor"] is False


class TestActuatedCable:
    """The last open case: a gripper grasping and MOVING a cord. The
    kinematic-driver pattern (zero inverse mass set before finalize, pose
    driven each substep) works for VBD rigid rods."""

    def test_grasp_benchmark_is_wired(self):
        import ast
        src = (REPO / "scripts" / "verify_asset_newton.py").read_text()
        ast.parse(src)
        assert "def grasp(" in src
        assert '"grasp": grasp' in src
        # the criteria that make it a real benchmark, not a smoke test
        for k in ("gripper_follow_error_m", "arc_stretch_ratio",
                  "anchor_drift_m", "cable_survives_manipulation"):
            assert k in src, k

    def test_dynamic_compose_can_author_physical_mass(self, tmp_path):
        pytest.importorskip("pxr")
        from pxr import Usd

        from make_cable import compose
        out = compose(
            str(REPO / "workspace/assets_fixed/soldering_iron_1_simready.usda"),
            str(REPO / "workspace/assets_fixed/power_plug_european_simready.usda"),
            tmp_path / "d.usda", length_m=0.8, radius_m=0.005, links=20,
            cord_mode="dynamic", upright=False, physx_floor=False)
        st = Usd.Stage.Open(str(out))
        meta = dict(st.GetRootLayer().customLayerData)["cord"]
        assert meta["mode"] == "dynamic"
        assert meta["physx_floor"] is False      # Newton runs real mass
        assert meta["length_m"] == 0.8 and meta["links"] == 20


# ---------------------------------------------------------------------------
# Cloth actuation. Both bugs below were SILENT: the sim ran, printed
# plausible numbers, and was wrong.
# ---------------------------------------------------------------------------

def _newton_src():
    return (Path(__file__).resolve().parents[1] / "scripts" /
            "verify_asset_newton.py").read_text()


def test_device_helper_does_not_recurse():
    """A blanket replace once made _device()'s CPU fallback call _device()."""
    body = _newton_src().split("def _device()", 1)[1].split("\ndef ", 1)[0]
    assert "\n    _device()" not in body, "infinite recursion in _device()"
    assert 'wp.set_device("cpu")' in body


def test_cloth_solver_is_told_bodies_move_externally():
    """Without this flag VBD ignores rigid shapes entirely and the cloth
    falls straight through the fingers — no error, just a garment 25 m
    below the gripper. Newton 1.5's full-surface A/B instead lets VBD own
    those rigid bodies, while the supported particle baseline stays external.
    """
    body = _newton_src().split("def cloth_grasp", 1)[1]
    assert ("integrate_with_external_rigid_solver=not full_surface_contact"
            in body)


@pytest.mark.parametrize("fn", ["def grasp", "def cloth_grasp"])
def test_body_poses_are_assigned_back_not_mutated_in_place(fn):
    """wp.array.numpy() is a VIEW on CPU but a COPY on CUDA. Mutating it
    drives the gripper on CPU and does nothing at all on GPU."""
    body = _newton_src().split(fn, 1)[1].split("\ndef ", 1)[0]
    if "body_q.numpy()" not in body:
        pytest.skip(f"{fn} does not pose-drive bodies")
    assert "body_q.numpy().copy()" in body
    assert "body_q.assign(" in body


def test_grasp_verdict_does_not_require_the_centroid_to_rise():
    """A garment lifted by one corner from flat MUST lose centroid height —
    it stops being a sheet and becomes a hanging one. Gating on
    centroid_rise fails a perfect grasp."""
    body = _newton_src().split("def cloth_grasp", 1)[1]
    verdict = [l for l in body.splitlines() if l.strip().startswith("ok =")]
    assert verdict, "cloth_grasp must compute a verdict"
    assert "centroid_rise" not in verdict[0]
    for need in ("held_err", "suspended", "hangs", "settled"):
        assert need in verdict[0]


def test_cloth_damping_is_viscous_not_stiffness():
    """tri_kd above ~0.1 detonates the cloth at any affordable substep
    count (the corner flew 100 m). Energy is bled off viscously instead."""
    src = _newton_src()
    assert "CLOTH_DAMP_HZ" in src
    assert float(src.split('"CLOTH_TRI_KD", "', 1)[1].split('"')[0]) <= 0.1


# ---------------------------------------------------------------------------
# Cloth as a pick-and-place workpiece. The rigid path grasps by welding a
# UsdPhysics.FixedJoint to the object; a FixedJoint has no deformable body to
# bind, so on cloth it defines cleanly and holds nothing.
# ---------------------------------------------------------------------------

def _svc():
    import sys
    p = str(Path(__file__).resolve().parents[1] / "service")
    if p not in sys.path:
        sys.path.insert(0, p)


def test_cloth_workpieces_are_in_the_palette():
    _svc()
    from isaac_assist_service.multimodal.object_palette import PALETTE
    cloth = {k for k, v in PALETTE.items() if "deformable" in v.tags}
    assert {"washcloth", "napkin", "towel", "tshirt"} <= cloth
    for k in cloth:
        assert "workpiece" in PALETTE[k].tags, f"{k} must be pickable"


def test_cloth_is_never_given_a_rigid_body():
    """RigidBodyAPI on a garment is simply the wrong physics, and it is what
    makes the weld-grasp look like it should work."""
    _svc()
    from isaac_assist_service.multimodal import instantiator as I
    src = Path(I.__file__).read_text()
    rigid = src.split("_RIGID_WORKPIECE_CLASSES = {", 1)[1].split("}", 1)[0]
    for c in ("washcloth", "napkin", "towel", "tshirt"):
        assert f'"{c}"' not in rigid, f"{c} must not be a rigid workpiece"
    deform = src.split("_DEFORMABLE_WORKPIECE_CLASSES = {", 1)[1].split("}", 1)[0]
    for c in ("washcloth", "napkin", "towel", "tshirt"):
        assert f'"{c}"' in deform


def test_generated_scene_authors_cloth_not_a_rigid_body():
    _svc()
    from isaac_assist_service.multimodal.instantiator import (
        LayoutSpecCodeGenerator,
    )
    code = LayoutSpecCodeGenerator().generate_full_script([{
        "name": "Towel_1", "type": "Mesh", "path": "/World/Towel_1",
        "position": [0, 0, 0.8],
        "extra_attrs": {"_isaac_assist_physics": {
            "collision": True, "rigid_body": False,
            "deformable": True, "cloth_preset": "cloth_cotton"}},
    }])
    assert "_apply_cloth(prim.GetPrim()" in code
    assert "'cloth_cotton'" in code
    assert "PhysxDeformableSurfaceAPI" in code
    assert "_apply_rigid_body(prim.GetPrim()" not in code
    compile(code, "<generated>", "exec")


def test_pick_place_grasps_cloth_by_friction_not_a_fixed_joint():
    _svc()
    from isaac_assist_service.chat.tools.handlers.pick_place import (
        _gen_setup_pick_place_controller,
    )
    code = _gen_setup_pick_place_controller({
        "robot_path": "/World/Franka", "target_source": "cube_tracking",
        "source_paths": ["/World/Towel_1"], "destination_path": "/World/Bin"})
    assert "def _is_deformable" in code
    assert "GRIP_CLOSE_CLOTH" in code
    # the deformable branch must come BEFORE the joint is defined
    attach = code.split("def _attach_cube_to_ee", 1)[1]
    assert attach.index("_is_deformable") < attach.index("FixedJoint.Define")
    # and the rigid path must be untouched
    assert "UsdPhysics.FixedJoint.Define(stage, joint_path)" in code
    compile(code, "<generated>", "exec")


def test_newton_cloth_pick_place_measures_where_the_cloth_ended_up():
    """The verdict must rest on the cloth's own final state, not on whether
    the arm reached its waypoints — an arm can complete the whole trajectory
    having never picked anything up."""
    src = (Path(__file__).resolve().parents[1] / "scripts" /
           "pick_place_cloth.py").read_text()
    verdict = [l for l in src.splitlines() if l.strip().startswith("ok =")]
    assert verdict, "pick_place_cloth must compute a verdict"
    for need in ("carried", "intact", "landed", "settled"):
        assert need in verdict[0]


# ---------------------------------------------------------------------------
# The Franka pick scene is workpiece-agnostic. It used to hardcode 5 cm rigid
# cubes, which silently decided both the physics and the layout.
# ---------------------------------------------------------------------------

def _mcp():
    _svc()
    from isaac_assist_service import mcp_floorplan_tools as m
    return m


@pytest.mark.parametrize("bad,why", [
    ("franka_panda", "robot"), ("table_large", "fixture"),
    ("no_such_thing", "unknown"),
])
def test_only_pickable_classes_are_accepted_as_workpieces(bad, why):
    m = _mcp()
    with pytest.raises(ValueError):
        m._workpiece_profile(bad)


def test_cloth_workpiece_gets_cloth_physics_and_no_rigid_mass():
    m = _mcp()
    cloth = m._workpiece_profile("towel")
    assert cloth["deformable"] is True
    assert cloth["metadata"]["physics"] == "deformable_surface"
    assert "mass_kg" not in cloth["metadata"], "a garment is not a rigid mass"
    rigid = m._workpiece_profile("cube_small")
    assert rigid["deformable"] is False
    assert rigid["metadata"]["physics"] == "dynamic_rigid_body"
    assert rigid["metadata"]["mass_kg"] > 0


def test_rigid_body_assertion_is_conditional_on_the_workpiece():
    """Asserting require_rigid_body_api_for_workpieces on a cloth scene would
    fail every such scene, or worse, 'repair' the garment into a rigid body."""
    m = _mcp()
    for wp, rigid_expected in (("cube_small", True), ("towel", False)):
        spec = m._franka_pick_scene_spec(description="d", motion_backend="auto",
                                         object_count=2, workpiece=wp)
        phys = spec.parameters["physics"]
        assert phys["require_rigid_body_api_for_workpieces"] is rigid_expected
        assert phys["require_deformable_api_for_workpieces"] is (not rigid_expected)
        assert phys["workpiece_class"] == wp


@pytest.mark.parametrize("wp", ["cube_small", "bolt", "washcloth", "napkin",
                                "towel", "tshirt"])
def test_workpieces_are_placed_on_the_table_not_off_the_end(wp):
    """Spacing comes from the workpiece's own footprint, so a large one would
    space itself past the table edge — three 1.4 m towels would land at
    x = 0.38, 2.34 and 4.30 against a table that stops at 1.55."""
    m = _mcp()
    spec = m._franka_pick_scene_spec(description="d", motion_backend="auto",
                                     object_count=3, workpiece=wp)
    picks = [o.model_dump() for o in spec.objects
             if str(o.name).startswith("PickObject")]
    assert picks, "a pick scene needs something to pick"
    for p in picks:
        assert p["position"]["x"] <= m._PICK_TABLE_X_MAX + 1e-6, \
            f"{wp} placed at x={p['position']['x']} beyond the table"
    phys = spec.parameters["physics"]
    # whatever could not fit must be REPORTED, never silently dropped
    assert (phys["object_count_placed"] + phys["object_count_dropped"]
            == phys["object_count_requested"] == 3)
    assert phys["object_count_placed"] == len(picks)


def test_controller_plan_picks_the_grip_style_from_the_workpiece():
    m = _mcp()
    for wp, style in (("cube_small", "fixed_joint"), ("towel", "friction")):
        plan = m._franka_controller_plan(
            motion_backend="auto", object_count=1,
            generate_controller_code=True,
            deformable=m._workpiece_profile(wp)["deformable"])
        assert plan["controller_args"]["grip_style"] == style
        code = plan["controller_code"]
        assert code, "controller code should have been generated"
        compile(code, "<generated>", "exec")
        assert f"GRIP_STYLE = {style!r}" in code
