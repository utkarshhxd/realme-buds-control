"""Frequency response maths for the equaliser display.

Each band is drawn as a peaking filter: a bell centred on the band's frequency
whose width is constant in octaves, which is how a parametric EQ behaves and
how the curve reads to the eye.  This is for display only - the earbuds apply
their own filters and this does not try to model them exactly.
"""

from __future__ import annotations

import math

F_MIN = 20.0
F_MAX = 20000.0
DEFAULT_WIDTH_OCTAVES = 1.1


def x_for(freq: float, width: int, pad: int = 0) -> float:
    """Pixel x for a frequency on a logarithmic axis."""
    freq = max(F_MIN, min(F_MAX, freq))
    span = math.log10(F_MAX / F_MIN)
    return pad + (math.log10(freq / F_MIN) / span) * (width - 2 * pad)


def freq_for(x: float, width: int, pad: int = 0) -> float:
    """Inverse of :func:`x_for`, for click and drag."""
    usable = max(1, width - 2 * pad)
    ratio = min(1.0, max(0.0, (x - pad) / usable))
    return F_MIN * (10 ** (ratio * math.log10(F_MAX / F_MIN)))


def response(bands: list[tuple[int, int]], freq: float,
             width_octaves: float = DEFAULT_WIDTH_OCTAVES) -> float:
    """Combined gain in dB at one frequency."""
    total = 0.0
    for centre, gain in bands:
        if not gain or centre <= 0:
            continue
        octaves = math.log2(freq / centre)
        total += gain * math.exp(-0.5 * (octaves / width_octaves) ** 2)
    return total


def curve_points(bands: list[tuple[int, int]], width: int, height: int,
                 gain_limit: float = 8.0, pad: int = 0,
                 steps: int = 220) -> list[tuple[float, float]]:
    """Sample the response into canvas coordinates."""
    points = []
    span = math.log10(F_MAX / F_MIN)
    usable = max(1, width - 2 * pad)
    for i in range(steps + 1):
        ratio = i / steps
        freq = F_MIN * (10 ** (ratio * span))
        db = response(bands, freq)
        x = pad + ratio * usable
        y = height / 2 - (db / gain_limit) * (height / 2)
        points.append((x, max(1.0, min(height - 1.0, y))))
    return points
