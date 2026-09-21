"""L0 tests for merging LeRobot v3.0 datasets into one multi-task root.

Merging is reindexing, and both of these go wrong silently rather than loudly:
a duplicated instruction that gets two indices makes frames cite a task that
describes a different dataset, and a gap in the frame spans hands an episode
somebody else's frames. Neither raises; both corrupt training data.
"""
import pytest

pytestmark = pytest.mark.l0

from scripts.merge_lerobot_v3_datasets import assign_task_indices, episode_spans

BANANA = "Pick up the banana and put it on the plate"
BLOCK = "Pick up the block and put it in the bin"


def test_instructions_are_numbered_by_first_appearance():
    indices = assign_task_indices([[BANANA], [BANANA], [BLOCK], [BANANA]])
    assert indices == {BANANA: 0, BLOCK: 1}


def test_a_shared_instruction_keeps_one_index_across_datasets():
    # Two sources describing the same task must not end up as two tasks.
    indices = assign_task_indices([[BLOCK], [BANANA], [BLOCK]])
    assert len(indices) == 2
    assert indices[BLOCK] == 0


def test_spans_are_contiguous_and_half_open():
    spans = episode_spans([186, 148, 200])
    assert spans == [(0, 186), (186, 334), (334, 534)]
    # No gaps and no overlaps: each episode starts where the last one ended.
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end == start
    assert spans[-1][1] == sum([186, 148, 200])


def test_spans_of_nothing_are_empty():
    assert episode_spans([]) == []
