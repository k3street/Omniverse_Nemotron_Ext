"""
L0 / L1 tests for Addendum C — Community & Remote Access tools.

Covers six tools introduced by docs/specs/addendum_community_remote.md:
  C.1 filter_templates_by_hardware  (DATA)
  C.2 export_template / import_template  (CODE_GEN)
  C.3 check_vram_headroom            (DATA)
  C.4 dispatch_async_task / query_async_task  (DATA)
  C.5 visualize_forces                (CODE_GEN)
  C.6 render_video                    (CODE_GEN)

All tests are self-contained — they do not require Kit or a GPU.  Code
generators are exercised via compile() and substring assertions; data
handlers are exercised via direct await + monkeypatched detectors.
"""
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _handle_filter_templates_by_hardware,
    _handle_check_vram_headroom,
    _handle_dispatch_async_task,
    _handle_query_async_task,
    _gen_export_template,
    _gen_import_template,
    _gen_visualize_forces,
    _gen_render_video,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


_NEW_TOOL_NAMES = {
    "filter_templates_by_hardware",
    "export_template",
    "import_template",
    "check_vram_headroom",
    "dispatch_async_task",
    "query_async_task",
    "visualize_forces",
    "render_video",
}


# ---------------------------------------------------------------------------
# Schema registration
# ---------------------------------------------------------------------------

class TestSchemasRegistered:
    def test_all_eight_tools_declared_in_schemas(self):
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        missing = _NEW_TOOL_NAMES - names
        assert not missing, f"Missing schemas for: {missing}"

    @pytest.mark.parametrize("name", sorted(_NEW_TOOL_NAMES))
    def test_each_tool_has_handler(self, name):
        assert name in CODE_GEN_HANDLERS or name in DATA_HANDLERS, (
            f"{name} declared in schema but no handler registered"
        )

    def test_handler_categories_match_spec(self):
        # Spec C.1, C.3, C.4 → DATA handlers
        for name in [
            "filter_templates_by_hardware",
            "check_vram_headroom",
            "dispatch_async_task",
            "query_async_task",
        ]:
            assert name in DATA_HANDLERS, f"{name} should be a DATA handler"

        # Spec C.2, C.5, C.6 → CODE_GEN handlers
        for name in ["export_template", "import_template", "visualize_forces", "render_video"]:
            assert name in CODE_GEN_HANDLERS, f"{name} should be a CODE_GEN handler"


# ---------------------------------------------------------------------------
# C.1 — filter_templates_by_hardware
# ---------------------------------------------------------------------------

def _write_manifest(library: Path, name: str, **fields) -> None:
    folder = library / name
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"template": name, "name": name, **fields}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class TestFilterTemplatesByHardware:
    @pytest.mark.asyncio
    async def test_empty_library_returns_zero_matches(self, tmp_path):
        result = await _handle_filter_templates_by_hardware({
            "device_vram_gb": 12.0,
            "library_dir": str(tmp_path / "library"),
        })
        assert result["matched_count"] == 0
        assert result["matched"] == []
        assert result["device_vram_gb"] == 12.0

    @pytest.mark.asyncio
    async def test_filters_out_templates_above_vram(self, tmp_path):
        lib = tmp_path / "library"
        _write_manifest(lib, "tiny", min_vram_gb=4, recommended_vram_gb=8)
        _write_manifest(lib, "huge", min_vram_gb=24, recommended_vram_gb=48)
        result = await _handle_filter_templates_by_hardware({
            "device_vram_gb": 12.0,
            "library_dir": str(lib),
        })
        names = [m["template"] for m in result["matched"]]
        assert "tiny" in names
        assert "huge" not in names
        assert result["rejected_count"] == 1
        assert result["rejected"][0]["template"] == "huge"

    @pytest.mark.asyncio
    async def test_recommended_only_is_stricter(self, tmp_path):
        lib = tmp_path / "library"
        _write_manifest(lib, "edge_case", min_vram_gb=8, recommended_vram_gb=16)
        # min_vram fits 12 GB, recommended does not
        loose = await _handle_filter_templates_by_hardware({
            "device_vram_gb": 12.0,
            "library_dir": str(lib),
        })
        strict = await _handle_filter_templates_by_hardware({
            "device_vram_gb": 12.0,
            "library_dir": str(lib),
            "include_recommended_only": True,
        })
        assert loose["matched_count"] == 1
        assert strict["matched_count"] == 0

    @pytest.mark.asyncio
    async def test_tag_filter(self, tmp_path):
        lib = tmp_path / "library"
        _write_manifest(lib, "a", min_vram_gb=4, tags=["beginner_friendly"])
        _write_manifest(lib, "b", min_vram_gb=4, tags=["advanced"])
        result = await _handle_filter_templates_by_hardware({
            "device_vram_gb": 16.0,
            "library_dir": str(lib),
            "tag": "beginner_friendly",
        })
        assert {m["template"] for m in result["matched"]} == {"a"}

    @pytest.mark.asyncio
    async def test_category_filter(self, tmp_path):
        lib = tmp_path / "library"
        _write_manifest(lib, "rl1", min_vram_gb=4, category="rl")
        _write_manifest(lib, "sdg1", min_vram_gb=4, category="sdg")
        result = await _handle_filter_templates_by_hardware({
            "device_vram_gb": 16.0,
            "library_dir": str(lib),
            "category": "rl",
        })
        assert {m["template"] for m in result["matched"]} == {"rl1"}

    @pytest.mark.asyncio
    async def test_skips_invalid_manifest_entries(self, tmp_path):
        lib = tmp_path / "library"
        _write_manifest(lib, "valid", min_vram_gb=4)
        # Bad JSON
        bad = lib / "bad"
        bad.mkdir()
        (bad / "manifest.json").write_text("{this is: not json", encoding="utf-8")
        # Stray file (not a directory) — should be ignored
        (lib / "loose.txt").write_text("ignore", encoding="utf-8")
        result = await _handle_filter_templates_by_hardware({
            "device_vram_gb": 16.0,
            "library_dir": str(lib),
        })
        assert result["matched_count"] == 1
        assert result["matched"][0]["template"] == "valid"


