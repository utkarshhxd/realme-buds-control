"""Connection handling and the high-level earbud API."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass

import serial
import serial.tools.list_ports

from . import protocol as p


class BudsError(Exception):
    pass


class NotConnected(BudsError):
    pass


@dataclass
class DeviceInfo:
    firmware: str = ""
    region: int | None = None
    battery: dict[int, int] | None = None
    features: dict[int, int] | None = None
    capabilities: set[int] | None = None
    active_preset: int | None = None


_MAC_RE = re.compile(r"&([0-9A-Fa-f]{12})_")

# Device names worth auto-selecting when several Bluetooth ports exist.
_KNOWN_NAMES = ("realme", "buds", "oppo", "oneplus")


def _bluetooth_names() -> dict[str, str]:
    """Map Bluetooth MAC -> friendly name, from the Windows registry.

    Returns an empty mapping anywhere else, which just means ports get labelled
    by their description instead of by device name.
    """
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except ImportError:
        return {}

    names: dict[str, str] = {}
    path = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as devices:
            for i in range(winreg.QueryInfoKey(devices)[0]):
                mac = winreg.EnumKey(devices, i)
                try:
                    with winreg.OpenKey(devices, mac) as entry:
                        raw, _ = winreg.QueryValueEx(entry, "Name")
                        if isinstance(raw, bytes):
                            raw = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
                        names[mac.lower()] = str(raw)
                except OSError:
                    continue
    except OSError:
        return {}
    return names


@dataclass
class Port:
    device: str              # e.g. COM6
    name: str                # the paired device's name, when we can find it
    mac: str = ""

    @property
    def label(self) -> str:
        return f"{self.device} - {self.name}" if self.name else self.device

    @property
    def is_candidate(self) -> bool:
        """True for a port that belongs to a real paired device.

        Windows also publishes local listening ports whose MAC is all zeros;
        writing to one of those just times out.
        """
        if not self.mac or set(self.mac) == {"0"}:
            return False
        return True


def find_ports() -> list[Port]:
    """List serial ports, most likely earbuds first.

    On Windows a paired earbud's SPP service shows up as a "Standard Serial
    over Bluetooth link" COM port.  The port only exists while the earbuds are
    actually connected to this machine, so an empty list usually means they
    have handed the link to a phone rather than that anything is broken.
    """
    names = _bluetooth_names()
    ports: list[Port] = []
    for entry in serial.tools.list_ports.comports():
        match = _MAC_RE.search(entry.hwid or "")
        mac = match.group(1).lower() if match else ""
        name = names.get(mac, "") or (entry.description or "")
        ports.append(Port(entry.device, name, mac))

    def rank(port: Port) -> tuple:
        known = any(word in port.name.lower() for word in _KNOWN_NAMES)
        return (not known, not port.is_candidate, port.device)

    ports.sort(key=rank)
    return ports


class Buds:
    """A connection to one pair of earbuds."""

    def __init__(self, port: str, timeout: float = 1.5):
        self.port_name = port
        self.timeout = timeout
        self._serial: serial.Serial | None = None
        self._seq = 0
        self._buffer = b""

    # ------------------------------------------------------------ plumbing --
    def connect(self) -> None:
        try:
            self._serial = serial.Serial(self.port_name, 115200,
                                         timeout=self.timeout, write_timeout=2)
        except serial.SerialException as exc:
            raise NotConnected(f"could not open {self.port_name}: {exc}") from exc

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def connected(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def __enter__(self) -> "Buds":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    def _drain(self) -> list[p.Packet]:
        if not self._serial:
            return []
        waiting = self._serial.in_waiting
        if waiting:
            self._buffer += self._serial.read(waiting)
        packets, self._buffer = p.parse_frames(self._buffer)
        return packets

    def request(self, cmd: int, data: bytes = b"",
                wait: float = 1.0) -> p.Packet | None:
        """Send a command and return its matching response, if one arrives.

        Several commands are deliberately fire-and-forget in the official app
        and never answer, so a ``None`` here is not necessarily a failure.
        """
        if not self.connected:
            raise NotConnected("earbuds are not connected")
        seq = self._next_seq()
        try:
            self._serial.write(p.build_frame(cmd, seq, data))
        except serial.SerialTimeoutException as exc:
            # The COM port exists but nothing is listening on the other end,
            # which on Windows usually means this port belongs to a different
            # device, or the earbuds have handed their link to a phone.
            raise NotConnected(
                f"{self.port_name} accepted no data - the earbuds are probably "
                "not connected to this computer right now"
            ) from exc

        deadline = time.monotonic() + wait
        unsolicited: list[p.Packet] = []
        while time.monotonic() < deadline:
            for packet in self._drain():
                if packet.is_response and packet.base_cmd == cmd:
                    return packet
                unsolicited.append(packet)
            time.sleep(0.05)
        return None

    # ----------------------------------------------------------- reads --
    def firmware(self) -> str:
        reply = self.request(p.CMD_FIRMWARE)
        if not reply or not reply.ok:
            return ""
        text = reply.body.decode("ascii", "replace").strip("\x00").strip()
        # The buds answer with a comma separated component table; the version
        # proper is the first dotted field in it.
        for part in text.split(","):
            if part.count(".") >= 2:
                return part.strip()
        return text

    def battery(self) -> dict[int, int]:
        reply = self.request(p.CMD_BATTERY)
        return p.parse_battery(reply.body) if reply and reply.ok else {}

    def region(self) -> int | None:
        reply = self.request(p.CMD_REGION)
        return reply.body[0] if reply and reply.ok and reply.body else None

    def capabilities(self) -> set[int]:
        reply = self.request(p.CMD_CAPABILITY)
        return p.parse_capabilities(reply.body) if reply and reply.ok else set()

    def features(self, ids: list[int] | None = None) -> dict[int, int]:
        ids = ids or p.PROBE_FEATURES
        payload = bytes([len(ids)]) + bytes(ids)
        reply = self.request(p.CMD_GET_FEATURE, payload)
        return p.parse_features(reply.body) if reply and reply.ok else {}

    def eq_profiles(self) -> list[p.EqProfile]:
        reply = self.request(p.CMD_GET_EQ_LIST, wait=1.5)
        return p.parse_eq_list(reply.body) if reply and reply.ok else []

    def active_preset(self) -> int | None:
        reply = self.request(p.CMD_GET_EQ_PRESET)
        return reply.body[0] if reply and reply.ok and reply.body else None

    def bass_engine(self) -> list[tuple[int, int, int, int]]:
        reply = self.request(p.CMD_MULTI_BASS)
        if reply and reply.ok:
            return p.parse_bass_engine(reply.body)
        reply = self.request(p.CMD_BASS_ENGINE)
        return p.parse_bass_engine(reply.body) if reply and reply.ok else []

    def info(self) -> DeviceInfo:
        """Read everything worth showing on a status screen in one go."""
        return DeviceInfo(
            firmware=self.firmware(),
            battery=self.battery(),
            region=self.region(),
            capabilities=self.capabilities(),
            features=self.features(),
            active_preset=self.active_preset(),
        )

    # ---------------------------------------------------------- writes --
    def set_feature(self, feature_id: int, on: bool) -> bool:
        reply = self.request(p.CMD_SET_FEATURE, bytes([feature_id, 1 if on else 0]))
        return bool(reply and reply.ok)

    def set_eq_preset(self, preset_id: int) -> bool:
        reply = self.request(p.CMD_SET_EQ_PRESET, bytes([preset_id]))
        return bool(reply and reply.ok)

    def write_eq(self, profile: p.EqProfile) -> bool:
        """Store an EQ profile and make it active.

        Tries modify first, then add, because which one the earbuds accept
        depends on whether that slot already holds something.
        """
        for action in (2, 1):
            reply = self.request(p.CMD_SET_CUSTOM_EQ, profile.encode(action), wait=1.5)
            if reply and reply.ok:
                return True
        return False

    def delete_eq(self, eq_id: int) -> bool:
        profile = p.EqProfile(eq_id, "", [])
        reply = self.request(p.CMD_SET_CUSTOM_EQ, profile.encode(3), wait=1.5)
        return bool(reply and reply.ok)

    def set_bass_band(self, band: int, value: int) -> bool:
        reply = self.request(p.CMD_SET_BASS_ENGINE, p.encode_bass_band(band, value))
        return bool(reply and reply.ok)

    def set_bass_engine(self, values: list[int], enable: bool = True) -> bool:
        """Set every bass band at once and flip the engine's own switch."""
        self.set_feature(29, enable)
        return all(self.set_bass_band(i + 1, v) for i, v in enumerate(values))
