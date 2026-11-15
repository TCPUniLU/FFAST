"""One optional fast-reader install per server, shared by all its windows.

Only a venv provisioned by FFAST may be changed through the browser. A
disconnected window stops waiting, not the installation (ADR 0101).
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path

from ffast.readers import install_fast, text

logger = logging.getLogger("FFAST")


def managed_environment() -> bool:
    prefix = Path(sys.prefix)
    return (sys.prefix != sys.base_prefix
            and (prefix / ".ffast-install.sha256").is_file()
            and os.access(prefix, os.W_OK))


class FastReaderSetup:
    def __init__(self):
        self.offered = False
        self.task = None
        self.lines = []
        self.listeners = set()

    def status(self):
        return {"ready": bool(text.fast_parser_usable()),
                "managed": managed_environment(),
                "busy": self.task is not None and not self.task.done(),
                "missing": text._fast_missing}

    def offer(self):
        state = self.status()
        state["offer"] = not state["ready"] and not self.offered
        if state["offer"]:
            self.offered = True
            # The actionable prompt replaces the CLI warning in this server.
            text._fast_warned = True
        return state

    def skip(self):
        self.offered = True
        text._fast_warned = True
        return self.status()

    def _say(self, line):
        line = str(line).replace("Restart FFAST to use it.", "Checking activation in this server…")
        logger.info("fast reader: %s", line)
        self.lines.append(line)
        del self.lines[:-100]
        for listener in tuple(self.listeners):
            listener(line)

    async def _install(self):
        loop = asyncio.get_running_loop()
        source = Path(sys.prefix) / "ffast-xyz"
        if not (source / "Cargo.toml").is_file():
            source = install_fast.source_dir()
        try:
            code = await asyncio.to_thread(
                install_fast.install, source=source,
                out=lambda line: loop.call_soon_threadsafe(self._say, line))
            if code:
                return {**self.status(), "error": "The fast reader could not be installed. See the installation details."}
            # An absent package can be imported immediately. A native module
            # already loaded with an old contract cannot be unloaded safely.
            importlib.invalidate_caches()
            text._fast_usable = None
            if not text.fast_parser_usable():
                return {**self.status(), "error": "The reader was installed, but this server needs a restart to use it. Continue with Python for this load, then start a new cluster server job."}
            self._say("The fast reader is ready. Continuing the load.")
            return self.status()
        except Exception as exc:
            logger.exception("Fast reader installation failed", extra={"popup": False})
            self._say(str(exc))
            return {**self.status(), "error": "The fast reader could not be installed. See the installation details."}

    async def wait(self, progress):
        if self.task is None:
            return self.status()
        self.listeners.add(progress)
        for line in self.lines:
            progress(line)
        try:
            return await asyncio.shield(self.task)
        finally:
            self.listeners.discard(progress)

    async def install(self, progress):
        if self.status()["ready"]:
            return self.status()
        if not managed_environment():
            return {**self.status(), "error": "This server environment is manually managed. Run ffast install-fast-reader in its Python environment."}
        if self.task is None:
            self.task = asyncio.create_task(self._install())
        return await self.wait(progress)
