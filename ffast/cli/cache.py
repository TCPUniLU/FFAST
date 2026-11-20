"""``ffast-cli cache``: list, pin, unpin and delete parsed cache copies (ADR 0108)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from ffast.io import cache_space, frame_cache

GB = 2**30


def _root():
    return frame_cache._folder()


def _budget_bytes():
    from ffast.io.budget import budget
    return min(frame_cache.MAX_BYTES, budget().parsed_limit)


def _resolve(root, text):
    """The stored string for ``text``: the resolved path, or the spelling that
    a pin or copy already has (the file may be gone)."""
    known = set(cache_space.read_pins(root))
    known |= {d.path for d in cache_space.datasets(root) if d.path}
    for candidate in (os.path.realpath(text), os.path.abspath(text), text):
        if candidate in known:
            return candidate
    return os.path.realpath(text)


def _rows(root):
    rows = []
    pins = cache_space.read_pins(root)
    seen = set()
    for d in cache_space.datasets(root):
        seen.add(d.path)
        rows.append({"path": d.path, "bytes": d.bytes, "pinned": d.pinned, "open": d.open,
                     "last_used": d.last_used, "source_missing": d.path is not None and not os.path.exists(d.path),
                     "has_copy": True})
    for p in pins:
        if p not in seen:
            rows.append({"path": p, "bytes": 0, "pinned": True, "open": False, "last_used": None,
                         "source_missing": not os.path.exists(p), "has_copy": False})
    rows.sort(key=lambda r: (r["last_used"] is None, -(r["last_used"] or 0), r["path"] or ""))
    return rows


def cmd_list(args: argparse.Namespace) -> None:
    root = _root()
    rows = _rows(root)
    if args.json:
        out = [{**r, "last_used": None if r["last_used"] is None else
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["last_used"]))} for r in rows]
        print(json.dumps(out, indent=2))
        return
    for r in rows:
        notes = []
        if r["pinned"]:
            notes.append("pinned")
        if r["open"]:
            notes.append("open")
        if r["source_missing"]:
            notes.append("source missing")
        if not r["has_copy"]:
            notes.append("no copy")
        when = "-" if r["last_used"] is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_used"]))
        print(f"{r['path'] or '(unknown source)'}  {r['bytes'] / GB:.2f} GB  {when}"
              + ("  " + ", ".join(notes) if notes else ""))
    print(f"Total: {cache_space.used(root) / GB:.2f} GB of {_budget_bytes() / GB:.2f} GB")


def cmd_pin(args: argparse.Namespace) -> None:
    root = _root()
    path = os.path.realpath(args.path)
    cache_space.write_pins(root, cache_space.read_pins(root) + [path])
    print(f"Pinned {path}")


def cmd_unpin(args: argparse.Namespace) -> None:
    root = _root()
    path = _resolve(root, args.path)
    pins = cache_space.read_pins(root)
    if path not in pins:
        print(f"{path} is not pinned", file=sys.stderr)
        sys.exit(1)
    cache_space.write_pins(root, [p for p in pins if p != path])
    print(f"Unpinned {path}")


def cmd_delete(args: argparse.Namespace) -> None:
    root = _root()
    path = _resolve(root, args.path)
    pins = cache_space.read_pins(root)
    has_copy = any(d.path == path for d in cache_space.datasets(root))
    if not has_copy and path not in pins:
        print(f"Nothing is cached or pinned for {path}", file=sys.stderr)
        sys.exit(1)
    if any(d.path == path and d.open for d in cache_space.datasets(root)):
        print(f"{path} is open in a running FFAST; close it first", file=sys.stderr)
        sys.exit(1)
    if path in pins and not args.yes:
        if input(f"{path} is pinned. Delete its copies and the pin? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Kept.")
            return
    try:
        freed = cache_space.delete_dataset(root, path)
    except cache_space.InUse:
        print(f"{path} is open in a running FFAST; close it first", file=sys.stderr)
        sys.exit(1)
    cache_space.write_pins(root, [p for p in pins if p != path])
    print(f"Deleted {path}: {freed / GB:.2f} GB freed")


def cmd_export(args: argparse.Namespace) -> None:
    from ffast.readers import bundle
    from ffast.readers.contract import ReaderError
    started = time.monotonic()
    try:
        bundle.export(args.path, args.output, force=args.force)
    except ReaderError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    size = sum(f.stat().st_size for f in args.output.rglob("*") if f.is_file())
    print(f"Wrote {args.output}: {size / GB:.2f} GB in {time.monotonic() - started:.1f} s")


def register(sub) -> None:
    parser = sub.add_parser("cache", help="Parsed cache commands")
    cache_sub = parser.add_subparsers(dest="cache_command")
    cache_sub.required = True

    cmd = cache_sub.add_parser("list", help="List the cached datasets")
    cmd.add_argument("--json", action="store_true", help="Print JSON")
    cmd.set_defaults(func=cmd_list)

    cmd = cache_sub.add_parser("pin", help="Keep a file's cached copies when space is needed")
    cmd.add_argument("path")
    cmd.set_defaults(func=cmd_pin)

    cmd = cache_sub.add_parser("unpin", help="Let a file's cached copies be evicted again")
    cmd.add_argument("path")
    cmd.set_defaults(func=cmd_unpin)

    cmd = cache_sub.add_parser("delete", help="Delete every cached copy of a file")
    cmd.add_argument("path")
    cmd.add_argument("--yes", action="store_true", help="Do not ask when the file is pinned")
    cmd.set_defaults(func=cmd_delete)

    cmd = cache_sub.add_parser("export", help="Write a file's parsed dataset as a portable bundle folder")
    cmd.add_argument("path", help="The .xyz or .extxyz file")
    cmd.add_argument("-o", "--output", required=True, type=Path, help="The bundle folder to write")
    cmd.add_argument("--force", action="store_true", help="Replace the folder when it exists")
    cmd.set_defaults(func=cmd_export)
