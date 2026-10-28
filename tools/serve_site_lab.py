#!/usr/bin/env python3
"""Serve only generated feed metadata on loopback for updater integration tests."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--requests-log", type=Path, required=True)
    parser.add_argument("--prefix", default="/survey-correction-updates")
    args = parser.parse_args()
    root = args.root.resolve()
    prefix = "/" + args.prefix.strip("/")
    routes = {prefix + "/updates.json": ("updates.json", "application/json; charset=utf-8"),
              prefix + "/advinst_update.json": ("advinst_update.json", "application/json; charset=utf-8"),
              prefix + "/": ("index.html", "text/html; charset=utf-8")}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/health":
                data = json.dumps({"pid": os.getpid(), "root": str(root)}).encode("ascii")
                kind = "application/json"
            elif route in routes:
                filename, kind = routes[route]
                try:
                    data = (root / filename).read_bytes()
                except FileNotFoundError:
                    self.send_error(404, "The generated file is not ready")
                    return
            else:
                self.send_error(403, "Metadata-only lab: installer downloads are blocked")
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, fmt, *values):
            with args.requests_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                         "request": fmt % values}) + "\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state = {"pid": os.getpid(), "port": server.server_port, "host": "127.0.0.1",
             "root": str(root), "prefix": prefix}
    args.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()