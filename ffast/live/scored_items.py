"""Score any loaded dataset against a reference bundle (ADR 0093).

A scored item is one dataset scored with one bundle and one set of settings.
Its folder holds everything and nothing about the screen (guard b)::

    <analyses>/<item id>/
        item.json                       parent fingerprint and name, bundle,
                                        settings, state, hidden
        events.jsonl                    same format as a run folder's
        analysis/<analysis id>/         same layout as a run folder's analysis
            settings.json  scores.jsonl  progress.json  alarms/

``score_dataset`` writes that folder. The reference decides which frames can be
scored: it is asked about every frame (``validate_frame``) and a frame it
refuses gets a gap row with its own error text. FFAST adds only one check of
its own: a dataset with a cell is refused, because the reference does not know
periodic systems yet.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ffast.live import run_folder as rf
from ffast.live.analysis_writer import ROLE, AnalysisWriter
from ffast.live.include_file import DEFAULT_PERSIST_PS

log = logging.getLogger("ffast.live.scored_items")

ENV = "FFAST_ANALYSES"
DEFAULT_DIR = "~/.ffast/analyses"
ITEM = "item.json"
CHUNK = 256                     # frames scored per bundle call and per stop check
PERIODIC_REASON = "FFAST does not score periodic systems yet; this file has a cell"
NO_TIME_EVENT = "no time between frames: frames are marked, alarms are off"


def analyses_dir(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(os.path.expanduser(environ.get(ENV) or DEFAULT_DIR))


def make_settings(frame_dt_ps: float | None, persist_ps: float | None = None) -> dict:
    return {"thresholds": "bundle", "role": ROLE,
            "frame_dt_ps": None if frame_dt_ps is None else float(frame_dt_ps),
            "persist_ps": float(persist_ps or DEFAULT_PERSIST_PS)}


def item_id(parent_fp: str, bundle_dir, settings: dict) -> str:
    """16 hex characters naming one parent, one bundle and one set of settings."""
    h = hashlib.sha256()
    h.update(str(parent_fp).encode())
    h.update(rf.analysis_id(bundle_dir, settings).encode())
    return h.hexdigest()[:16]


def item_dir(iid: str, environ=None) -> Path:
    return analyses_dir(environ) / iid


def check_dataset(dataset) -> str | None:
    """Why this whole dataset cannot be scored, or None. A non-zero cell does."""
    lattice = dataset.getLattice() if hasattr(dataset, "getLattice") else None
    if lattice is not None and bool(np.any(np.asarray(lattice, dtype=float) != 0)):
        return PERIODIC_REASON
    return None


def suggest_frame_dt_fs(dataset) -> float | None:
    """The time between frames in the file's ``time`` field, in fs, or None.

    Only when every frame has a number and the steps are equal (within 1e-6
    relative). extxyz has no standard unit for it; femtoseconds is assumed (D2),
    and the dialog tells the user to check.
    """
    try:
        field = dataset.getFrameField("time")
    except Exception:
        return None
    if field is None:
        return None
    t = np.asarray(field, dtype=float)
    if t.ndim != 1 or len(t) < 2 or not np.all(np.isfinite(t)):
        return None
    d = np.diff(t)
    if d[0] <= 0 or not np.allclose(d, d[0], rtol=1e-6, atol=0):
        return None
    return float(d[0])


def _steps_of(dataset) -> np.ndarray | None:
    """The file's ``step`` field when every frame has a whole number (D3)."""
    try:
        field = dataset.getFrameField("step")
    except Exception:
        return None
    if field is None:
        return None
    v = np.asarray(field, dtype=float)
    if v.ndim != 1 or len(v) != dataset.getN() or not np.all(np.isfinite(v)):
        return None
    return v.astype(np.int64)


@dataclass
class _Frame:
    index: int
    step: int
    time: float | None
    positions: np.ndarray


def _symbols(dataset, index) -> list[str]:
    from ase.data import chemical_symbols

    return [chemical_symbols[int(z)] for z in dataset.getElements(index)]


def write_item_json(directory: Path, **changes) -> dict:
    data = rf.read_json(directory / ITEM) or {}
    data.update(changes)
    rf.write_json_atomic(directory / ITEM, data)
    return data


def _event(directory: Path):
    return lambda kind, **fields: rf.append_event(directory, kind, **fields)


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(folder: Path) -> threading.Lock:
    """One lock per item folder. A cancelled scoring finishes its chunk in its thread
    after the task is gone, so Continue scoring can start while it still writes: the
    second one waits, then sees the rows the first wrote."""
    with _locks_guard:
        return _locks.setdefault(str(folder), threading.Lock())


