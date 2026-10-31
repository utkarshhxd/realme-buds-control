#!/usr/bin/env python3
"""Command line interface for realme Buds.

    python cli.py ports
    python cli.py info
    python cli.py eq classic
    python cli.py eq --bands 55:1 120:3 320:-2 1300:3 3200:1 7500:-2
    python cli.py bass 2 1 0
    python cli.py feature 24 on
"""

from __future__ import annotations

import argparse
import sys

from buds import FEATURES, PRESETS, Buds, EqProfile, find_ports
from buds.presets import DEFAULT_EQ_SLOT


def pick_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    ports = [port for port in find_ports() if port.is_candidate]
    if not ports:
        sys.exit("No earbuds found. Connect them to this computer first, "
                 "then try 'ports' to see what is available.")
    # find_ports already sorts recognised devices first
    return ports[0].device


def cmd_ports(_args) -> None:
    ports = find_ports()
    if not ports:
        print("No serial ports found.")
        print("The earbuds only appear while connected to this computer;")
        print("if they are talking to a phone, reconnect them here first.")
        return
    for port in ports:
        mark = " " if port.is_candidate else "-"
        print(f"{mark} {port.device:<8} {port.name}")
    if any(not port.is_candidate for port in ports):
        print("\n(ports marked '-' are local endpoints, not paired devices)")


def cmd_info(args) -> None:
    with Buds(pick_port(args.port)) as buds:
        info = buds.info()
        print(f"Firmware     {info.firmware or 'unknown'}")
        print(f"Region code  {info.region if info.region is not None else 'unknown'}")
        if info.battery:
            names = {1: "left", 2: "right", 3: "case"}
            parts = [f"{names.get(k, k)} {v}%" for k, v in sorted(info.battery.items())]
            print("Battery      " + ", ".join(parts))
        if info.active_preset is not None:
            print(f"Preset       {info.active_preset}")
        if info.features:
            print("\nFeatures:")
            for fid, state in sorted(info.features.items()):
                print(f"  [{fid:>2}] {FEATURES.get(fid, 'unknown'):<24} "
                      f"{'on' if state else 'off'}")
        missing = [f for f in (11, 19, 54) if info.features and f not in info.features]
        if missing:
            names = ", ".join(FEATURES.get(f, str(f)) for f in missing)
            print(f"\nNot implemented by this firmware: {names}")


def cmd_eq(args) -> None:
    if args.bands:
        bands = []
        for item in args.bands:
            try:
                hz, gain = item.split(":")
                bands.append((int(hz), int(gain)))
            except ValueError:
                sys.exit(f"Bad band '{item}'. Use HZ:GAIN, for example 1300:3")
        name = args.name or "Custom"
    else:
        preset = PRESETS.get((args.preset or "").lower())
        if not preset:
            sys.exit("Unknown preset. Available: " + ", ".join(PRESETS))
        bands, name = list(preset.bands), preset.name

    profile = EqProfile(eq_id=args.slot, name=name[:12], bands=bands)
    with Buds(pick_port(args.port)) as buds:
        if buds.write_eq(profile):
            print(f"Applied '{name}':")
            for hz, gain in bands:
                print(f"  {hz:>6} Hz  {gain:+d} dB")
        else:
            sys.exit("The earbuds rejected the EQ.")


def cmd_bass(args) -> None:
    values = [args.low, args.mid, args.high]
    with Buds(pick_port(args.port)) as buds:
        if buds.set_bass_engine(values, enable=not args.off):
            state = "off" if args.off else f"low {args.low}, mid {args.mid}, high {args.high}"
            print(f"Bass engine: {state}")
        else:
            sys.exit("The earbuds rejected the bass settings.")


def cmd_feature(args) -> None:
    on = args.state.lower() in ("on", "1", "true", "yes")
    with Buds(pick_port(args.port)) as buds:
        if buds.set_feature(args.id, on):
            print(f"{FEATURES.get(args.id, args.id)}: {'on' if on else 'off'}")
        else:
            sys.exit("The earbuds refused that change. "
                     "This firmware may not implement that feature.")


def cmd_presets(_args) -> None:
    for preset in PRESETS.values():
        print(f"{preset.key:<10} {preset.name}")
        print(f"           {preset.description}")
        bands = "  ".join(f"{hz}Hz {g:+d}" for hz, g in preset.bands)
        print(f"           {bands}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Control realme Buds earbuds.")
    parser.add_argument("-p", "--port", help="serial port (auto-detected by default)")
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("ports", help="list serial ports").set_defaults(func=cmd_ports)
    subs.add_parser("info", help="read device information").set_defaults(func=cmd_info)
    subs.add_parser("presets", help="show the built-in presets").set_defaults(func=cmd_presets)

    eq = subs.add_parser("eq", help="apply an equaliser curve")
    eq.add_argument("preset", nargs="?", help="preset name, see 'presets'")
    eq.add_argument("--bands", nargs="+", metavar="HZ:GAIN",
                    help="six custom bands, e.g. 55:1 120:3 320:-2 1300:3 3200:1 7500:-2")
    eq.add_argument("--name", help="name to store the profile under")
    eq.add_argument("--slot", type=int, default=DEFAULT_EQ_SLOT, help="EQ slot id")
    eq.set_defaults(func=cmd_eq)

    bass = subs.add_parser("bass", help="set the dynamic bass engine")
    bass.add_argument("low", type=int, nargs="?", default=0)
    bass.add_argument("mid", type=int, nargs="?", default=0)
    bass.add_argument("high", type=int, nargs="?", default=0)
    bass.add_argument("--off", action="store_true", help="disable the engine")
    bass.set_defaults(func=cmd_bass)

    feature = subs.add_parser("feature", help="toggle a switch feature")
    feature.add_argument("id", type=int)
    feature.add_argument("state", choices=["on", "off"])
    feature.set_defaults(func=cmd_feature)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
