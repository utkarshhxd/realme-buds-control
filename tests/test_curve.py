"""Response curve maths."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buds import curve  # noqa: E402


def test_axis_is_logarithmic_and_bounded():
    width = 600
    assert curve.x_for(curve.F_MIN, width) == 0
    assert round(curve.x_for(curve.F_MAX, width)) == width
    # a decade should occupy a constant share of the axis
    one = curve.x_for(200, width) - curve.x_for(20, width)
    two = curve.x_for(2000, width) - curve.x_for(200, width)
    assert abs(one - two) < 1.0


def test_axis_round_trip():
    for freq in (50, 440, 1000, 7500, 16000):
        x = curve.x_for(freq, 800, pad=10)
        assert abs(curve.freq_for(x, 800, pad=10) - freq) < freq * 0.02


def test_out_of_range_frequencies_are_clamped():
    assert curve.x_for(1, 600) == curve.x_for(curve.F_MIN, 600)
    assert curve.x_for(999999, 600) == curve.x_for(curve.F_MAX, 600)


def test_response_peaks_at_the_band_centre():
    bands = [(1000, 6)]
    assert curve.response(bands, 1000) > curve.response(bands, 4000)
    assert curve.response(bands, 1000) > curve.response(bands, 250)
    assert abs(curve.response(bands, 1000) - 6) < 0.01


def test_flat_bands_give_a_flat_response():
    bands = [(62, 0), (250, 0), (1000, 0)]
    assert all(abs(curve.response(bands, f)) < 1e-9
               for f in (30, 100, 1000, 10000))


def test_neighbouring_bands_sum():
    both = curve.response([(1000, 3), (1100, 3)], 1050)
    assert both > 3, "overlapping bands should reinforce each other"


def test_curve_points_stay_inside_the_canvas():
    bands = [(55, 6), (120, 6), (320, -6), (1300, 6), (3200, 6), (7500, -6)]
    points = curve.curve_points(bands, 600, 180, pad=10)
    assert len(points) > 100
    assert all(0 <= x <= 600 for x, _ in points)
    assert all(1 <= y <= 179 for _, y in points)


def run() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  pass  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}  {exc}")
    print("all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
