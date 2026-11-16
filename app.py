#!/usr/bin/env python3
"""Shufersal Weekly Planner — local web app. Run: python3 app.py"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import planner
import shufersal

VERSION = "1.2.0"
PORT = int(os.environ.get("PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- hot reload

def _mtimes(exts):
    out = {}
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in ("cache", ".git", "__pycache__", "tests")]
        for f in files:
            if f.endswith(exts):
                p = os.path.join(root, f)
                try:
                    out[p] = os.path.getmtime(p)
                except OSError:
                    pass
    return out


def asset_rev() -> str:
    """Changes whenever any served file changes; the page polls it to auto-refresh."""
    return str(int(max(_mtimes((".py", ".html", ".css", ".js")).values(), default=0) * 1000))


def watch_and_restart(interval=1.0):
    """Restart this process in place when a .py file changes (HTML is read fresh per request)."""
    before = _mtimes((".py",))
    while True:
        time.sleep(interval)
        now = _mtimes((".py",))
        if now != before:
            print("↻ code changed — restarting…", flush=True)
            args = [a for a in sys.argv if a != "--no-browser"] + ["--no-browser"]
            os.execv(sys.executable, [sys.executable] + args)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("· " + fmt % args + "\n")

    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/stores":
            try:
                return self._send(200, {"stores": shufersal.load_stores()})
            except Exception as e:
                return self._send(502, {"error": f"Couldn't load the branch list: {e}"})
        if self.path == "/api/rev":
            return self._send(200, {"rev": asset_rev()})
        if self.path == "/api/status":
            return self._send(200, {"version": VERSION, "has_key": bool(planner.get_api_key({})),
                                    "cli": planner.find_cli(),
                                    "model": planner.DEFAULT_MODEL})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/plan":
            return self._send(404, {"error": "not found"})
        try:
            params = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if float(params.get("budget") or 0) <= 0:
                return self._send(400, {"error": "Set a budget above ₪0."})
            store = int(params.get("store") or 2)
            prices, promos = shufersal.load_branch(store, include_club=bool(params.get("include_club")),
                                                   force=bool(params.get("refresh")))
            result = planner.make_plan(params, prices, promos)
            result["store"] = store
            self._send(200, result)
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Shufersal Weekly Planner v{VERSION} → {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if "--no-reload" not in sys.argv:
        threading.Thread(target=watch_and_restart, daemon=True).start()
        print("Hot reload on: edits to .py restart the server, the page refreshes itself.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
