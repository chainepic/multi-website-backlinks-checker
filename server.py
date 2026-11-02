#!/usr/bin/env python3
"""Local dashboard for the owned-site reciprocal link check.

Run: python3 server.py
Open: http://127.0.0.1:8787
"""

from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from checker import REPORT_PATH, load_config, run_scan, write_report

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = "127.0.0.1"
PORT = 8787

_lock = threading.Lock()
_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "sites": {},
}


def _read_report() -> dict | None:
    if not REPORT_PATH.exists():
        return None
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def _start_scan() -> bool:
    with _lock:
        if _state["running"]:
            return False
        config = load_config()
        hosts = [site["host"] for group in config["groups"] for site in group["sites"]]
        _state["running"] = True
        _state["error"] = None
        _state["finished_at"] = None
        _state["started_at"] = time.strftime("%H:%M:%S")
        _state["sites"] = {host: "排队" for host in hosts}

    def progress(host: str, status: str) -> None:
        with _lock:
            _state["sites"][host] = status

    def worker() -> None:
        try:
            report = run_scan(on_progress=progress)
            write_report(report)
            with _lock:
                _state["error"] = None
                _state["finished_at"] = report["generated_at"]
        except Exception as exc:
            with _lock:
                _state["error"] = str(exc)
        finally:
            with _lock:
                _state["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[http] {self.address_string()} {fmt % args}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/report":
            report = _read_report()
            if report is None:
                self._json(404, {"error": "还没有检查结果"})
                return
            self._json(200, report)
            return
        if path == "/api/status":
            with _lock:
                self._json(200, dict(_state))
            return
        if path == "/api/config":
            self._json(200, load_config())
            return
        if path in {"/", "/index.html"}:
            self._file(STATIC / "index.html")
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/scan":
            self._json(404, {"error": "not found"})
            return
        if not _start_scan():
            self._json(409, {"error": "正在检查"})
            return
        self._json(202, {"ok": True})

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json(404, {"error": "missing page"})
            return
        data = path.read_bytes()
        self._send(200, data, "text/html; charset=utf-8")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"多网站互链检查器  http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
