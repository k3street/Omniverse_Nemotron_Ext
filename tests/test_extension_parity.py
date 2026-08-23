from scripts.audit_extension_parity import parity_errors, shared_paths

import pytest

pytestmark = pytest.mark.l0


def test_shared_extension_manifest_is_nonempty():
    assert len(shared_paths()) >= 10


def test_intentionally_shared_extension_files_do_not_drift():
    assert parity_errors() == []
