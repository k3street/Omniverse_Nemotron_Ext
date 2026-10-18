"""A reel cuts each take by its own log's stages: a stage's runs in order, "name#n" the n-th run, a "to" stage its
first run at or after the "from" run, and film time the time the log records (compose_reel.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from compose_reel import resolve, stage_runs  # noqa: E402

LOG = [{"stage": s, "t": i / 20.0} for i, s in enumerate(
    ["reach"] * 3 + ["close"] * 2 + ["turn"] * 4 + ["release"] * 2 + ["close"] * 2 + ["turn"] * 3 + ["lift"] * 2)]


def test_a_take_is_cut_by_its_stages_runs_and_the_time_its_log_records():
    runs = stage_runs(LOG)
    assert [r[0] for r in runs] == ["reach", "close", "turn", "release", "close", "turn", "lift"]
    # from the first close to the first turn after it, in the log's time (a 20 Hz take: index / 20)
    assert resolve({"from": "close", "to": "turn"}, LOG, runs) == (3 / 20, 8 / 20)
    # the second close to the lift
    assert resolve({"from": "close#2", "to": "lift"}, LOG, runs) == (11 / 20, 17 / 20)
    # a "to" stage is its first run at or after the "from" run: the second turn, after the second close
    assert resolve({"from": "close#2", "to": "turn"}, LOG, runs) == (11 / 20, 15 / 20)
    assert resolve({"from_tick": 1, "to_tick": 4}, LOG, runs) == (1 / 20, 4 / 20)


def test_a_stage_the_take_does_not_have_is_refused():
    runs = stage_runs(LOG)
    with pytest.raises(SystemExit):
        resolve({"from": "carry", "to": "lift"}, LOG, runs)
    with pytest.raises(SystemExit):
        resolve({"from": "close#3", "to": "lift"}, LOG, runs)
