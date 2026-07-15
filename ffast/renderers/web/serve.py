"""Static file server for the FFAST web renderer.

Serves the web app from ffast/renderers/web/static/ via HTTP so any browser
can connect to the ffast-server WebSocket and render a molecular scene.

Usage (programmatic, from server.py):
    from ffast.renderers.web.serve import start_static_server
    start_static_server(port=9000)

Usage (CLI):
    ffast-server --port 8765 --web-port 9000
    # Then open http://localhost:9000/?port=8765 in a browser.
"""
from __future__ import annotations

import functools
import http.server
import logging
import os
import threading

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class _SilentStaticHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that suppresses per-request access logs.

    Also sends no-cache headers so edits to index.html / ffast-viewer.js are
    always picked up on reload (the assets are tiny — caching buys nothing and
    causes stale-HTML/fresh-JS mismatches during development).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, format, *args):  # noqa: A002
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def start_static_server(port: int, host: str = "0.0.0.0") -> http.server.HTTPServer:
    """Start an HTTP server serving the web app in a daemon thread.

    ``host`` defaults to ``0.0.0.0`` (the cluster/remote case, where a browser
    on another machine reaches the served app); the local launcher passes
    ``127.0.0.1`` to keep the app loopback-only. Returns the HTTPServer so
    callers can shut it down explicitly (the daemon thread dies with the
    process regardless).
    """
    httpd = http.server.HTTPServer((host, port), _SilentStaticHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name=f"ffast-web-{port}")
    thread.start()
    logger.info("Web renderer serving at http://%s:%d (static dir: %s)", host, port, STATIC_DIR)
    return httpd
