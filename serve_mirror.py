#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal local server for the copied frontend.

It serves the static ./mirror folder and also stubs the dynamic seat/API
endpoints captured from the original demo so that the login, candidate info,
notice, paper instruction, and question screens can be viewed locally without
the original backend.

Usage:
    python serve_mirror.py [port]
"""
from __future__ import annotations

import mimetypes
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
MIRROR = ROOT / "mirror"
SESSION_FILE = MIRROR / "seat" / "session.json"
CSS_DIR = MIRROR / "seat" / "css"
SKIN_DIR = MIRROR / "seat" / "skin"
API_DIR = MIRROR / "api"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(MIRROR), **kwargs)

    def _api_file(self, path: str):
        """Return the captured stub file for a seat API path, if any."""
        normalized = path.rstrip("/")
        mapping = {
            "/seat/session": API_DIR / "session.json",
            "/seat/login": API_DIR / "login.json",
            "/seat/confirm": API_DIR / "confirm.json",
            "/seat/notice": API_DIR / "notice.json",
            "/seat/form": API_DIR / "form.json",
            "/seat/response": API_DIR / "response.json",
            "/seat/response/patch": API_DIR / "response_patch.json",
            "/seat/event": API_DIR / "event.json",
        }
        return mapping.get(normalized)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # Dynamic-ish endpoints captured from the real demo site.
        api_file = self._api_file(path)
        if api_file is not None:
            self._serve_json(api_file)
            return
        if path.startswith("/seat/css/"):
            css_id = path[len("/seat/css/"):].strip("/")
            self._serve_json(CSS_DIR / f"{css_id}.json")
            return
        if path.startswith("/seat/skin/"):
            # The real URL contains ':' (e.g. demo:0589...), which cannot be a
            # Windows directory name.  The mirror stores the same files with '_'.
            rel = path[len("/seat/skin/"):]
            rel = rel.replace(":", "_")
            self._serve_static(MIRROR / "seat" / "skin" / rel)
            return

        # Convenience: redirect the root to the copied demo page, because the
        # Angular app chooses a different view based on the URL path.
        if path == "/" or path == "" or path == "/index.html":
            self.send_response(302)
            self.send_header("Location", "/demo/t/xajtdxsnb_20260123/1")
            self.end_headers()
            return

        # Everything else is a regular static file under mirror/.
        target = MIRROR / path.lstrip("/")
        if target.is_file():
            self._serve_static(target)
            return
        # SPA fallback: serve the copied index for non-file routes.
        if not Path(path).suffix:
            self._serve_static(MIRROR / "index.html")
        else:
            self.send_error(404)

    def do_POST(self):
        # The SPA sends several state-changing requests during the captured
        # flow. Return the captured stub responses so the copied frontend can
        # be browsed locally without the original backend.
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        api_file = self._api_file(path)
        if api_file is not None:
            self._serve_json(api_file)
            return
        if path.startswith("/seat/css/"):
            css_id = path[len("/seat/css/"):].strip("/")
            self._serve_json(CSS_DIR / f"{css_id}.json")
            return
        self.send_error(404)

    def _serve_static(self, file_path: Path):
        if not file_path.is_file():
            self.send_error(404)
            return
        ctype, _ = mimetypes.guess_type(str(file_path))
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_json(self, file_path: Path):
        if not file_path.exists():
            self.send_error(404)
            return
        data = file_path.read_bytes() if file_path.is_file() else b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def log_message(self, fmt, *args):
        # Keep the console quieter for static asset requests.
        if len(args) >= 2:
            url = str(args[1]) if args[1] else ""
            if url.startswith("/client/") or url.startswith("/seat/"):
                return
        super().log_message(fmt, *args)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving {MIRROR} at http://127.0.0.1:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
