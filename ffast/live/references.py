"""The references FFAST knows about ("Run with FFAST" run folders, plan §7 F2).

A reference is a ``.bundle`` folder (DetectAna) with its LAMMPS include
``.lmp`` beside it. ``make_reference`` (Make reference, ``ffast bundle``)
records each one it writes in ``~/.ffast/references/<id>.json``
(``$FFAST_REFERENCES`` overrides), so the Run-with-FFAST dialog can offer
them as a list instead of asking for a path.

``describe`` reads a bundle without DetectAna: atoms and when it was made
from ``manifest.json``, the LAMMPS type order from the include's
``elements`` line. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ENV = "FFAST_REFERENCES"
DEFAULT_DIR = "~/.ffast/references"
ELEMENTS_RE = re.compile(r"^elements\s+(.+?)\s*$", re.M)


def references_dir(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(os.path.expanduser(environ.get(ENV) or DEFAULT_DIR))


def _record_id(bundle: Path) -> str:
    return hashlib.sha256(str(bundle).encode()).hexdigest()[:16]


def record_reference(bundle_dir, environ=None, *, name=None) -> Path | None:
    """Remember ``bundle_dir``; never fails the caller (returns None on an OS error)."""
    bundle = Path(bundle_dir).expanduser().resolve()
    d = references_dir(environ)
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_record_id(bundle)}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"bundle": str(bundle), "name": name,
                                   "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def include_of(bundle: Path) -> Path:
    """``x.bundle`` → ``x.lmp`` beside it."""
    return bundle.with_suffix(".lmp")


def describe(bundle_dir) -> dict:
    """What the dialog shows for one reference; ``problem`` set when it cannot be used."""
    bundle = Path(bundle_dir).expanduser()
    out = {"bundle": str(bundle), "name": bundle.stem, "include": str(include_of(bundle)),
           "n_atoms": None, "formula": None, "types": None, "made_utc": None, "problem": None}
    try:
        manifest = json.loads((bundle / "manifest.json").read_text())
        mol = manifest.get("molecule") or {}
        elements = mol.get("elements") or []
        out["n_atoms"] = mol.get("n_atoms") or len(elements) or None
        counts = Counter(elements)
        out["formula"] = "".join(f"{e}{counts[e] if counts[e] > 1 else ''}" for e in sorted(counts)) or None
        out["made_utc"] = manifest.get("created_utc")
    except (OSError, ValueError):
        out["problem"] = "not a reference bundle (no readable manifest.json)"
        return out
    try:
        m = ELEMENTS_RE.search(include_of(bundle).read_text())
        out["types"] = m.group(1).split() if m else None
    except OSError:
        pass
    if out["types"] is None:
        out["problem"] = f"no include file {include_of(bundle).name} beside it"
    return out


def recorded(environ=None) -> list[dict]:
    """The recorded references whose folder still exists, newest first."""
    d = references_dir(environ)
    rows = []
    for p in d.glob("*.json") if d.is_dir() else []:
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        bundle = Path(rec.get("bundle", ""))
        if bundle.is_dir():
            rows.append((rec.get("recorded_utc") or "", bundle, rec.get("name")))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [{**describe(b), **({"name": name} if name else {})} for _, b, name in rows]
