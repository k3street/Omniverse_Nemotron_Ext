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
        ast.parse((REPO / "scripts" / "verify_asset_newton.py").read_text())

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
