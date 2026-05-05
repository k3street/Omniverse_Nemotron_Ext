"""Reusable animation primitives for omni.ui.

omni.ui has no built-in transition system, so animations are coroutines
that mutate widget style/properties at the asyncio loop's tick rate
(~60 Hz on Kit's main loop). Each helper takes a setter callable and
interpolates a value over a duration with cubic ease-out.

Color format throughout this module is omni.ui ABGR: ``0xAABBGGRR``.
For example NVIDIA green ``#76B900`` (RGB) is ``0xFF00B976`` (ABGR).
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

# 60 Hz target. Lowered automatically when Kit is under heavy load.
_FRAME_S = 1.0 / 60.0


def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _lerp_int(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _lerp_color_abgr(c0: int, c1: int, t: float) -> int:
    a0, b0, g0, r0 = (c0 >> 24) & 0xFF, (c0 >> 16) & 0xFF, (c0 >> 8) & 0xFF, c0 & 0xFF
    a1, b1, g1, r1 = (c1 >> 24) & 0xFF, (c1 >> 16) & 0xFF, (c1 >> 8) & 0xFF, c1 & 0xFF
    a = _lerp_int(a0, a1, t)
    b = _lerp_int(b0, b1, t)
    g = _lerp_int(g0, g1, t)
    r = _lerp_int(r0, r1, t)
    return (a << 24) | (b << 16) | (g << 8) | r


async def lerp_color(set_fn: Callable[[int], None], c0: int, c1: int, ms: int = 300) -> None:
    """Interpolate ABGR color from c0 to c1 over ``ms`` with ease-out cubic.

    ``set_fn(color)`` is called with the current color each frame, plus
    once at the end with c1 to guarantee the final value is exact.
    """
    t0 = time.monotonic()
    duration_s = max(1, ms) / 1000.0
    while True:
        elapsed = time.monotonic() - t0
        if elapsed >= duration_s:
            try:
                set_fn(c1)
            except Exception:
                pass
            return
        t = _ease_out_cubic(elapsed / duration_s)
        try:
            set_fn(_lerp_color_abgr(c0, c1, t))
        except Exception:
            return  # widget got destroyed mid-animation; abort
        await asyncio.sleep(_FRAME_S)


async def fade_in_widget(widget, color_key: str, target_color: int, ms: int = 150) -> None:
    """Fade widget's ``style[color_key]`` from alpha=0 to ``target_color``."""
    base = target_color & 0x00FFFFFF  # alpha = 0

    def _set(c):
        s = dict(widget.style or {})
        s[color_key] = c
        widget.style = s

    await lerp_color(_set, base, target_color, ms)


async def pulse_widget(
    widget,
    color_key: str,
    base_color: int,
    peak_color: int,
    up_ms: int = 250,
    down_ms: int = 600,
) -> None:
    """One-shot pulse: base → peak → base.

    Used for the assistant-bubble "turn complete" border pulse.
    """
    def _set(c):
        s = dict(widget.style or {})
        s[color_key] = c
        widget.style = s

    await lerp_color(_set, base_color, peak_color, up_ms)
    await lerp_color(_set, peak_color, base_color, down_ms)
