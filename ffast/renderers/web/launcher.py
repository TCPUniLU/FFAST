"""One-command launcher for the FFAST web client (ADR 0045, decision #1).

`ffast-web` serves the web app on loopback, starts `ffast-server` for the
WebSocket RPC, and opens the user's already-installed browser at the app,
pointed at the WS port. This is the "just run one command, get the whole
workflow in a browser" entry point — no native GUI to install or launch, which
is the reliability payoff the Qt/Vispy desktop could not deliver.

The WS server runs as a subprocess (a clean process boundary matching how a
user would run `ffast-server` by hand); the launcher process owns the static
server and the browser, and blocks until the WS server exits or is interrupted.
Pointed at a remote server instead, the same web app drives a Server Connection
to that server — that path uses `ffast-server --web-port` directly and is out
of this launcher's scope.

Loopback caveat: the static web app is bound to 127.0.0.1, but the RPC surface
is not yet — `ffast-server` has no bind-host flag, so it listens on all
interfaces. Fully loopback-only operation (ADR 0045 decision #1) needs a
`--host 127.0.0.1` flag on `ffast-server`, which is blocked on the in-flight
ADR 0044 rewrite of `server._serve`. Until then, do not run this on an
untrusted network.
"""

from __future__ import annotations

import argparse
import http.server
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from typing import Callable

from ffast.renderers.web.serve import start_static_server

logger = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"

# Chromium-family executables that support a chromeless ``--app=URL`` window,
# most-preferred first. Discovered on PATH via shutil.which; macOS app bundles
# are not on PATH, so their absolute launcher paths are checked too.
_APP_MODE_EXECUTABLES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "msedge",
)
_MAC_APP_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


@dataclass
class LaunchResult:
    """Handles for a running launch, so callers can inspect and tear it down."""

    url: str
    web_port: int
    ws_port: int
    httpd: http.server.HTTPServer
    proc: subprocess.Popen


def app_url(web_port: int, ws_port: int, *, host: str = LOOPBACK) -> str:
    """URL of the web app, carrying the WebSocket RPC port as a query arg."""
    return f"http://{host}:{web_port}/?port={ws_port}"


def pick_free_port() -> int:
    """Ask the OS for an unused loopback TCP port."""
    with socket.socket() as sock:
        sock.bind((LOOPBACK, 0))
        return sock.getsockname()[1]


def wait_until_ready(
    port: int,
    *,
    host: str = LOOPBACK,
    timeout: float = 30.0,
    interval: float = 0.1,
) -> bool:
    """Block until a TCP connect to (host, port) succeeds, or timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(interval)
    return False


def _find_app_mode_executable() -> str | None:
    """Return a Chromium-family executable that supports ``--app=``, or None."""
    for exe in _APP_MODE_EXECUTABLES:
        path = shutil.which(exe)
        if path:
            return path
    for path in _MAC_APP_PATHS:
        if os.path.exists(path):
            return path
    return None


def open_browser(
    url: str,
    *,
    app_mode: bool = False,
    opener: Callable[[str], object] = webbrowser.open,
    launch: Callable[[list], object] = subprocess.Popen,
) -> None:
    """Open ``url`` in the user's browser.

    With ``app_mode``, launch a Chromium-family browser with ``--app=URL`` for a
    chromeless, desktop-feeling window; if none is found, fall back to the
    default browser so the launch still succeeds.
    """
    if app_mode:
        exe = _find_app_mode_executable()
        if exe is not None:
            launch([exe, f"--app={url}"])
            return
        logger.info(
            "No Chromium-family browser found for --app window; "
            "opening in the default browser instead"
        )
    opener(url)


def _spawn_server(ws_port: int) -> subprocess.Popen:
    """Start `ffast-server` on ``ws_port`` as a subprocess.

    Prefers the installed console script; falls back to ``python -m server``
    so a source checkout works without an editable install. Auto-snapshots are
    disabled — a throwaway local run does not need them. The server currently
    binds all interfaces (see the loopback caveat in the module docstring).
    """
    args = ["--port", str(ws_port), "--snapshot-interval", "0"]
    exe = shutil.which("ffast-server")
    cmd = [exe, *args] if exe else [sys.executable, "-m", "server", *args]
    logger.info("Starting ffast-server: %s", " ".join(cmd))
    return subprocess.Popen(cmd)


def _terminate(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception:  # pragma: no cover - best-effort teardown
        pass


def run(
    *,
    ws_port: int = 0,
    web_port: int = 0,
    host: str = LOOPBACK,
    app_mode: bool = False,
    spawn_server: Callable[[int], object] = _spawn_server,
    opener: Callable[[str], object] = webbrowser.open,
    ready_timeout: float = 30.0,
    block: bool = True,
) -> LaunchResult:
    """Serve the app, start the WS server, open the browser.

    Ports default to 0 → an OS-assigned free port. With ``block`` (the CLI
    default) the call blocks until the WS server exits or Ctrl-C; tests pass
    ``block=False`` and drive teardown via the returned :class:`LaunchResult`.
    """
    ws_port = ws_port or pick_free_port()
    web_port = web_port or pick_free_port()

    httpd = start_static_server(web_port, host=host)
    proc = spawn_server(ws_port)

    if not wait_until_ready(ws_port, host=host, timeout=ready_timeout):
        logger.error(
            "ffast-server did not accept connections on %s:%d within %.0fs; "
            "opening the browser anyway",
            host,
            ws_port,
            ready_timeout,
        )

    url = app_url(web_port, ws_port, host=host)
    logger.info("Opening FFAST web app at %s", url)
    open_browser(url, app_mode=app_mode, opener=opener)

    result = LaunchResult(url=url, web_port=web_port, ws_port=ws_port, httpd=httpd, proc=proc)
    if not block:
        return result

    try:
        proc.wait()
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
    finally:
        _terminate(proc)
        httpd.shutdown()
    return result


def main(argv: list | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="ffast-web",
        description="Launch the FFAST web client: start the server on loopback "
        "and open it in your browser.",
    )
    parser.add_argument(
        "--ws-port",
        type=int,
        default=0,
        metavar="PORT",
        help="WebSocket RPC port (default: an OS-assigned free port).",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=0,
        metavar="PORT",
        help="HTTP port for the web app (default: an OS-assigned free port).",
    )
    parser.add_argument(
        "--app",
        action="store_true",
        help="Open a chromeless app window (Chrome/Edge/Chromium --app) instead "
        "of a normal browser tab.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the servers but do not open a browser (print the URL only).",
    )
    args = parser.parse_args(argv)

    opener = (lambda url: logger.info("Web app ready at %s", url)) if args.no_browser else webbrowser.open
    run(
        ws_port=args.ws_port,
        web_port=args.web_port,
        app_mode=args.app,
        opener=opener,
    )


if __name__ == "__main__":
    main()
