#!/usr/bin/env python3
"""Taking obstacle boxes out of a HomeHero Checker and putting them back: a
cloth's cells are obstacles while the hand reaches for it and none once it
is in the hand (the pinched corner rises with the palm; kept as static
cells they stopped every 20 cm lift at the thumb, 2026-10-15)."""
from __future__ import annotations


def _rebuild(checker):
    import numpy as np
    checker.box_lows = np.array([b[3] for b in checker.boxes]).reshape(-1, 3)
    checker.box_highs = np.array([b[4] for b in checker.boxes]).reshape(-1, 3)


def drop_boxes(checker, prefix: str) -> int:
    """Remove the boxes whose id starts with prefix (kept on the checker for
    restore_boxes); returns how many."""
    gone = [b for b in checker.boxes if str(b[0]).startswith(prefix)]
    if not gone:
        return 0
    checker.boxes = [b for b in checker.boxes if not str(b[0]).startswith(prefix)]
    checker._dropped = getattr(checker, "_dropped", []) + gone
    _rebuild(checker)
    return len(gone)


def restore_boxes(checker) -> int:
    """Put back everything drop_boxes took; returns how many."""
    gone = getattr(checker, "_dropped", [])
    if not gone:
        return 0
    checker.boxes = checker.boxes + gone
    checker._dropped = []
    _rebuild(checker)
    return len(gone)
