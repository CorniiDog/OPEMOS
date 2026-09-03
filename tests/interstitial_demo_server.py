#!/usr/bin/env python3
"""Bounded loopback-only server for the macOS interstitial visual test."""

import argparse
import http.server
import os
import signal
import threading
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    pill = b""
    def log_message(self, _format, *_arguments):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self):
        if self.path == "/health":
            payload = b'{"schemaVersion":1,"status":"ready"}\n'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/opemos-pill.svg":
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(self.pill)))
            self.end_headers()
            self.wfile.write(self.pill)
            return
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        super().do_GET()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    parser.add_argument("--duration", required=True, type=int)
    parser.add_argument("--pill", required=True, type=Path)
    args = parser.parse_args()
    if not args.directory.is_dir() or args.directory.is_symlink():
        raise SystemExit("demo directory is unsafe")
    pill_info = args.pill.lstat()
    if args.pill.is_symlink() or not args.pill.is_file() or not 1 <= pill_info.st_size <= 16 * 1024:
        raise SystemExit("pill asset is unsafe or excessive")
    Handler.pill = args.pill.read_bytes()
    if not 5 <= args.duration <= 600:
        raise SystemExit("duration must be between 5 and 600 seconds")
    os.chdir(args.directory)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    args.port_file.write_text(f"{server.server_port}\n", encoding="ascii")
    timer = threading.Timer(args.duration, server.shutdown)
    timer.daemon = True
    timer.start()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    signal.signal(signal.SIGINT, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        timer.cancel()
        server.server_close()


if __name__ == "__main__":
    main()
