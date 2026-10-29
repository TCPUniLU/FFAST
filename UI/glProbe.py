"""Decide whether pyqtgraph's ``useOpenGL`` is safe on this machine.

pyqtgraph can render plots through OpenGL (``useOpenGL=True``) for speed. On
Apple Silicon, Qt routes that through Apple's deprecated OpenGL-over-Metal shim,
which mis-rasterizes ``ScatterPlotItem`` and ultimately **segfaults** in
``glDrawArrays`` while blitting the point atlas via ``drawPixmapFragments``.

A segfault can't be caught in-process (it kills the interpreter), and trying to
*reproduce* the crash in a child process is unreliable — a single offscreen
``grab()`` doesn't always hit the same paint path, so it can wrongly report
"safe" and re-arm the crash. Instead we **identify the renderer**: creating an
offscreen GL context and reading ``GL_VERSION``/``GL_RENDERER`` is harmless (the
crash is in vertex submission during real painting, not in context setup or a
string query). The Apple shim reports a version like ``"2.1 Metal - 90.5"``, so
we disable OpenGL whenever the renderer is that shim and keep it on for real
native drivers (Linux/Windows).

Override with ``FFAST_OPENGL=1`` (force GL), ``=0`` (force software), or
``=auto`` (default: detect).
"""

import logging
import os
import sys

logger = logging.getLogger("FFAST")


def query_gl():
    """Return ``(vendor, renderer, version)`` lowercased, or ``None``.

    Safe to call: it only creates a context and reads strings, never the
    ``glDrawArrays`` path that crashes. Returns ``None`` if no GL context can be
    obtained (e.g. headless), letting the caller fall back to a heuristic.
    """
    try:
        from PySide6 import QtWidgets
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext

        app = QtWidgets.QApplication.instance()
        transient = None
        if app is None:  # standalone use (e.g. `python UI/glProbe.py`)
            transient = QtWidgets.QApplication([])  # noqa: F841 — keep alive
        surface = QOffscreenSurface()
        surface.create()
        ctx = QOpenGLContext()
        if not ctx.create() or not ctx.makeCurrent(surface):
            return None
        funcs = ctx.functions()
        GL_VENDOR, GL_RENDERER, GL_VERSION = 0x1F00, 0x1F01, 0x1F02
        info = (
            str(funcs.glGetString(GL_VENDOR) or ""),
            str(funcs.glGetString(GL_RENDERER) or ""),
            str(funcs.glGetString(GL_VERSION) or ""),
        )
        ctx.doneCurrent()
        return tuple(s.lower() for s in info)
    except Exception as exc:
        logger.warning("OpenGL renderer query failed (%s)", exc)
        return None


def opengl_is_safe():
    """Whether pyqtgraph ``useOpenGL`` can be enabled here (overridable)."""
    override = os.environ.get("FFAST_OPENGL", "auto").strip().lower()
    if override in ("0", "false", "off", "software", "sw"):
        return False
    if override in ("1", "true", "on", "gl", "opengl"):
        return True

    info = query_gl()
    if info is None:
        # Couldn't identify the renderer. macOS GL is the deprecated shim, so be
        # conservative there; trust native drivers elsewhere.
        return sys.platform != "darwin"

    vendor, renderer, version = info
    # Apple's OpenGL-over-Metal shim (e.g. version "2.1 metal - 90.5") segfaults
    # pyqtgraph's ScatterPlotItem. Disable GL for it; allow real native drivers.
    if "metal" in version or "metal" in renderer:
        logger.info(
            "OpenGL is Apple's Metal shim (%r) — using software rendering.",
            version,
        )
        return False
    return True


if __name__ == "__main__":
    print("gl info        :", query_gl())
    print("opengl_is_safe :", opengl_is_safe())
