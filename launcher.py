"""The `ffast` command: start the browser client.

The browser client is the main client (ADR 0090). The desktop client is being
retired and starts with ``ffast-qt``.

This lives at the top level, not under ``ffast/``, as the other entry points do
(``main.py``, ``server.py``).
"""

import sys


LIVE_COMMANDS = ("bundle", "lammps")


def main(argv: list | None = None) -> None:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in LIVE_COMMANDS:
        # `ffast bundle` / `ffast lammps data` (ADR 0086) start no client.
        from ffast.live.cli import main as live_main

        sys.exit(live_main(args))

    from ffast.renderers.web.launcher import main as web_main

    return web_main(args)
