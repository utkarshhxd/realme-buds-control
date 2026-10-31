"""Local HTTP front end.

Serves the web interface and a small JSON API over :class:`Controller`.  Every
device call goes through the controller's single worker thread, so concurrent
browser requests still reach the earbuds one at a time.

Binds to the loopback address only.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import protocol as p
from .controller import Controller
from .presets import DEFAULT_EQ_SLOT, PRESETS

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
HOST = "127.0.0.1"


class Api:
    """The operations the browser can ask for."""

    def __init__(self) -> None:
        self.controller = Controller()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ helpers --
    def _ports(self) -> list[dict]:
        return [
            {"device": port.device, "name": port.name, "label": port.label,
             "candidate": port.is_candidate}
            for port in self.controller.ports()
        ]

    @staticmethod
    def _presets() -> list[dict]:
        return [
            {"key": preset.key, "name": preset.name,
             "description": preset.description,
             "bands": [list(b) for b in preset.bands],
             "bass": list(preset.bass)}
            for preset in PRESETS.values()
        ]

    # --------------------------------------------------------------- api --
    def state(self) -> dict:
        port = self.controller.port
        base = {
            "connected": self.controller.connected,
            # Reported here too, so a page that did not perform the connect
            # itself still knows which earbuds it is talking to.
            "portName": (port.name or port.device) if port else None,
            "portDevice": port.device if port else None,
            "ports": self._ports(),
            "presets": self._presets(),
            "limits": {"gainMin": p.EQ_GAIN_MIN, "gainMax": p.EQ_GAIN_MAX,
                       "bassMin": p.BASS_GAIN_MIN, "bassMax": p.BASS_GAIN_MAX,
                       "bands": p.EQ_BANDS},
            "featureNames": {str(k): v for k, v in p.FEATURES.items()},
            "device": None,
        }
        if not self.controller.connected:
            return base

        result = self.controller.execute("state", self._read_everything)
        if not result.ok:
            base["connected"] = self.controller.connected
            base["error"] = str(result.error)
            return base
        base["device"] = result.value
        base["connected"] = self.controller.connected
        return base

    @staticmethod
    def _read_everything(buds) -> dict:
        info = buds.info()
        profiles = buds.eq_profiles()
        active = next((x for x in profiles if x.active), None)
        bass = buds.bass_engine()
        return {
            "firmware": info.firmware,
            "region": info.region,
            "battery": {str(k): v for k, v in (info.battery or {}).items()},
            "features": {str(k): v for k, v in (info.features or {}).items()},
            "activePreset": info.active_preset,
            "eq": {
                "name": active.name if active else None,
                "bands": [list(b) for b in active.bands] if active else None,
            },
            "bass": [{"band": b, "value": v, "min": lo, "max": hi}
                     for b, v, lo, hi in bass],
        }

    def connect(self, device: str | None) -> dict:
        ports = self.controller.ports()
        port = next((x for x in ports if x.device == device), None)
        if port is None:
            port = next((x for x in ports if x.is_candidate), None)
        if port is None:
            return {"ok": False,
                    "error": "No earbuds found. Connect them to this computer, "
                             "then press Refresh."}
        result = self.controller.connect_sync(port)
        if not result.ok:
            return {"ok": False, "error": str(result.error)}
        return {"ok": True, "firmware": result.value,
                "name": port.name or port.device, "device": port.device}

    def disconnect(self) -> dict:
        self.controller.disconnect_sync()
        return {"ok": True}

    def set_eq(self, bands: list, name: str) -> dict:
        clean: list[tuple[int, int]] = []
        for entry in bands:
            try:
                hz, gain = int(entry[0]), int(entry[1])
            except (TypeError, ValueError, IndexError):
                return {"ok": False, "error": f"Invalid band: {entry!r}"}
            if not 20 <= hz <= 20000:
                return {"ok": False,
                        "error": f"{hz} Hz is outside the 20-20000 Hz range."}
            if not p.EQ_GAIN_MIN <= gain <= p.EQ_GAIN_MAX:
                return {"ok": False,
                        "error": f"{gain:+d} dB is outside the supported "
                                 f"{p.EQ_GAIN_MIN:+d} to {p.EQ_GAIN_MAX:+d} range."}
            clean.append((hz, gain))
        if not clean:
            return {"ok": False, "error": "No bands given."}

        profile = p.EqProfile(eq_id=DEFAULT_EQ_SLOT, name=(name or "Custom")[:12],
                              bands=clean)
        result = self.controller.execute("write_eq", lambda b: b.write_eq(profile))
        if not result.ok:
            return {"ok": False, "error": str(result.error)}
        if not result.value:
            return {"ok": False, "error": "The earbuds rejected that curve."}
        return {"ok": True, "name": profile.name}

    def set_bass(self, values: list, enabled: bool) -> dict:
        try:
            clean = [int(v) for v in values]
        except (TypeError, ValueError):
            return {"ok": False, "error": "Bass values must be whole numbers."}
        if any(not p.BASS_GAIN_MIN <= v <= p.BASS_GAIN_MAX for v in clean):
            return {"ok": False,
                    "error": f"Bass values must be between {p.BASS_GAIN_MIN} "
                             f"and {p.BASS_GAIN_MAX}."}
        result = self.controller.execute(
            "write_bass", lambda b: b.set_bass_engine(clean, enabled))
        if not result.ok:
            return {"ok": False, "error": str(result.error)}
        if not result.value:
            return {"ok": False, "error": "The earbuds rejected those settings."}
        return {"ok": True}

    def set_feature(self, feature_id: int, on: bool) -> dict:
        result = self.controller.execute(
            "set_feature", lambda b: b.set_feature(int(feature_id), bool(on)))
        if not result.ok:
            return {"ok": False, "error": str(result.error)}
        if not result.value:
            name = p.FEATURES.get(int(feature_id), feature_id)
            return {"ok": False,
                    "error": f"The earbuds refused to change {name}. "
                             "This firmware may not implement it."}
        return {"ok": True}

    def shutdown(self) -> None:
        self.controller.stop()


class Handler(BaseHTTPRequestHandler):
    api: Api  # set on the server class

    server_version = "buds"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # keep the console quiet
        pass

    # ------------------------------------------------------------ plumbing --
    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _serve_file(self, rel: str) -> None:
        target = (WEB_ROOT / rel).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------- routes --
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/state":
            self._send(self.api.state())
        elif path in ("/", "/index.html"):
            self._serve_file("index.html")
        elif path.startswith("/api/"):
            self.send_error(404)
        else:
            self._serve_file(path.lstrip("/"))

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        body = self._body()
        try:
            if path == "/api/connect":
                self._send(self.api.connect(body.get("device")))
            elif path == "/api/disconnect":
                self._send(self.api.disconnect())
            elif path == "/api/eq":
                self._send(self.api.set_eq(body.get("bands", []),
                                           body.get("name", "Custom")))
            elif path == "/api/bass":
                self._send(self.api.set_bass(body.get("values", []),
                                             bool(body.get("enabled", True))))
            elif path == "/api/feature":
                self._send(self.api.set_feature(body.get("id"),
                                                bool(body.get("on"))))
            else:
                self.send_error(404)
        except Exception as exc:  # never let one request kill the server
            self._send({"ok": False, "error": str(exc)}, status=500)


def build(port: int = 8777) -> tuple[ThreadingHTTPServer, Api]:
    api = Api()
    handler = type("BoundHandler", (Handler,), {"api": api})
    httpd = ThreadingHTTPServer((HOST, port), handler)
    return httpd, api
