"""Phase 102 contract test — macOS / Windows binaries are intentionally
deferred (user excluded macOS/Windows release work). Only the metadata
shape is verified; no implementation is expected on this branch.
"""
import pytest

pytestmark = pytest.mark.l0


def test_phase_102_metadata_shape():
    """Confirms the scaffold reports the right phase ID + status.

    Phase 102 is intentionally left as a scaffold. If you re-implement
    it, replace this test with the full spec contract.
    """
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        get_phase_metadata,
    )
    md = get_phase_metadata()
    assert md["phase"] == 102
    assert md["status"] == "scaffold", (
        "Phase 102 is user-skipped (macOS/Windows release). "
        "If you've landed an implementation, update both the module "
        "PHASE_STATUS and this test."
    )
