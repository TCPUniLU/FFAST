"""The `ffast` command: start the browser client.

The browser client is the only client (ADR 0090, ADR 0098).

This lives at the top level, not under ``ffast/``, as ``server.py`` does.
"""

import sys


LIVE_COMMANDS = ("bundle", "lammps")


def main(argv: list | None = None) -> None:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in LIVE_COMMANDS:
        # `ffast bundle` / `ffast lammps data` (ADR 0086) start no client.
        from ffast.live.cli import main as live_main

        sys.exit(live_main(args))
    if args and args[0] == "install-fast-reader":
        # Build the fast .xyz reader from source (ADR 0100); starts no client.
        from ffast.readers.install_fast import main as install_main

        sys.exit(install_main(args[1:]))

    from ffast.renderers.web.launcher import main as web_main

    return web_main(args)
