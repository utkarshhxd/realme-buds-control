"""Protocol tests.

The fixtures are real responses captured from a realme Buds Air8 on firmware
1.1.0.104, so these check the parsers against hardware rather than against
assumptions about it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buds import protocol as p  # noqa: E402


def h(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


def test_frame_roundtrip():
    frame = p.build_frame(0x010D, 7, bytes([1, 11]))
    assert frame == h("AA 09 00 00 0D 01 07 02 00 01 0B")
    packets, rest = p.parse_frames(frame)
    assert rest == b"" and len(packets) == 1
    assert packets[0].cmd == 0x010D and packets[0].data == bytes([1, 11])


def test_partial_frame_is_held_back():
    frame = p.build_frame(0x0106, 1)
    packets, rest = p.parse_frames(frame[:-3])
    assert packets == [] and rest == frame[:-3]
    packets, rest = p.parse_frames(frame[:-3] + frame[-3:])
    assert len(packets) == 1 and rest == b""


def test_response_flag_and_status():
    packets, _ = p.parse_frames(h("AA 08 00 00 03 84 28 01 00 00"))
    reply = packets[0]
    assert reply.is_response and reply.base_cmd == p.CMD_SET_FEATURE
    assert reply.status == 0 and reply.ok


def test_rejected_write_is_not_ok():
    # a gain range wider than the firmware allows comes back as status 1
    packets, _ = p.parse_frames(h("AA 09 00 00 18 84 7A 02 00 01 06"))
    assert packets[0].status == 1 and not packets[0].ok


def test_parse_features_skips_padding():
    body = h("10 06 00 04 01 05 01 0B 00 08 00 11 01 18 01 09 01 "
             "1A 01 0C 00 1D 00 1B 00 26 00 00 00 00 00 00 00")
    features = p.parse_features(body)
    assert features[24] == 1          # Hi-Res on
    assert features[27] == 0          # spatial off
    assert 0 not in features          # padding dropped
    assert 54 not in features         # unsupported, never reported
    assert len(features) == 13


def test_parse_battery():
    assert p.parse_battery(h("03 01 64 02 64 03 00")) == {1: 100, 2: 100, 3: 0}


def test_parse_capabilities():
    caps = p.parse_capabilities(h("BF 0F 5A E6 65 DC 40 1C"))
    assert p.CMD_SET_CUSTOM_EQ in caps      # bit 34
    assert p.CMD_SET_BASS_ENGINE in caps    # bit 38
    assert p.CMD_REGION in caps             # bit 32
    assert 0x0F00 not in caps               # bit 23 clear on this firmware


def test_parse_eq_list():
    body = h("03 "
             "00 FA 06 04 07 437573746F6D31 06 3E0000 FA0000 E80300 A00F00 401F00 803E00 "
             "00 FA 06 05 07 437573746F6D32 06 3E0002 FA0001 E80300 A00F01 401F01 803E01 "
             "01 FA 06 06 07 436C6173736963 06 370001 780003 4001FE 140503 800C01 4C1DFE")
    profiles = p.parse_eq_list(body)
    assert [x.name for x in profiles] == ["Custom1", "Custom2", "Classic"]
    assert profiles[0].bands[0] == (62, 0)
    assert profiles[2].active is True
    # custom centres and signed gains survive the round trip
    assert profiles[2].bands == [(55, 1), (120, 3), (320, -2),
                                 (1300, 3), (3200, 1), (7500, -2)]


def test_eq_encode_matches_wire_format():
    profile = p.EqProfile(eq_id=6, name="Classic",
                          bands=[(55, 1), (120, 3), (320, -2),
                                 (1300, 3), (3200, 1), (7500, -2)])
    assert profile.encode(2) == h("02 FA 06 06 07 436C6173736963 06 "
                                  "370001 780003 4001FE 140503 800C01 4C1DFE")


def test_parse_bass_engine():
    bands = p.parse_bass_engine(h("03 FB 05 04 01 FB 05 02 02 FB 05 00 03"))
    assert bands == [(1, 4, -5, 5), (2, 2, -5, 5), (3, 0, -5, 5)]


def test_encode_bass_band_is_signed():
    assert p.encode_bass_band(1, -3) == h("FB 05 FD 01")


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
