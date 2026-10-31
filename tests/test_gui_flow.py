"""End-to-end interface test.

Builds the real window and drives it the way a person would - refresh, connect,
load presets, apply, toggle - pumping the event loop between steps so queued
work actually completes.  Needs earbuds connected to this computer and is
skipped otherwise.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class Skip(Exception):
    pass


def pump(app, seconds: float) -> None:
    """Run the event loop for a while without blocking in mainloop()."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.02)


def wait_until(app, predicate, timeout: float = 25.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if predicate():
            return True
        time.sleep(0.03)
    return False


def build():
    import gui
    app = gui.App()
    app.update()
    return app


def connected_app():
    app = build()
    app.refresh_ports()
    app.update()
    if not any(p.is_candidate for p in app.ports):
        app.destroy()
        raise Skip("no paired earbuds on a serial port")
    app.toggle_connection()
    if not wait_until(app, lambda: app.controller.connected):
        text = app.status.cget("text")
        app.controller.stop()
        app.destroy()
        raise Skip(f"could not connect: {text}")
    # let the four queued startup reads finish
    wait_until(app, lambda: not app.controller.busy, timeout=30)
    return app


def test_window_builds_and_starts_disabled():
    app = build()
    try:
        assert app.connect_btn.cget("text") == "Connect"
        assert str(app.apply_btn.cget("state")) == "disabled", \
            "controls must be disabled before connecting"
        assert len(app.rows) == 6
    finally:
        app.controller.stop()
        app.destroy()


def test_presets_populate_bands_and_curve():
    app = build()
    try:
        from buds import PRESETS
        for key, preset in PRESETS.items():
            app.load_preset(key)
            app.update()
            assert app.current_bands() == list(preset.bands), \
                f"preset {key} did not load into the rows"
        app.load_preset("classic")
        app.update()
        assert app.current_bands()[1] == (120, 3)
    finally:
        app.controller.stop()
        app.destroy()


def test_invalid_frequency_is_rejected_not_crashed():
    app = build()
    try:
        app.rows[0].freq_var.set("abc")
        assert app.rows[0].band() is None
        app.rows[0].freq_var.set("99999")
        assert app.rows[0].band() is None, "out of range value accepted"
        app.rows[0].freq_var.set("55")
        assert app.rows[0].band() == (55, app.rows[0].gain)
        app.update()  # redraw with a bad row present must not raise
    finally:
        app.controller.stop()
        app.destroy()


def test_connect_enables_controls_and_shows_device():
    app = connected_app()
    try:
        assert str(app.apply_btn.cget("state")) == "normal"
        assert app.connect_btn.cget("text") == "Disconnect"
        assert "Not connected" not in app.device_label.cget("text")
        assert "%" in app.battery_label.cget("text"), "battery never populated"
    finally:
        app.controller.stop()
        app.destroy()


def test_connect_enables_supported_feature_switches():
    """The original bug: every switch stayed greyed out after connecting."""
    app = connected_app()
    try:
        enabled = [fid for fid, sw in app._feature_boxes.items()
                   if str(sw.cget("state")) == "normal"]
        assert enabled, "no feature switches were enabled after connecting"
        assert 24 in enabled, "Hi-Res switch should be supported on this firmware"
        # unimplemented ones must stay disabled rather than silently lying
        assert str(app._feature_boxes[54].cget("state")) == "disabled", \
            "virtual bass is not implemented and must stay disabled"
    finally:
        app.controller.stop()
        app.destroy()


def test_device_info_is_populated():
    app = connected_app()
    try:
        text = app.info_box.get("1.0", "end")
        assert "Firmware" in text and "Region code" in text
        assert "unknown" not in text.split("Region code")[1][:20]
    finally:
        app.controller.stop()
        app.destroy()


def test_apply_eq_round_trips():
    app = connected_app()
    try:
        app.load_preset("acoustic")
        app.update()
        app.apply_eq()
        assert wait_until(app, lambda: not app.controller.busy, 20)
        assert "rejected" not in app.status.cget("text").lower()

        # wipe the rows, then read back from the earbuds
        for row in app.rows:
            row.set_band(1000, 0)
        app.update()
        app.read_eq()
        assert wait_until(app, lambda: not app.controller.busy, 20)
        from buds import PRESETS
        assert app.current_bands() == list(PRESETS["acoustic"].bands), \
            "what came back differs from what was written"
    finally:
        app.load_preset("classic")
        app.apply_eq()
        wait_until(app, lambda: not app.controller.busy, 20)
        app.controller.stop()
        app.destroy()


def test_bass_round_trips():
    app = connected_app()
    try:
        for slider, value in zip(app.bass_sliders, (3, 2, 1)):
            slider.set(value)
        app.update()
        app.apply_bass()
        assert wait_until(app, lambda: not app.controller.busy, 20)

        for slider in app.bass_sliders:
            slider.set(0)
        app.update()
        app.read_bass()
        assert wait_until(app, lambda: not app.controller.busy, 20)
        got = [int(round(s.get())) for s in app.bass_sliders]
        assert got == [3, 2, 1], f"bass read back as {got}"
    finally:
        for slider, value in zip(app.bass_sliders, (2, 1, 0)):
            slider.set(value)
        app.apply_bass()
        wait_until(app, lambda: not app.controller.busy, 20)
        app.controller.stop()
        app.destroy()


def test_disconnect_disables_everything_again():
    app = connected_app()
    try:
        app.toggle_connection()
        assert wait_until(app, lambda: not app.controller.connected, 15)
        pump(app, 0.4)
        assert str(app.apply_btn.cget("state")) == "disabled"
        assert app.connect_btn.cget("text") == "Connect"
        for switch in app._feature_boxes.values():
            assert str(switch.cget("state")) == "disabled"
    finally:
        app.controller.stop()
        app.destroy()


def run() -> int:
    failures = skipped = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"  pass  {name}")
        except Skip as exc:
            skipped += 1
            print(f"  skip  {name}  ({exc})")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {name}  {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {name}  {type(exc).__name__}: {exc}")
    summary = "all passed" if not failures else f"{failures} failed"
    if skipped:
        summary += f", {skipped} skipped (earbuds not connected)"
    print(summary)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
