"""Wire protocol for realme Buds earbuds over Bluetooth SPP.

Frame layout
------------
    AA | LEN | CTRL | 00 | [FSN] | payload

``LEN`` is a 7-bit varint (1-2 bytes) holding ``len(payload) + 2``, or ``+ 3``
when the frame is fragmented.  The low two bits of ``CTRL`` carry the fragment
state: 0 single, 1 first, 2 middle, 3 last.  Fragmented frames insert a
sequence byte (``FSN``) before the payload.

Packet layout (the frame payload)
---------------------------------
    CMD (u16 LE) | SEQ (u8) | LEN (u16 LE) | DATA

Bit 15 of ``CMD`` marks a response.  The first byte of a response's ``DATA`` is
a status code where zero means success.

There is no checksum, encryption or authentication anywhere in the protocol;
being paired is the only requirement.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SOF = 0xAA
RESPONSE_BIT = 0x8000

# ---------------------------------------------------------------- commands --
# 0x01xx read, 0x02xx notify, 0x03xx firmware, 0x04xx write, 0x05xx unsolicited
CMD_CAPABILITY = 0x0100
CMD_FIRMWARE = 0x0105
CMD_BATTERY = 0x0106
CMD_KEY_FUNCTIONS = 0x0108
CMD_ANC_STATE = 0x010C
CMD_GET_FEATURE = 0x010D
CMD_GET_EQ_PRESET = 0x010F
CMD_REGION = 0x0120
CMD_GET_EQ_LIST = 0x0122
CMD_BASS_ENGINE = 0x0124
CMD_MULTI_BASS = 0x012C

CMD_SET_FEATURE = 0x0403
CMD_SET_ANC = 0x0404
CMD_SET_EQ_PRESET = 0x0406
CMD_SET_CUSTOM_EQ = 0x0418
CMD_SET_BASS_ENGINE = 0x041B

COMMAND_NAMES = {
    CMD_CAPABILITY: "capability bitmap",
    CMD_FIRMWARE: "firmware version",
    CMD_BATTERY: "battery",
    CMD_KEY_FUNCTIONS: "key functions",
    CMD_ANC_STATE: "ANC state",
    CMD_GET_FEATURE: "get switch feature",
    CMD_GET_EQ_PRESET: "active EQ preset",
    CMD_REGION: "region code",
    CMD_GET_EQ_LIST: "EQ list",
    CMD_BASS_ENGINE: "bass engine",
    CMD_MULTI_BASS: "multi-band bass engine",
    CMD_SET_FEATURE: "set switch feature",
    CMD_SET_ANC: "set ANC",
    CMD_SET_EQ_PRESET: "set EQ preset",
    CMD_SET_CUSTOM_EQ: "set custom EQ",
    CMD_SET_BASS_ENGINE: "set bass engine",
}

# ---------------------------------------------------------------- features --
# Toggles addressed through CMD_GET_FEATURE / CMD_SET_FEATURE.  Not every model
# implements every one; the earbuds simply stay silent about those they lack.
FEATURES = {
    4: "Wear detection",
    5: "Power saving",
    6: "Game mode",
    8: "Auto answer",
    9: "Vocal enhance",
    11: "Hearing enhancement",
    17: "Dual device connect",
    19: "AI microphone",
    24: "Hi-Res audio (LHDC)",
    26: "Wind noise reduction",
    27: "Spatial audio",
    29: "Dynamic bass engine",
    38: "Find phone",
    54: "Virtual bass",
}

# Queried on every connection by the official app, whatever the model.
PROBE_FEATURES = [7, 6, 4, 5, 11, 8, 17, 19, 24, 9, 26, 12, 29, 27, 54, 38]

EQ_PRESETS = {0: "Nature Balance", 1: "Bass Boost", 2: "Clear Vocals", 3: "Clear Bass"}

# Firmware-enforced limits, established by probing real hardware: band centre
# frequencies are accepted as sent, but a gain range wider than +/-6 is
# rejected with status 1.
EQ_GAIN_MIN = -6
EQ_GAIN_MAX = 6
EQ_BANDS = 6
BASS_GAIN_MIN = -5
BASS_GAIN_MAX = 5

# Capability bitmap: bit index -> commands it enables.  Bits absent from this
# table carry no command this client uses.
CAPABILITY_BITS = {
    0: [CMD_FIRMWARE], 1: [CMD_BATTERY], 2: [0x0107],
    3: [CMD_KEY_FUNCTIONS, 0x0401], 4: [0x0109], 5: [0x0400], 6: [0x0402],
    7: [CMD_GET_FEATURE, CMD_SET_FEATURE], 8: [CMD_ANC_STATE, CMD_SET_ANC],
    9: [0x0405], 10: [CMD_SET_EQ_PRESET, CMD_GET_EQ_PRESET],
    11: [0x0110, 0x0407], 13: [0x0408], 14: [0x0409], 15: [0x040A, 0x0111],
    19: [0x040E, 0x040D, 0x0115, 0x0116], 22: [0x0205], 23: [0x0F00],
    25: [0x0118, 0x0411], 26: [0x011A, 0x0412], 29: [0x0112, 0x040B],
    30: [0x011E, 0x011F, 0x0415], 32: [CMD_REGION],
    34: [CMD_GET_EQ_LIST, CMD_SET_CUSTOM_EQ],
    38: [CMD_BASS_ENGINE, CMD_SET_BASS_ENGINE], 43: [0x041E],
    46: [0x0128, 0x050E], 54: [CMD_MULTI_BASS], 58: [0x0131, 0x0428],
    60: [0x042C],
}


def _s8(value: int) -> int:
    """Clamp to a signed byte and return its unsigned representation."""
    value = max(-128, min(127, int(value)))
    return value & 0xFF


def _from_s8(byte: int) -> int:
    return byte - 256 if byte > 127 else byte


def encode_varint(value: int) -> bytes:
    if value > 0x7F:
        return bytes([(value & 0x7F) | 0x80, value >> 7])
    return bytes([value])


def build_frame(cmd: int, seq: int, data: bytes = b"") -> bytes:
    """Wrap a command and payload into a complete wire frame."""
    packet = struct.pack("<HBH", cmd, seq & 0xFF, len(data)) + data
    return bytes([SOF]) + encode_varint(len(packet) + 2) + b"\x00\x00" + packet


@dataclass
class Packet:
    cmd: int
    seq: int
    data: bytes

    @property
    def is_response(self) -> bool:
        return bool(self.cmd & RESPONSE_BIT)

    @property
    def base_cmd(self) -> int:
        return self.cmd & ~RESPONSE_BIT

    @property
    def status(self) -> int | None:
        """Status byte of a response, or None for an unsolicited packet."""
        if self.is_response and self.data:
            return self.data[0]
        return None

    @property
    def ok(self) -> bool:
        return self.status == 0

    @property
    def body(self) -> bytes:
        """Response payload with the status byte stripped off."""
        return self.data[1:] if self.is_response and self.data else self.data

    @property
    def name(self) -> str:
        return COMMAND_NAMES.get(self.base_cmd, f"0x{self.base_cmd:04X}")


def parse_frames(stream: bytes) -> tuple[list[Packet], bytes]:
    """Pull every complete packet out of a byte stream.

    Returns the packets found and whatever trailing bytes belong to a frame
    that has not fully arrived yet, so the caller can prepend them next time.
    """
    packets: list[Packet] = []
    i = 0
    pending = bytearray()
    while i < len(stream):
        if stream[i] != SOF:
            i += 1
            continue
        if i + 2 >= len(stream):
            break
        if stream[i + 1] > 0x7F:
            if i + 3 >= len(stream):
                break
            length = (stream[i + 1] & 0x7F) | (stream[i + 2] << 7)
            size = 2
        else:
            length = stream[i + 1]
            size = 1
        ctrl = stream[i + 1 + size]
        fragment = ctrl & 0x3
        header = 3 + size + (1 if fragment else 0)
        payload_len = length - (3 if fragment else 2)
        if payload_len <= 0:
            i += 1
            continue
        if i + header + payload_len > len(stream):
            break  # frame still in flight
        payload = stream[i + header:i + header + payload_len]
        i += header + payload_len

        if fragment in (1, 2):
            pending.extend(payload)
            continue
        if fragment == 3:
            pending.extend(payload)
            payload, pending = bytes(pending), bytearray()

        if len(payload) >= 5:
            cmd, seq, dlen = struct.unpack("<HBH", payload[:5])
            data = payload[5:5 + dlen]
            if len(data) == dlen:
                packets.append(Packet(cmd, seq, data))
    return packets, stream[i:]


# --------------------------------------------------------------------- EQ --
@dataclass
class EqProfile:
    """One stored equaliser slot."""

    eq_id: int
    name: str
    bands: list[tuple[int, int]] = field(default_factory=list)  # (hz, gain dB)
    active: bool = False
    gain_min: int = EQ_GAIN_MIN
    gain_max: int = EQ_GAIN_MAX

    def encode(self, action: int) -> bytes:
        """Build a CMD_SET_CUSTOM_EQ payload. Action 1 add, 2 modify, 3 delete."""
        name = self.name.encode("utf-8")
        out = bytearray([action, _s8(self.gain_min), _s8(self.gain_max),
                         self.eq_id, len(name)])
        out += name
        out.append(len(self.bands))
        for hz, gain in self.bands:
            out += struct.pack("<H", hz)
            out.append(_s8(gain))
        return bytes(out)


def parse_eq_list(body: bytes) -> list[EqProfile]:
    """Decode a CMD_GET_EQ_LIST response body into profiles."""
    profiles: list[EqProfile] = []
    if not body:
        return profiles
    count, pos = body[0], 1
    for _ in range(count):
        if pos + 5 > len(body):
            break
        active = bool(body[pos])
        gain_min = _from_s8(body[pos + 1])
        gain_max = _from_s8(body[pos + 2])
        eq_id = body[pos + 3]
        name_len = body[pos + 4]
        pos += 5
        name = body[pos:pos + name_len].decode("utf-8", "replace")
        pos += name_len
        if pos >= len(body):
            break
        n_bands = body[pos]
        pos += 1
        bands = []
        for _ in range(n_bands):
            if pos + 3 > len(body):
                break
            hz = struct.unpack_from("<H", body, pos)[0]
            bands.append((hz, _from_s8(body[pos + 2])))
            pos += 3
        profiles.append(EqProfile(eq_id, name, bands, active, gain_min, gain_max))
    return profiles


def parse_bass_engine(body: bytes) -> list[tuple[int, int, int, int]]:
    """Decode a bass-engine response into (band, value, min, max) tuples."""
    out = []
    if not body:
        return out
    count = body[0]
    for i in range(count):
        pos = 1 + i * 4
        if pos + 4 > len(body):
            break
        lo, hi, value, band = body[pos:pos + 4]
        out.append((band, _from_s8(value), _from_s8(lo), _from_s8(hi)))
    return out


def encode_bass_band(band: int, value: int,
                     lo: int = BASS_GAIN_MIN, hi: int = BASS_GAIN_MAX) -> bytes:
    return bytes([_s8(lo), _s8(hi), _s8(value), band])


def parse_features(body: bytes) -> dict[int, int]:
    """Decode a CMD_GET_FEATURE response. Unsupported slots are padded (0, 0)."""
    result: dict[int, int] = {}
    if not body:
        return result
    count = body[0]
    pairs = body[1:1 + count * 2]
    for i in range(0, len(pairs) - 1, 2):
        feature_id, state = pairs[i], pairs[i + 1]
        if feature_id:
            result[feature_id] = state
    return result


def parse_battery(body: bytes) -> dict[int, int]:
    """Decode a battery response into {1: left, 2: right, 3: case}."""
    result = {}
    if not body:
        return result
    for i in range(body[0]):
        pos = 1 + i * 2
        if pos + 2 > len(body):
            break
        result[body[pos]] = body[pos + 1]
    return result


def parse_capabilities(body: bytes) -> set[int]:
    """Decode the capability bitmap into the set of supported command ids."""
    supported: set[int] = set()
    for bit in range(len(body) * 8):
        if body[bit // 8] & (1 << (bit % 8)):
            supported.update(CAPABILITY_BITS.get(bit, []))
    return supported