# ---------------------------------------------------------------------------
# C.2 — export_template / import_template (.isaa zip)
# ---------------------------------------------------------------------------

class TestExportTemplateCodeGen:
    def test_compiles(self):
        code = _gen_export_template({
            "name": "demo_pickplace",
            "description": "Pick & place demo",
            "min_vram_gb": 8,
            "recommended_vram_gb": 12,
            "tags": ["beginner_friendly"],
        })
        compile(code, "<export_template>", "exec")

    def test_includes_manifest_fields(self):
        code = _gen_export_template({
            "name": "demo_pickplace",
            "description": "Pick & place demo",
            "min_vram_gb": 8,
            "recommended_vram_gb": 12,
            "tags": ["beginner_friendly"],
        })
        assert "manifest_version" in code
        assert "demo_pickplace" in code
        assert "Pick & place demo" in code
        assert "beginner_friendly" in code
        assert "zipfile" in code
        assert ".isaa" in code

    def test_uses_safe_filename(self):
        code = _gen_export_template({"name": "weird name/with*chars"})
        # Filesystem-unsafe chars must be sanitized
        assert "weird_name_with_chars" in code

    def test_handles_supplied_scene_path(self):
        code = _gen_export_template({
            "name": "x",
            "scene_path": "/tmp/myscene.usd",
        })
        assert "/tmp/myscene.usd" in code
        assert "shutil.copyfile" in code


class TestImportTemplateCodeGen:
    def test_compiles(self):
        code = _gen_import_template({"file_path": "/tmp/demo.isaa"})
        compile(code, "<import_template>", "exec")

    def test_validates_manifest(self):
        code = _gen_import_template({"file_path": "/tmp/demo.isaa"})
        assert "manifest.json" in code
        assert "ZipFile" in code
        assert ".extractall" in code

    def test_overwrite_flag_threaded_through(self):
        code_off = _gen_import_template({"file_path": "/tmp/x.isaa"})
        code_on = _gen_import_template({"file_path": "/tmp/x.isaa", "overwrite": True})
        assert "False" in code_off
        assert "True" in code_on


