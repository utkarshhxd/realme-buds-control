"""Tuning presets.

realme's own app locks the equaliser to six fixed centre frequencies
(62/250/1k/4k/8k/16k Hz).  The firmware does not: it stores whatever centres
it is given.  That leaves two awkward gaps in the stock layout - nothing
between 250 Hz and 1 kHz, where boxiness lives, and nothing between 1 kHz and
4 kHz, where vocal presence lives - and these presets place bands there.

Each entry is (centre Hz, gain dB), gains limited to +/-6 by the firmware.
``bass`` is the three-band dynamic bass engine, a separate processor with its
own +/-5 range.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import EqProfile


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    description: str
    bands: tuple[tuple[int, int], ...]
    bass: tuple[int, int, int] = (0, 0, 0)

    def to_profile(self, eq_id: int) -> EqProfile:
        return EqProfile(eq_id=eq_id, name=self.name, bands=list(self.bands))


PRESETS: dict[str, Preset] = {
    "classic": Preset(
        "classic", "Classic",
        "80s and 90s: warmth from bass guitar rather than sub-bass, restored "
        "midrange, and the top end pulled down because masters of that era "
        "are already bright.",
        ((55, 1), (120, 3), (320, -2), (1300, 3), (3200, 1), (7500, -2)),
        bass=(2, 1, 0),
    ),
    "acoustic": Preset(
        "acoustic", "Acoustic",
        "Honest midrange for unplugged material, guitar body boom controlled, "
        "and air opened up for string shimmer and room sound.",
        ((55, 0), (110, 1), (280, -3), (1400, 3), (3500, 2), (11000, 3)),
        bass=(1, 0, 0),
    ),
    "clarity": Preset(
        "clarity", "Clarity",
        "General purpose: cuts the 200 Hz mud these earbuds ship with and "
        "lifts 2.5 kHz so vocals come forward.",
        ((60, 2), (200, -5), (500, -2), (2500, 3), (6000, 2), (12000, 3)),
        bass=(3, 1, 0),
    ),
    "vocal": Preset(
        "vocal", "Vocal",
        "Podcasts and vocal-led music: bass pulled right back, presence up.",
        ((60, -2), (200, -5), (700, 0), (2800, 4), (5000, 3), (10000, 1)),
        bass=(0, 0, 0),
    ),
    "bass": Preset(
        "bass", "Bass",
        "Electronic and hip-hop: deep sub lift, but 200 Hz still cut so the "
        "low end stays tight instead of turning boomy.",
        ((45, 5), (90, 4), (200, -4), (900, 0), (4000, 1), (11000, 2)),
        bass=(4, 3, 1),
    ),
    "flat": Preset(
        "flat", "Flat",
        "Neutral. Stock frequencies, no gain, bass engine off.",
        ((62, 0), (250, 0), (1000, 0), (4000, 0), (8000, 0), (16000, 0)),
        bass=(0, 0, 0),
    ),
}

DEFAULT_EQ_SLOT = 6