def mark_interrupted(folder: Path) -> None:
    """An item still saying ``scoring`` when nothing scores it (the server stopped
    mid-scoring) is incomplete: say ``cancelled`` so Continue scoring is offered."""
    item = read_item(folder)
    if item and item.get("state") == "scoring":
        write_item_json(Path(folder), state="cancelled")


def score_dataset(
    dataset,
    bundle_dir,
    settings: dict,
    *,
    environ=None,
    stopped: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] = lambda done, total: None,
    name: str | None = None,
) -> Path:
    """Score every frame of ``dataset`` that has no row yet; return the item folder.

    Scoring again with the same key scores only the missing frames (this is
    Continue scoring). The state ends as ``done``, ``cancelled`` (``stopped``
    turned true) or ``refused`` (with the reason).
    """
    from ffast.live.detectana_api import require_detectana

    detectana = require_detectana()
    bundle_dir = Path(bundle_dir)
    fp = dataset.getFingerprint()
    iid = item_id(fp, bundle_dir, settings)
    folder = item_dir(iid, environ)
    folder.mkdir(parents=True, exist_ok=True)
    with _lock_for(folder):
        return _score_locked(dataset, bundle_dir, settings, detectana, fp, iid, folder,
                             stopped=stopped, progress=progress, name=name)


def _score_locked(dataset, bundle_dir, settings, detectana, fp, iid, folder, *, stopped, progress, name):
    from ffast.live.data_file import bundle_sha256

    event = _event(folder)
    old = rf.read_json(folder / ITEM) or {}
    write_item_json(folder, item_id=iid, parent=fp, parent_name=name or old.get("parent_name") or dataset.getName(),
                    bundle=str(bundle_dir), settings=settings, state="scoring", hidden=False,
                    created_utc=old.get("created_utc") or rf.utc_now())
    event("scoring_start", frames=int(dataset.getN()))

    def refuse(reason: str) -> Path:
        write_item_json(folder, state="refused", reason=reason)
        event("refused", reason=reason)
        return folder

    reason = check_dataset(dataset)
    if reason:
        return refuse(reason)
    try:
        bundle = detectana.load(bundle_dir)
    except detectana.BundleError as exc:
        return refuse(f"cannot use the bundle {bundle_dir}: {exc}")

    aid = rf.analysis_id(bundle_dir, settings)
    analysis = folder / rf.ANALYSIS_DIR / aid
    done_rows = rf.read_scores(analysis / rf.SCORES)
    done = {r["frame"] for r in done_rows}
    old_progress = rf.read_json(analysis / rf.PROGRESS) or {}
    shared = {"state": "scoring", "scored_frames": 0, "scored_through_step": None, "alarms": [],
              **{k: old_progress[k] for k in ("scored_frames", "scored_through_step", "alarms") if k in old_progress}}

    n = int(dataset.getN())
    steps = _steps_of(dataset)
    dt = settings.get("frame_dt_ps")
    writer = AnalysisWriter(
        analysis, bundle, bundle_dir=bundle_dir, bundle_sha=bundle_sha256(bundle_dir),
        detectana_installed=detectana.__version__, settings=settings,
        persist_ps=float(settings.get("persist_ps") or DEFAULT_PERSIST_PS), event=event,
        positions_of=lambda i: np.asarray(dataset.getCoordinates(int(i)), dtype=np.float64),
        base_dir=folder, time_unit="ps", progress=shared, planned_frames=lambda: n,
        no_time_text=NO_TIME_EVENT)
    writer.identity = "unverified"          # a file never proves atom order
    writer.write_settings()
    if dt is None:
        writer.tracker = False              # alarms off; said once here, not again by the writer
        event("warning", text=NO_TIME_EVENT)
    writer.replay(done_rows)                # the alarm rule sees the frames scored before
    accepted = sum(1 for r in done_rows if "refused" not in r)

    def step_of(i):
        return int(steps[i]) if steps is not None else int(i)

    cancelled = False
    todo = [i for i in range(n) if i not in done]
    for start in range(0, len(todo), CHUNK):
        chunk = todo[start:start + CHUNK]
        good: list[_Frame] = []
        refused_rows: list[dict] = []
        for i in chunk:
            pos = np.asarray(dataset.getCoordinates(i), dtype=np.float64)
            try:
                check = bundle.validate_frame(_symbols(dataset, i), pos)
            except detectana.BundleError as exc:
                refused_rows.append({"segment": 0, "frame": i, "step": step_of(i),
                                     "time": None if dt is None else i * dt, "refused": str(exc)})
                continue
            if accepted == 0 and not good and not check.ok:
                # The first scorable frame: atom order cannot be proven for a file, so a
                # failed bond check means wrong order or an already damaged frame.
                return refuse("atom order unverified and the first frame fails the bond check: either "
                              "the atoms are in another order than the bundle's, or the first frame "
                              f"is already damaged ({check.summary})")
            good.append(_Frame(i, step_of(i), None if dt is None else i * dt, pos))
        if refused_rows:
            writer.append_rows(refused_rows)
        if good:
            writer.score(good)
            accepted += len(good)
        progress(len(done) + start + len(chunk), n)
        if stopped():
            cancelled = True
            break
    if accepted == 0 and not cancelled:     # stopped early: later frames may still be scorable
        return refuse("no frame of this dataset can be scored with this reference")
    final = "cancelled" if cancelled else "done"
    writer.write_progress(state=final)
    write_item_json(folder, state=final)
    event("scoring_" + final, scored_frames=writer.progress["scored_frames"])
    return folder