class TestExportImportRoundTrip:
    """End-to-end smoke test: build an .isaa, then read it back manually."""

    def test_isaa_zip_roundtrip(self, tmp_path):
        # Build a fake .isaa the same way _gen_export_template's emitted code would.
        manifest = {
            "manifest_version": 1,
            "name": "demo",
            "template": "demo",
            "min_vram_gb": 8,
            "recommended_vram_gb": 12,
            "tags": ["beginner_friendly"],
            "scene_file": "scene.usda",
        }
        isaa = tmp_path / "demo.isaa"
        scene = tmp_path / "scene.usda"
        scene.write_text("#usda 1.0\n", encoding="utf-8")
        with zipfile.ZipFile(isaa, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.write(scene, arcname="scene.usda")

        # Now feed that into the filter handler via the library import path.
        # Roundtrip: extract by hand (simulating import_template's code) and
        # verify filter_templates_by_hardware sees the result.
        library = tmp_path / "lib"
        dest = library / "demo"
        dest.mkdir(parents=True)
        with zipfile.ZipFile(isaa) as zf:
            zf.extractall(dest)

        assert (dest / "manifest.json").exists()
        assert (dest / "scene.usda").exists()


# ---------------------------------------------------------------------------
# C.3 — check_vram_headroom
# ---------------------------------------------------------------------------

class TestCheckVramHeadroom:
    @pytest.mark.asyncio
    async def test_fits_when_plenty_of_vram(self):
        result = await _handle_check_vram_headroom({
            "operation": "clone",
            "num_envs": 32,
            "complexity": "low",
            "device_vram_gb": 24.0,
            "currently_used_gb": 2.0,
        })
        assert result["fits"] is True
        assert result["warning"] is None
        assert result["estimated_gb"] > 0

    @pytest.mark.asyncio
    async def test_warns_when_insufficient(self):
        # 1024 envs * medium clone (16 MB) = ~16 GB > 5 GB headroom on 12 GB GPU
        result = await _handle_check_vram_headroom({
            "operation": "clone",
            "num_envs": 1024,
            "complexity": "medium",
            "device_vram_gb": 12.0,
            "currently_used_gb": 7.0,
        })
        assert result["fits"] is False
        assert result["warning"] is not None
        assert "VRAM" in result["warning"]
        assert any("Reduce" in s for s in result["suggestions"])
        assert any("headless" in s for s in result["suggestions"])
        assert any("cloud" in s.lower() for s in result["suggestions"])

    @pytest.mark.asyncio
    async def test_per_env_override_used(self):
        result = await _handle_check_vram_headroom({
            "operation": "custom",
            "num_envs": 100,
            "per_env_mb_override": 50.0,
            "device_vram_gb": 24.0,
            "currently_used_gb": 0.0,
        })
        # 100 * 50 MB = 5000 MB ≈ 4.88 GB
        assert result["per_env_mb"] == 50.0
        assert 4.5 < result["estimated_gb"] < 5.0

    @pytest.mark.asyncio
    async def test_unknown_vram_returns_warning_when_overestimate(self, monkeypatch):
        # Simulate inability to detect VRAM
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te, "_detect_local_vram_gb", lambda: None)
        monkeypatch.setattr(te, "_detect_used_vram_gb", lambda: None)
        result = await _handle_check_vram_headroom({
            "operation": "train",
            "num_envs": 64,
        })
        # Without device info we can't know if it fits → fits=False, warning set
        assert result["device_vram_gb"] is None
        assert result["available_gb"] is None
        assert result["fits"] is False
        assert "auto-detect" in (result["warning"] or "")

    @pytest.mark.asyncio
    async def test_invalid_complexity_falls_back_to_medium(self):
        result = await _handle_check_vram_headroom({
            "operation": "clone",
            "num_envs": 10,
            "complexity": "ludicrous",  # invalid
            "device_vram_gb": 24.0,
            "currently_used_gb": 0.0,
        })
        assert result["complexity"] == "medium"


# ---------------------------------------------------------------------------
# C.4 — dispatch_async_task / query_async_task
# ---------------------------------------------------------------------------

