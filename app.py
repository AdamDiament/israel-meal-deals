#!/usr/bin/env python3
"""Shufersal Weekly Planner — local web app. Run: python3 app.py"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import planner
import shufersal

VERSION = "1.1.0"
PORT = int(os.environ.get("PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))


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
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
