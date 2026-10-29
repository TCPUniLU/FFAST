"""The `ffast` command: start the desktop client, or the browser one if Qt is absent.

The desktop client is the complete one, so the bare command should land there.
It needs the ``gui`` extra, and a base install deliberately has no PySide6 — on
those machines (a cluster login node, a container) the browser client is the
whole point, so falling back to it beats an ImportError traceback.

Detection uses ``find_spec`` rather than importing PySide6: ``main`` has to set
``QT_PLUGIN_PATH`` and the bytecode-cache prefix *before* anything touches Qt,
so this module must not touch it first.

This lives at the top level, not under ``ffast/``, because it imports ``main``
— a Desktop-Client module. The Headless Core imports no such thing, and
``tests/ffast/test_ffast_core_boundary.py`` enforces that.
"""

import importlib.util
import logging
import sys

logger = logging.getLogger(__name__)


def _qt_available() -> bool:
    """True when PySide6 is importable, without importing it."""
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        # A half-installed or shadowed PySide6 raises here rather than
        # returning None; treat it as absent so we still start something.
        return False


def main(argv: list | None = None) -> None:
    if _qt_available():
        from main import cli

        return cli()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info(
        "PySide6 is not installed, so starting the browser client.\n"
        "For the desktop client, install the gui extra: "
        "pip install 'ffast[gui]' — then run ffast-qt.\n"
    )
    from ffast.renderers.web.launcher import main as web_main

    return web_main(argv if argv is not None else sys.argv[1:])
