"""Guard: the server/CLI import closure must stay Qt-free (ADR 0026).

`pip install ffast` (no `[gui]` extra) ships the headless core only. If a GUI
module ever creeps onto the import path of `ffast-server` or `ffast-cli`, a
headless install would break at import time. This test is that tripwire.

Runs in a fresh subprocess: pytest shares one interpreter, so an unrelated test
importing Qt earlier would poison `sys.modules` and false-fail an in-process
check.
"""

import subprocess
import sys

_PROBE = r"""
import sys
import server            # ffast-server entry closure
import ffast.cli.main    # ffast-cli entry closure

GUI_ROOTS = {"PySide6", "PyQt5", "PyQt6", "shiboken6",
             "qasync", "vispy", "pyqtgraph", "OpenGL"}
leaked = sorted(GUI_ROOTS & {m.split(".")[0] for m in sys.modules})
print(",".join(leaked))
"""


def test_server_and_cli_closure_is_qt_free():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    leaked = proc.stdout.strip()
    assert not leaked, f"GUI modules leaked into headless closure: {leaked}"
