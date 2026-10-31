#!/usr/bin/env python3
"""Run every test module.

    python tests/run_all.py          everything
    python tests/run_all.py --offline  skip the ones needing real earbuds

Hardware tests skip themselves when no earbuds are connected, so this is safe
to run anywhere.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

OFFLINE = ["test_protocol", "test_curve"]
# test_server runs either way: routing, validation and static files need no
# earbuds, and the device parts skip themselves.
HARDWARE = ["test_server", "test_controller"]


def main() -> int:
    modules = OFFLINE if "--offline" in sys.argv else OFFLINE + HARDWARE
    failures = 0
    for name in modules:
        print(f"\n{name}")
        module = importlib.import_module(name)
        failures += module.run()
    print("\n" + ("=" * 40))
    print("FAILURES PRESENT" if failures else "everything passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
