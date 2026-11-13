"""SSH key help for the cluster form (gap 8 of the Qt-removal audit,
2026-11-13).

The page cannot open a file dialog that gives the launcher a path, so the
launcher, on the machine that will use the key, lists the private keys in
``~/.ssh`` and checks a typed path. Only a file's first bytes are read, to
tell a private key from anything else; no key content leaves this module.
"""
from __future__ import annotations

import os

_HEAD = 64   # bytes read: enough for "-----BEGIN ... PRIVATE KEY-----"


def _is_private_key(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(_HEAD)
    except OSError:
        return False
    return head.startswith(b"-----BEGIN") and b"PRIVATE KEY" in head


def ssh_keys(ssh_dir: str | None = None) -> list[str]:
    """The private key files in ``ssh_dir`` (default ``~/.ssh``), sorted; under
    the home folder they are written ``~/...``, as a person types them."""
    folder = os.path.expanduser(ssh_dir or "~/.ssh")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    home = os.path.expanduser("~")
    out = []
    for name in names:
        path = os.path.join(folder, name)
        if name.endswith(".pub") or not os.path.isfile(path) or not _is_private_key(path):
            continue
        out.append("~" + path[len(home):] if path.startswith(home + os.sep) else path)
    return out


def check_key(path: str) -> dict:
    """Whether ``path`` names a private key: ``{"ok", "note"}``. Empty is fine:
    ssh then uses the agent or ``~/.ssh/config``."""
    text = (path or "").strip()
    if not text:
        return {"ok": True, "note": "Empty: ssh uses ssh-agent or ~/.ssh/config"}
    full = os.path.expanduser(text)
    if os.path.isdir(full):
        return {"ok": False, "note": "That is a folder"}
    if not os.path.exists(full):
        return {"ok": False, "note": "No such file"}
    if full.endswith(".pub"):
        return {"ok": False, "note": "That is the public half (.pub); choose the file without .pub"}
    if not os.access(full, os.R_OK):
        return {"ok": False, "note": "Cannot read it"}
    if not _is_private_key(full):
        return {"ok": False, "note": "Not a private key file"}
    return {"ok": True, "note": "Private key found"}
