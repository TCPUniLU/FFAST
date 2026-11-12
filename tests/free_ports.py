"""Ports for servers a test starts.

Do not ask the OS for a free port (``bind(("127.0.0.1", 0))``), close it and
hand it to a server. The OS picks such ports from its ephemeral range
(49152–65535 on macOS), the same range it gives every outgoing connection,
and under load it cycles through that range in seconds. A server needs
1.5–6 s to start before it binds, and by then the port may have gone to
another worker's server, to a browser connection, or even to the same
server's other port. The server then dies with ``Errno 48 Address already in
use`` and the test sees ``Errno 61 Connect call failed``; or, after a server
stops, a test still finds "its" port held by another worker's server
(docs/flaky-tests.md, 2026-11-12).

So each pytest worker takes ports from its own block below the ephemeral
range, which the OS never hands out by itself, in turn.
"""

from __future__ import annotations

import itertools
import os
import random
import socket

_FIRST = 20000
_BLOCK = 1400
_BLOCKS = 20  # 20000 + 20 * 1400 = 48000, below the ephemeral range


def _worker_index() -> int:
    name = os.environ.get("PYTEST_XDIST_WORKER", "")  # "gw0", "gw1", … or unset
    return int(name[2:]) % _BLOCKS if name.startswith("gw") else 0


# A random start in the block, so two pytest runs at once rarely meet.
_turn = itertools.count(random.randrange(_BLOCK))


def _bindable(port: int) -> bool:
    """True if nothing holds ``port`` on any address, as the server binds it
    (``""``: every IPv4 and IPv6 address)."""
    for family, address in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            with socket.socket(family) as sock:
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((address, port))
        except OSError:
            return False
    return True


def free_port() -> int:
    """A port no other test of this run is using, nor anything else now."""
    base = _FIRST + _worker_index() * _BLOCK
    for _ in range(_BLOCK):
        port = base + next(_turn) % _BLOCK
        if _bindable(port):
            return port
    raise RuntimeError(f"no free port in {base}–{base + _BLOCK - 1}")
