"""The readers FFAST knows (ADR 0095, decisions 9 and 13).

Bundled readers are listed in ``ffast.readers.BUNDLED``. Outside readers are
pip packages that name the reader class in the entry point group
``ffast.readers``::

    [project.entry-points."ffast.readers"]
    foo = "ffast_foo:FooReader"

Names are unique across all readers. A file's ending picks the candidates
(registration order); a candidate's optional ``can_read(path)`` can say no.
"""
from __future__ import annotations

import fnmatch
import logging
import os
from importlib.metadata import entry_points

logger = logging.getLogger("FFAST")

ENTRY_POINT_GROUP = "ffast.readers"

_readers: dict[str, type] = {}
_discovered = False


def register(reader) -> None:
    """Add a reader class. ValueError when it has no usable name or
    extensions, or its name is taken."""
    discover()      # the bundled readers and the entry points come first, always
    name = getattr(reader, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Reader {reader!r} needs a name (a non-empty string)")
    extensions = getattr(reader, "extensions", None)
    if not isinstance(extensions, (list, tuple)) or not extensions \
            or not all(isinstance(e, str) and e for e in extensions):
        raise ValueError(f"Reader '{name}' needs extensions, a list such as [\"*.foo\"]")
    if name in _readers:
        raise ValueError(f"Reader '{name}' is already registered")
    _readers[name] = reader


def unregister(name: str) -> None:
    _readers.pop(name, None)


def discover() -> None:
    """Register the bundled readers and the entry points, once. A reader that
    cannot be loaded or whose name is taken is skipped with a log line: one
    broken package must not stop the others."""
    global _discovered
    if _discovered:
        return
    _discovered = True
    from ffast import readers as package
    for reader in package.BUNDLED:
        _try_register(reader, "bundled")
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            reader = ep.load()
        except Exception as exc:
            logger.warning("Skipping reader '%s' (import failed: %s)", ep.name, exc,
                           extra={"popup": False})
            continue
        _try_register(reader, f"entry point '{ep.name}'")


def _try_register(reader, source) -> None:
    try:
        register(reader)
    except ValueError as exc:
        logger.warning("Skipping reader from %s: %s", source, exc, extra={"popup": False})


def get(name: str):
    """The reader class called ``name``, or None."""
    discover()
    return _readers.get(name)


def all_readers() -> list:
    discover()
    return list(_readers.values())


def _claims(reader, path: str) -> bool:
    base = os.path.basename(path).lower()
    return any(fnmatch.fnmatch(base, pattern.lower()) for pattern in reader.extensions)


def readers_for(path: str) -> list:
    """The readers that can take ``path``, best first: those that claim its
    ending, in registration order, without those whose ``can_read`` says no.
    A reader that claims only ``*`` (ASE's) comes after the readers that name
    the ending, as a fallback. A reader with no ``can_read`` is decided by its
    ending alone. A ``can_read`` that fails counts as no."""
    found = []
    catch_all = lambda r: all(e == "*" for e in r.extensions)
    for reader in sorted(all_readers(), key=catch_all):     # stable: order kept within each group
        if not _claims(reader, path):
            continue
        can_read = getattr(reader, "can_read", None)
        if can_read is not None:
            try:
                if not can_read(path):
                    continue
            except Exception as exc:
                logger.info("%s can_read failed on %s: %s", reader.name, path, exc)
                continue
        found.append(reader)
    return found


def type_rows() -> list[dict]:
    """The rows of the page's Type list: ``extension`` is the first ending,
    for pages that know one, and ``extensions`` all of them. ``reader`` marks
    a type whose file can be probed for its names. A reader with ``hidden`` is
    not a Type of its own (xyz: "ase (auto)" resolves to it for those files)."""
    return [{"name": r.name, "extension": r.extensions[0], "extensions": list(r.extensions),
             "reader": True}
            for r in all_readers() if not getattr(r, "hidden", False)]


def writable_formats() -> list[dict]:
    """What Export can write: every format of every reader that can write, as
    ``{"format", "label", "ext", "can_write", "reader"}``, in the order the
    readers list them (the first reader that lists a format owns it)."""
    seen, out = set(), []
    for reader in all_readers():
        if not callable(getattr(reader, "open_writer", None)):
            continue
        for info in getattr(reader, "write_formats", []):
            if info["format"] not in seen:
                seen.add(info["format"])
                out.append({**info, "can_write": dict(info.get("can_write") or {}),
                            "reader": reader.name})
    out.sort(key=lambda f: f.get("order", 1000))       # stable: the dialog's order
    return out


def writer_for(format: str):
    """The reader class that writes ``format``, or None."""
    owner = next((f["reader"] for f in writable_formats() if f["format"] == format), None)
    return get(owner) if owner else None


def format_for_ext(ext: str):
    """The format of a file ending (``.gen`` is dftb), or None."""
    return next((f["format"] for f in writable_formats() if f["ext"] == ext), None)
