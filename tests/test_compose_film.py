"""compose_film: a film from every Nth tick's frames plays in real time."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from compose_film import compose, frame_ticks  # noqa: E402


def _frames(out: Path, ticks, size=(64, 36)):
    from PIL import Image
    for k in ("close", "head", "overview"):
        (out / f"frames_{k}").mkdir(parents=True)
        for i in ticks:
            Image.new("RGB", size, (i % 255, 0, 0)).save(out / f"frames_{k}" / f"f{i:05d}.png")


def test_frame_ticks_read_from_the_names(tmp_path):
    _frames(tmp_path, [0, 2, 4, 6])
    assert frame_ticks(tmp_path / "frames_close") == [0, 2, 4, 6]


def test_every_second_tick_plays_at_half_the_control_rate(tmp_path):
    _frames(tmp_path, range(0, 20, 2), size=(1280, 720))
    log = [{"t": i / 30, "stage": "pull", "tool": {"door": {"deg": 20.0 + i}}} for i in range(1, 20)]
    r = compose(tmp_path, log, "microwave", 30.0)
    assert r["every"] == 2 and r["fps"] == 15.0 and r["frames"] == 10
    assert (tmp_path / "contact_sheet.png").exists()
    assert not (tmp_path / "frames_close").exists()
