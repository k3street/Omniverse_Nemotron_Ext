"""A door's magnetic gasket (behaviors.gasket): the stated opening force at
the handle while shut, gone within a few millimetres of opening."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from behaviors import gasket  # noqa: E402


def test_shut_the_gasket_holds_with_the_opening_force_at_the_handle():
    assert abs(gasket(0.0, 35.0, 0.575, 0.61) - 35.0 * 0.575) < 1e-12
    assert gasket(-0.01, 35.0, 0.575, 0.61) == gasket(0.0, 35.0, 0.575, 0.61)      # pressed into its stop: no more


def test_it_lets_go_within_a_few_millimetres_of_the_latch_edge():
    one_mm = 0.001 / 0.61
    assert abs(gasket(one_mm * 3, 35.0, 0.575, 0.61, 0.003) / gasket(0.0, 35.0, 0.575, 0.61, 0.003) - math.exp(-1)) < 1e-9
    assert gasket(math.radians(5), 35.0, 0.575, 0.61, 0.003) < 1e-6 * 35.0               # 5 deg: 53 mm open, nothing left
