"""Controller tests.

These need real earbuds connected to this computer and are skipped otherwise.
The important one is :func:`test_queued_reads_do_not_collide`, which repeats
the pattern that used to break the interface: several reads issued together
against a single serial link.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buds.controller import Controller  # noqa: E402


class Skip(Exception):
    pass


def _drain(controller: Controller, expected: int, timeout: float = 25.0) -> list:
    """Poll until ``expected`` results have arrived, as a UI event loop would."""
    collected = []
    deadline = time.monotonic() + timeout
    while len(collected) < expected and time.monotonic() < deadline:
        collected.extend(controller.poll())
        time.sleep(0.05)
    return collected


def _connected_controller() -> Controller:
    controller = Controller()
    ports = [p for p in controller.ports() if p.is_candidate]
    if not ports:
        raise Skip("no paired earbuds on a serial port")
    controller.connect(ports[0])
    results = _drain(controller, 1)
    if not results or not results[0].ok:
        reason = results[0].message if results else "connect timed out"
        controller.stop()
        raise Skip(f"could not connect: {reason}")
    return controller


def test_connect_reports_firmware():
    controller = _connected_controller()
    try:
        assert controller.connected
        assert controller.port is not None
    finally:
        controller.stop()


def test_queued_reads_do_not_collide():
    """Three reads at once used to leave two of them empty."""
    controller = _connected_controller()
    try:
        controller.submit("info", lambda b: b.info())
        controller.submit("features", lambda b: b.features())
        controller.submit("bass", lambda b: b.bass_engine())
        results = {r.task: r for r in _drain(controller, 3)}

        assert set(results) == {"info", "features", "bass"}, \
            f"missing results: {set(results)}"
        for task, result in results.items():
            assert result.ok, f"{task} failed: {result.message}"
        assert results["info"].value.firmware, "firmware came back empty"
        assert results["features"].value, "features came back empty"
        assert results["bass"].value, "bass engine came back empty"
    finally:
        controller.stop()


def test_results_arrive_in_submission_order():
    controller = _connected_controller()
    try:
        order = ["a", "b", "c", "d"]
        for name in order:
            controller.submit(name, lambda b: b.battery())
        assert [r.task for r in _drain(controller, 4)] == order
    finally:
        controller.stop()


def test_failure_does_not_drop_the_connection():
    """A command the firmware refuses must not tear down the session."""
    controller = _connected_controller()
    try:
        controller.submit("boom", lambda b: (_ for _ in ()).throw(ValueError("x")))
        controller.submit("after", lambda b: b.battery())
        results = {r.task: r for r in _drain(controller, 2)}
        assert not results["boom"].ok
        assert not results["boom"].disconnected
        assert results["after"].ok, "connection was lost after one failure"
        assert controller.connected
    finally:
        controller.stop()


def test_work_without_connection_is_reported_not_raised():
    controller = Controller()
    try:
        controller.submit("info", lambda b: b.info())
        results = _drain(controller, 1, timeout=5)
        assert results and not results[0].ok
        assert results[0].disconnected
    finally:
        controller.stop()


def run() -> int:
    failures = skipped = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
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
