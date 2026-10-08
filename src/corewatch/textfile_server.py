"""Serve *.prom files over HTTP for hosts that have no node_exporter.

Standard library only, so it runs on the python3 from the AIX Toolbox with
nothing else installed:

    python3 textfile_server.py --dir /var/lib/corewatch --port 9954

Where node_exporter is available, point its textfile collector at the same
directory and skip this server.
"""

from __future__ import annotations

import argparse
import glob
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def render(directory: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    parts: list[str] = []
    ages: list[str] = []
    for path in sorted(glob.glob(os.path.join(directory, "*.prom"))):
        try:
            with open(path) as handle:
                body = handle.read()
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        parts.append(body if body.endswith("\n") else body + "\n")
        ages.append('corewatch_textfile_age_seconds{file="%s"} %.0f\n' % (os.path.basename(path), age))
    header = (
        "# HELP corewatch_textfile_age_seconds Seconds since the collector last wrote this file.\n"
        "# TYPE corewatch_textfile_age_seconds gauge\n"
    )
    return "".join(parts) + (header + "".join(ages) if ages else "")


def make_handler(directory: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (name fixed by the base class)
            if self.path.split("?")[0] != "/metrics":
                self.send_error(404)
                return
            body = render(directory).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default="/var/lib/corewatch")
    parser.add_argument("--address", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9954)
    args = parser.parse_args(argv)
    HTTPServer((args.address, args.port), make_handler(args.dir)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