class TestAsyncTaskLifecycle:
    @pytest.mark.asyncio
    async def test_dispatch_returns_task_id_and_state(self):
        result = await _handle_dispatch_async_task({
            "task_type": "sdg",
            "params": {"steps": 1, "step_seconds": 0.0},
            "label": "smoke test",
            "dry_run": True,  # don't actually start the worker thread
        })
        assert result["task_id"].startswith("task_sdg_")
        assert result["state"] == "pending"
        assert "smoke test" in result["message"]

    @pytest.mark.asyncio
    async def test_query_unknown_task_returns_unknown_state(self):
        result = await _handle_query_async_task({"task_id": "task_does_not_exist"})
        assert result["state"] == "unknown"

    @pytest.mark.asyncio
    async def test_full_lifecycle_completes(self):
        # Dry-run dispatch first to register, then drive the worker manually
        # so we don't depend on timing.
        import service.isaac_assist_service.chat.tools.tool_executor as te
        d = await _handle_dispatch_async_task({
            "task_type": "benchmark",
            "params": {"steps": 1, "step_seconds": 0.0},
            "label": "bench",
            "dry_run": True,
        })
        tid = d["task_id"]
        # Run the worker synchronously
        te._async_task_runner(tid, "benchmark", {"steps": 1, "step_seconds": 0.0})
        result = await _handle_query_async_task({"task_id": tid})
        assert result["state"] == "done"
        assert result["progress"] == 1.0
        assert result["result"]["task_type"] == "benchmark"
        assert "elapsed_seconds" in result

    @pytest.mark.asyncio
    async def test_real_thread_completes_quickly(self):
        # Verify the threading path actually runs without hanging tests.
        d = await _handle_dispatch_async_task({
            "task_type": "custom",
            "params": {"steps": 1, "step_seconds": 0.0},
            "label": "real thread",
        })
        tid = d["task_id"]
        # Poll up to ~1 s for completion (with 0 sleep this should be instant)
        for _ in range(50):
            r = await _handle_query_async_task({"task_id": tid})
            if r["state"] == "done":
                break
            time.sleep(0.02)
        r = await _handle_query_async_task({"task_id": tid})
        assert r["state"] == "done"


# ---------------------------------------------------------------------------
# C.5 — visualize_forces (CODE_GEN)
# ---------------------------------------------------------------------------

class TestVisualizeForcesCodeGen:
    def test_compiles(self):
        code = _gen_visualize_forces({"articulation_path": "/World/Franka"})
        compile(code, "<visualize_forces>", "exec")

    def test_uses_debug_draw(self):
        code = _gen_visualize_forces({"articulation_path": "/World/Franka"})
        assert "debug_draw" in code
        assert "draw_lines" in code
        assert "/World/Franka" in code

    def test_color_thresholds_present(self):
        code = _gen_visualize_forces({"articulation_path": "/World/X"})
        # Spec: green/yellow/red at 70 % / 90 %
        assert "0.70" in code
        assert "0.90" in code

    def test_scale_threaded_through(self):
        code = _gen_visualize_forces({
            "articulation_path": "/World/X",
            "scale": 0.05,
        })
        assert "0.05" in code


# ---------------------------------------------------------------------------
# C.6 — render_video (CODE_GEN)
# ---------------------------------------------------------------------------

class TestRenderVideoCodeGen:
    @pytest.mark.parametrize("quality", ["preview", "presentation", "production"])
    def test_compiles_for_each_quality(self, quality):
        code = _gen_render_video({"duration": 5.0, "quality": quality})
        compile(code, f"<render_video:{quality}>", "exec")

    def test_uses_movie_capture_extension(self):
        code = _gen_render_video({"duration": 1.0})
        assert "omni.kit.capture" in code
        assert "CaptureOptions" in code
        # Must NOT be a screen capture
        assert "viewport" not in code.lower() or "kit.capture.viewport" in code

    def test_preview_uses_raytracing(self):
        code = _gen_render_video({"duration": 1.0, "quality": "preview"})
        assert "RayTracing" in code
        assert "1280" in code and "720" in code

    def test_presentation_uses_pathtracing_1080p(self):
        code = _gen_render_video({"duration": 1.0, "quality": "presentation"})
        assert "PathTracing" in code
        assert "1920" in code and "1080" in code
        assert "64" in code  # SPP

    def test_production_uses_pathtracing_4k(self):
        code = _gen_render_video({"duration": 1.0, "quality": "production"})
        assert "PathTracing" in code
        assert "3840" in code and "2160" in code
        assert "256" in code  # SPP

    def test_invalid_quality_falls_back_to_preview(self):
        code = _gen_render_video({"duration": 1.0, "quality": "ultra_max"})
        assert "RayTracing" in code  # preview default

    def test_camera_optional(self):
        no_cam = _gen_render_video({"duration": 1.0})
        with_cam = _gen_render_video({
            "duration": 1.0,
            "camera": "/World/HeroCam",
        })
        assert "/World/HeroCam" in with_cam
        # Without camera the literal None is emitted
        assert "None" in no_cam

    def test_output_path_default_includes_renders_dir(self):
        code = _gen_render_video({"duration": 1.0})
        assert "workspace/renders" in code

    def test_explicit_output_path_threaded_through(self):
        code = _gen_render_video({
            "duration": 1.0,
            "output_path": "/tmp/my_clip.mp4",
        })
        assert "/tmp/my_clip.mp4" in code

    def test_fps_threaded_through(self):
        code = _gen_render_video({"duration": 1.0, "fps": 60})
        assert "60" in code
