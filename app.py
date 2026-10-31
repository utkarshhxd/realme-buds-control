#!/usr/bin/env python3
"""Start realme Buds Control and open it in a browser.

    python app.py              # pick a free port, open the browser
    python app.py --port 8777  # fixed port
    python app.py --no-browser # just serve, useful for tests
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser

from buds.server import HOST, build


def free_port(preferred: int) -> int:
    """Use ``preferred`` if it is free, otherwise let the OS choose."""
    with socket.socket() as probe:
        try:
            probe.bind((HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="realme Buds Control")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    port = free_port(args.port)
    httpd, api = build(port)
    url = f"http://{HOST}:{port}/"

    print(f"realme Buds Control  ->  {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.shutdown()
        api.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
