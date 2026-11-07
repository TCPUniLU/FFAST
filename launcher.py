"""The `ffast` command: start the browser client.

The browser client is the main client (ADR 0090). The desktop client is being
retired and starts with ``ffast-qt``.

This lives at the top level, not under ``ffast/``, as the other entry points do
(``main.py``, ``server.py``).
"""

import sys


def main(argv: list | None = None) -> None:
    from ffast.renderers.web.launcher import main as web_main

    return web_main(argv if argv is not None else sys.argv[1:])
