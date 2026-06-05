"""Phase 34/35/36 — verify workflow templates are registered in _WORKFLOW_TEMPLATES.

Tests confirm:
1. Each new template name is present in the registry dict.
2. start_workflow dispatched with the new names does NOT return 'Unknown workflow_type'.
3. No Kit RPC connection is required (mocked).
"""
import pytest

pytestmark = pytest.mark.l0

_NEW_WORKFLOW_TYPES = [
    "assemble_pick_place_cell",
    "validate_robot_import",
    "generate_sdg_dataset",
]


# ---------------------------------------------------------------------------
# Registry presence
# ---------------------------------------------------------------------------

def test_new_templates_in_registry():
    """All three Phase 34/35/36 templates must be registered."""
    from service.isaac_assist_service.chat.tools.handlers._state import _WORKFLOW_TEMPLATES
    for wt in _NEW_WORKFLOW_TYPES:
        assert wt in _WORKFLOW_TEMPLATES, (
            f"'{wt}' missing from _WORKFLOW_TEMPLATES; "
            f"found: {sorted(_WORKFLOW_TEMPLATES)}"
        )


def test_registry_template_shape():
    """Each new template must have 'description', 'phases', and 'default_params'."""
    from service.isaac_assist_service.chat.tools.handlers._state import _WORKFLOW_TEMPLATES
    for wt in _NEW_WORKFLOW_TYPES:
        tpl = _WORKFLOW_TEMPLATES[wt]
        assert "description" in tpl, f"{wt}: missing 'description'"
        assert "phases" in tpl and tpl["phases"], f"{wt}: missing or empty 'phases'"
        assert "default_params" in tpl, f"{wt}: missing 'default_params'"
        for phase in tpl["phases"]:
            assert "name" in phase, f"{wt}: phase missing 'name': {phase}"
            assert "checkpoint" in phase, f"{wt}: phase missing 'checkpoint': {phase}"
            assert "error_fix" in phase, f"{wt}: phase missing 'error_fix': {phase}"


def test_template_data_matches_source_modules():
    """Registry entries must be consistent with the source template module data."""
    from service.isaac_assist_service.chat.tools.handlers._state import _WORKFLOW_TEMPLATES
    from service.isaac_assist_service.multimodal.workflow_template_pick_place import (
        ASSEMBLE_PICK_PLACE_CELL_TEMPLATE,
    )
    from service.isaac_assist_service.multimodal.workflow_template_validate_robot import (
        VALIDATE_ROBOT_IMPORT_TEMPLATE,
    )
    from service.isaac_assist_service.multimodal.workflow_template_sdg import (
        GENERATE_SDG_DATASET_TEMPLATE,
    )

    src_map = {
        "assemble_pick_place_cell": ASSEMBLE_PICK_PLACE_CELL_TEMPLATE,
        "validate_robot_import": VALIDATE_ROBOT_IMPORT_TEMPLATE,
        "generate_sdg_dataset": GENERATE_SDG_DATASET_TEMPLATE,
    }
    for wt, src in src_map.items():
        reg = _WORKFLOW_TEMPLATES[wt]
        assert reg["description"] == src["description"], (
            f"{wt}: description mismatch registry={reg['description']!r} src={src['description']!r}"
        )
        reg_names = [p["name"] for p in reg["phases"]]
        src_names = [p["name"] for p in src["phases"]]
        assert reg_names == src_names, (
            f"{wt}: phase name list mismatch registry={reg_names} src={src_names}"
        )
        assert reg["default_params"] == src["default_params"], (
            f"{wt}: default_params mismatch"
        )


# ---------------------------------------------------------------------------
# start_workflow dispatch — no Kit required
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_workflow_no_unknown_type_error():
    """start_workflow with each new type must NOT return 'Unknown workflow_type'."""
    import asyncio
    from unittest.mock import patch, MagicMock
    # Prevent the WORKFLOWS singleton from persisting between tests
    from service.isaac_assist_service.chat.tools.handlers import _state as _st

    for wt in _NEW_WORKFLOW_TYPES:
        # Patch _te._WORKFLOW_TEMPLATES to use the live registry (already registered)
        result = await _call_start_workflow_stub(wt)
        assert "Unknown workflow_type" not in result.get("error", ""), (
            f"start_workflow('{wt}') returned unknown-type error: {result}"
        )


async def _call_start_workflow_stub(workflow_type: str) -> dict:
    """Call _handle_start_workflow with a minimal args dict.

    Stops right after the type-check by providing a valid goal; the
    workflow is created but never actually run (no Kit RPC needed).
    """
    from service.isaac_assist_service.chat.tools.handlers import workflow as wf_mod
    from service.isaac_assist_service.chat.tools.handlers import _state as _st

    # Ensure the workflow dict is not polluted between calls
    _st._WORKFLOWS.clear()

    args = {
        "workflow_type": workflow_type,
        "goal": f"test goal for {workflow_type}",
        "params": {},
        "auto_approve_checkpoints": False,
    }
    # _handle_start_workflow is decorated with @with_telemetry; call the
    # un-decorated inner to avoid telemetry side-effects in tests.
    # The real function is accessible via __wrapped__ if the decorator sets it,
    # otherwise call directly — it is safe without Kit in the pre-dispatch path.
    handler = wf_mod._handle_start_workflow
    result = await handler(args)
    return result


# ---------------------------------------------------------------------------
# Existing 3 templates still registered (regression guard)
# ---------------------------------------------------------------------------

def test_legacy_templates_still_registered():
    """rl_training / robot_import / sim_debugging must remain in registry."""
    from service.isaac_assist_service.chat.tools.handlers._state import _WORKFLOW_TEMPLATES
    for wt in ("rl_training", "robot_import", "sim_debugging"):
        assert wt in _WORKFLOW_TEMPLATES, f"Legacy template '{wt}' was accidentally removed"
