"""Which FFAST code this process runs: one hash over its installed files.

The server sends its fingerprint in HELLO_ACK; a client compares it with its
own and warns when they differ. A new cluster job with automatic setup already
reinstalls a changed package (``cluster/bootstrap.py`` ``needs_provision``),
but reconnecting to a job that is still running, or a profile with its own
``ffast_server_cmd`` (e.g. a container), can run older code with no sign of it.

The hash covers what a server runs and what the wheel ships: the code files of
the ``ffast`` package (Python, the web client, TOML configs) and the top-level
``server.py``. Both sit at the same place relative to each other in
an editable checkout and in an installed wheel, so the two give the same hash
for the same code. Caches and dotfiles (``__pycache__``, ``.DS_Store``) are
left out.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Code and configs only. JSON is left out: it is data, and the wheel and a
# checkout do not always ship the same JSON files (ffast/config/default.json).
_SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".toml"}


def _files(root: Path) -> list[Path]:
    """The files the fingerprint covers under ``root`` (the dir holding ffast/)."""
    files = [
        p for p in (root / "ffast").rglob("*")
        if p.is_file()
        and p.suffix in _SUFFIXES
        and "__pycache__" not in p.parts
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    ]
    server = root / "server.py"
    if server.is_file():
        files.append(server)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def fingerprint_of(root: Path) -> str:
    """The fingerprint of the FFAST code under ``root``: 16 hex characters."""
    root = Path(root)
    h = hashlib.sha256()
    for p in _files(root):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:16]


@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """This process's fingerprint, computed once (a few MB of files)."""
    import ffast

    return fingerprint_of(Path(ffast.__file__).resolve().parent.parent)


def server_code_notice(server_fingerprint: Optional[str], local_fingerprint: str) -> Optional[str]:
    """The warning to show when the server runs other code, or None.

    A server that sends no fingerprint predates this check, so it is older.
    """
    if server_fingerprint == local_fingerprint:
        return None
    if not server_fingerprint:
        what = "older FFAST code than this app"
    else:
        what = "different FFAST code than this app"
    return (
        f"The server runs {what}, so some features may fail. Restart the "
        f"server to update it (on a cluster: disconnect and submit a new job)."
    )
