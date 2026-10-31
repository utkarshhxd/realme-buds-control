"""HTTP layer tests.

Starts the real server on a spare port and talks to it over HTTP.  Routing,
validation and static files are checked without earbuds; the rest skips when
none are connected.
"""

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buds.server import build  # noqa: E402


class Skip(Exception):
    pass


_server = None
_api = None
_base = ""


def setup() -> None:
    global _server, _api, _base
    if _server:
        return
    _server, _api = build(0)                       # 0 = any free port
    _base = f"http://127.0.0.1:{_server.server_address[1]}"
    threading.Thread(target=_server.serve_forever, daemon=True).start()


def teardown() -> None:
    if _server:
        _server.shutdown()
    if _api:
        _api.shutdown()


def get(path: str):
    with urllib.request.urlopen(_base + path, timeout=30) as response:
        return json.loads(response.read())


def post(path: str, payload: dict | None = None):
    data = json.dumps(payload or {}).encode()
    request = urllib.request.Request(
        _base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read())


def raw(path: str):
    with urllib.request.urlopen(_base + path, timeout=15) as response:
        return response.status, response.headers.get("Content-Type"), response.read()


def connected() -> bool:
    if get("/api/state")["connected"]:
        return True
    return bool(post("/api/connect").get("ok"))


# ---------------------------------------------------------------- offline --
def test_page_and_assets_are_served():
    status, kind, body = raw("/")
    assert status == 200 and "html" in kind
    assert b"realme Buds Control" in body
    for asset, expect in (("/app.css", "css"), ("/app.js", "javascript")):
        status, kind, body = raw(asset)
        assert status == 200, f"{asset} returned {status}"
        assert expect in kind, f"{asset} served as {kind}"
        assert body, f"{asset} was empty"


def test_unknown_paths_404():
    for path in ("/api/nope", "/nothing.txt"):
        try:
            raw(path)
            raise AssertionError(f"{path} should have been 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404


def test_directory_traversal_is_refused():
    try:
        raw("/../buds/protocol.py")
        raise AssertionError("traversal was served")
    except urllib.error.HTTPError as exc:
        assert exc.code in (400, 404)


def test_state_shape_without_earbuds():
    state = get("/api/state")
    for key in ("connected", "ports", "presets", "limits", "featureNames"):
        assert key in state, f"missing {key}"
    assert state["limits"]["gainMin"] == -6
    assert state["limits"]["gainMax"] == 6
    assert any(p["key"] == "classic" for p in state["presets"])
    for preset in state["presets"]:
        assert len(preset["bands"]) == 6
        assert len(preset["bass"]) == 3


def test_eq_validation_rejects_bad_input():
    cases = [
        ([[55, 1], [120, 99]], "dB"),           # gain beyond the firmware limit
        ([[5, 1]], "Hz"),                        # below the audible range
        ([[90000, 1]], "Hz"),                    # above it
        ([["x", 1]], "Invalid"),                 # not a number
        ([], "No bands"),                        # nothing at all
    ]
    for bands, expected in cases:
        result = post("/api/eq", {"bands": bands, "name": "T"})
        assert not result["ok"], f"{bands} was accepted"
        assert expected.lower() in result["error"].lower(), \
            f"{bands} gave an unhelpful message: {result['error']}"


def test_bass_validation_rejects_out_of_range():
    result = post("/api/bass", {"values": [9, 0, 0], "enabled": True})
    assert not result["ok"] and "between" in result["error"]


def test_bad_json_does_not_crash_the_server():
    request = urllib.request.Request(
        _base + "/api/eq", data=b"{not json",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        assert response.status == 200
    assert get("/api/state")["connected"] in (True, False)   # still alive


# --------------------------------------------------------------- hardware --
def test_connect_reports_the_device():
    if not connected():
        raise Skip("no earbuds connected")
    state = get("/api/state")
    assert state["connected"]
    assert state["portName"], "portName missing, the header would say 'Earbuds'"
    device = state["device"]
    assert device and device["firmware"], "firmware not reported"
    assert device["battery"], "battery not reported"
    assert device["features"], "no switch features reported"


def test_eq_round_trips_through_http():
    if not connected():
        raise Skip("no earbuds connected")
    bands = [[55, 1], [120, 3], [320, -2], [1300, 3], [3200, 1], [7500, -2]]
    result = post("/api/eq", {"bands": bands, "name": "Classic"})
    assert result["ok"], result.get("error")
    assert result["name"] == "Classic", "the written name was not kept"
    back = get("/api/state")["device"]["eq"]
    assert back["bands"] == bands, f"read back {back['bands']}"
    assert back["name"] == "Classic"


def test_custom_frequencies_are_kept():
    """The whole point: centres the official app cannot reach."""
    if not connected():
        raise Skip("no earbuds connected")
    bands = [[45, 2], [190, -4], [640, 1], [2100, 3], [5200, 2], [13500, 1]]
    assert post("/api/eq", {"bands": bands, "name": "Odd"})["ok"]
    assert get("/api/state")["device"]["eq"]["bands"] == bands
    # put the usual curve back
    post("/api/eq", {"bands": [[55, 1], [120, 3], [320, -2],
                               [1300, 3], [3200, 1], [7500, -2]],
                     "name": "Classic"})


def test_bass_round_trips_through_http():
    if not connected():
        raise Skip("no earbuds connected")
    assert post("/api/bass", {"values": [3, 2, 1], "enabled": True})["ok"]
    values = {b["band"]: b["value"] for b in get("/api/state")["device"]["bass"]}
    assert [values.get(1), values.get(2), values.get(3)] == [3, 2, 1]
    post("/api/bass", {"values": [2, 1, 0], "enabled": True})


def test_unsupported_features_are_absent_from_state():
    """What the interface relies on to disable a switch.

    Setting an unreported feature is not a useful check: 0x0403 is allowed by
    the capability bitmap, so the firmware acknowledges the write and then
    ignores it.  Whether a feature exists is decided by whether it comes back
    in the 0x010D report, and that is what the interface greys out on.
    """
    if not connected():
        raise Skip("no earbuds connected")
    features = get("/api/state")["device"]["features"]
    assert features, "nothing reported at all"
    # ids are returned as strings and only for features the firmware answered
    assert all(key.isdigit() for key in features)
    assert "24" in features, "Hi-Res should be reported on a Buds Air8"
    from buds.protocol import PROBE_FEATURES
    extra = set(features) - {str(f) for f in PROBE_FEATURES}
    assert not extra, f"state returned unprobed features: {sorted(extra)}"


def run() -> int:
    setup()
    failures = skipped = 0
    try:
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
    finally:
        teardown()
    summary = "all passed" if not failures else f"{failures} failed"
    if skipped:
        summary += f", {skipped} skipped"
    print(summary)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
