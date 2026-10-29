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
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

class _StaticHTTPServer(http.server.ThreadingHTTPServer):
    """
    A wrapper class for the _SilentStaticHandler that handles multiple concurrent requests from the web browser.
    Having a single threaded server might block some requests from the web browser, resulting in the error 'net::CONNECTION_REFUSED'.
    Now, with Threading http, server does not crash in cases such as reloading the page, or starting the web application.
    """
    request_queue_size = 128
    daemon_threads = True


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

    def _send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cluster_request(self, method: str) -> None:
        """Pass a /cluster/ request to the launcher's cluster broker (web parity
        plan 2026-10-29). The custom header stops another web site in the same
        browser from using the broker (a cross-site page cannot send it without
        a CORS preflight, which this server never allows); the secret stops a
        DNS-rebinding page."""
        broker = getattr(self.server, "cluster_broker", None)
        if broker is None:
            return self._send_json(404, {
                "error": "This page was not opened by ffast-web, so it cannot reach a cluster."})
        secret = getattr(self.server, "cluster_secret", None)
        if not secret or self.headers.get("X-FFAST-Launch") != secret:
            return self._send_json(403, {"error": "Not allowed"})
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                return self._send_json(413, {"error": "Request too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send_json(400, {"error": "Not JSON"})
            if not isinstance(body, dict):
                return self._send_json(400, {"error": "Not a JSON object"})
        status, reply = broker.handle(method, self.path.split("?", 1)[0], body)
        self._send_json(status, reply)

    def do_POST(self):
        if self.path.startswith("/cluster/"):
            return self._cluster_request("POST")
        self.send_error(404)

    def do_GET(self):
        if self.path.startswith("/cluster/"):
            return self._cluster_request("GET")
        # Which FFAST code served this page, so the page can warn when the
        # server it connects to runs other code (code_fingerprint.js).
        if self.path.split("?", 1)[0] == "/code-fingerprint.json":
            from ffast.session.code_fingerprint import code_fingerprint

            body = json.dumps({"code_fingerprint": code_fingerprint()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def start_static_server(port: int, host: str = "0.0.0.0", *, cluster_broker=None,
                        cluster_secret=None) -> http.server.HTTPServer:
    """Start an HTTP server serving the web app in a daemon thread.

    ``host`` defaults to ``0.0.0.0`` (the cluster/remote case, where a browser
    on another machine reaches the served app); the local launcher passes
    ``127.0.0.1`` to keep the app loopback-only. Returns the HTTPServer so
    callers can shut it down explicitly (the daemon thread dies with the
    process regardless).

    Only the ``ffast-web`` launcher passes a ``cluster_broker``; the secret is
    the page's ``launch=`` id, which /cluster/ requests must send back.
    """
    #httpd = http.server.HTTPServer((host, port), _SilentStaticHandler)
    httpd = _StaticHTTPServer((host, port), _SilentStaticHandler)
    httpd.cluster_broker = cluster_broker
    httpd.cluster_secret = cluster_secret
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name=f"ffast-web-{port}")
    thread.start()
    logger.info("Web renderer serving at http://%s:%d (static dir: %s)", host, port, STATIC_DIR)
    return httpd
