"""LocalServerManager: start/stop a managed local ffast-server subprocess."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass

from .token import SessionToken

logger = logging.getLogger("FFAST")


@dataclass
class LocalServerProcess:
    """Handle to a managed local ffast-server subprocess.

    Distinct from RemoteSession — this owns the process lifetime.
    The app layer creates both and manages them independently:

        token = SessionToken.generate()
        handle = manager.start(port, token)
        session = await connect_direct(port=handle.port, token=token.plaintext)
    """

    port: int
    token_plaintext: str  # plaintext to pass to connect_direct()
    process: subprocess.Popen


class LocalServerManager:
    """Start and stop a local ffast-server as a managed subprocess."""

    def start(
        self,
        port: int,
        token: SessionToken,
        *,
        recovery_window: int = 30,
        snapshot_interval: int = 0,
        extra_args: list[str] | None = None,
    ) -> LocalServerProcess:
        """Launch ffast-server and return a handle.

        Parameters
        ----------
        port:
            Port for ffast-server to listen on.
        token:
            Session token — hash is passed to the server via --token-hash.
        recovery_window:
            Seconds to keep server alive after unexpected CONTROLLING disconnect.
        snapshot_interval:
            Auto-snapshot interval in minutes (0 = disabled).
        extra_args:
            Additional CLI args forwarded verbatim.
        """
        # Resolve ffast-server relative to the current Python executable so
        # it works inside a venv without the venv being activated.
        _bin = os.path.dirname(sys.executable)
        _server_exe = os.path.join(_bin, "ffast-server")
        if not os.path.isfile(_server_exe):
            _server_exe = "ffast-server"  # fall back to PATH

        cmd = [
            _server_exe,
            "--port", str(port),
            "--token-hash", token.hash,
            "--recovery-window", str(recovery_window),
            "--snapshot-interval", str(snapshot_interval),
        ]
        if extra_args:
            cmd.extend(extra_args)

        logger.info("Starting local server: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return LocalServerProcess(
            port=port,
            token_plaintext=token.plaintext,
            process=proc,
        )

    def stop(self, handle: LocalServerProcess, *, timeout: int = 5) -> None:
        """Terminate the managed server process."""
        try:
            handle.process.terminate()
            handle.process.wait(timeout=timeout)
            logger.info("Local server stopped (port=%d)", handle.port)
        except Exception as exc:
            logger.warning("Error stopping local server: %s", exc)
            try:
                handle.process.kill()
            except Exception:
                pass