def prepare_item(dataset, bundle_dir, settings: dict, *, environ=None, name: str | None = None):
    """Make (or find) the item folder for this key, un-hidden, before scoring starts, so
    the item can appear at once. Returns ``(item id, folder, existed)``."""
    bundle_dir = Path(bundle_dir)
    fp = dataset.getFingerprint()
    iid = item_id(fp, bundle_dir, settings)
    folder = item_dir(iid, environ)
    old = rf.read_json(folder / ITEM)
    folder.mkdir(parents=True, exist_ok=True)
    if old:
        write_item_json(folder, hidden=False)
    else:
        write_item_json(folder, item_id=iid, parent=fp, parent_name=name or dataset.getName(),
                        bundle=str(bundle_dir), settings=settings, state="scoring", hidden=False,
                        created_utc=rf.utc_now())
    return iid, folder, bool(old)


def read_item(folder: Path) -> dict | None:
    return rf.read_json(Path(folder) / ITEM)


def find_items(parent_fp: str, environ=None) -> list[Path]:
    """Item folders made for this parent that are not hidden."""
    d = analyses_dir(environ)
    out = []
    for p in sorted(d.iterdir()) if d.is_dir() else []:
        item = read_item(p)
        if item and item.get("parent") == parent_fp and not item.get("hidden"):
            out.append(p)
    return out


def set_hidden(folder: Path, hidden: bool) -> None:
    write_item_json(Path(folder), hidden=bool(hidden))


def all_items(parent_fp: str, environ=None) -> list[dict]:
    """Every item made for this parent, hidden ones included: what the dialog compares with."""
    d = analyses_dir(environ)
    out = []
    for p in sorted(d.iterdir()) if d.is_dir() else []:
        item = read_item(p)
        if item and item.get("parent") == parent_fp:
            out.append({"item_id": item.get("item_id"), "bundle": item.get("bundle"),
                        "settings": item.get("settings"), "state": item.get("state"),
                        "hidden": bool(item.get("hidden"))})
    return out


def dialog_info(dataset, environ=None, quick: bool = False) -> dict:
    """What the Score dialog shows about ``dataset`` (reads only). ``quick``: only whether
    DetectAna is installed, without importing it (an import takes seconds and would
    compete with the views the window is opening)."""
    from importlib.util import find_spec

    from ffast.live import references
    from ffast.live.detectana_api import INSTALL_HINT, DetectAnaMissing, require_detectana

    if quick:
        found = find_spec("detectana") is not None
        return {"fingerprint": dataset.getFingerprint(), "quick": True,
                "detectana": None if found else f"DetectAna is not installed; {INSTALL_HINT}"}
    try:
        require_detectana()
        missing = None
    except DetectAnaMissing as exc:
        missing = str(exc)
    fp = dataset.getFingerprint()
    return {"fingerprint": fp, "name": dataset.getName(), "n_frames": int(dataset.getN()),
            "references": references.recorded(), "detectana": missing,
            "frame_dt_fs": suggest_frame_dt_fs(dataset), "refusal": check_dataset(dataset),
            "persist_ps": DEFAULT_PERSIST_PS, "existing": all_items(fp, environ)}
