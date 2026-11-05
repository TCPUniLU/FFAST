"""The run folder of a live LAMMPS run (ADR 0087).

One folder per ``ffast-lammps`` invocation, never reused::

    <run id>/
        frames.lammpstrj          LAMMPS dump (geometry, step, time, units)
        log.lammps                LAMMPS log
        include.txt               written by the include file: bundle, interval
        events.jsonl              one event per line
        run.json                  run state, written by the wrapper only
        producer_done.json        written by the wrapper when LAMMPS has exited
        scorer.log                the scorer's own log
        analysis/<analysis id>/
            settings.json         bundle and settings this analysis used
            scores.jsonl          one row per scored frame, append-only
            progress.json         scorer state and heartbeat

Standard library only, and Python 3.6 compatible: the wrapper imports this
module under whatever ``python3`` the job script has.
"""

import datetime
import hashlib
import json
import os
import random
import socket
from pathlib import Path

RUN_FORMAT = "ffast-live-run"
RUN_FORMAT_VERSION = 1

FRAMES = "frames.lammpstrj"
LOG = "log.lammps"
INCLUDE_INFO = "include.txt"
EVENTS = "events.jsonl"
RUN_JSON = "run.json"
PRODUCER_DONE = "producer_done.json"
SCORER_LOG = "scorer.log"
ANALYSIS_DIR = "analysis"
SETTINGS = "settings.json"
SCORES = "scores.jsonl"
PROGRESS = "progress.json"

RUN_DIR_ENV = "FFAST_RUN_DIR"


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def run_base(environ, cwd):
    """Folder that holds run folders: ``$FFAST_RUN_DIR``, else ``<cwd>/ffast-runs``.

    Refuses a path with a space: LAMMPS cannot write a dump there.
    """
    base = environ.get(RUN_DIR_ENV) or os.path.join(cwd, "ffast-runs")
    base = os.path.abspath(os.path.expanduser(base))
    if any(ch.isspace() for ch in base):
        raise ValueError(
            "run folders would go to %r, which contains a space; LAMMPS cannot write "
            "a dump there. Set %s to a folder without spaces." % (base, RUN_DIR_ENV)
        )
    return Path(base)


def new_run_id():
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    host = socket.gethostname().split(".")[0] or "host"
    return "%s-%s-%d-%04x" % (now, host, os.getpid(), random.getrandbits(16))


def claim_run_folder(base):
    """Create a fresh run folder under ``base``; return (run id, path)."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        run_id = new_run_id()
        path = base / run_id
        try:
            os.mkdir(str(path))
        except FileExistsError:
            continue
        return run_id, path
    raise RuntimeError("could not claim a fresh run folder in %s" % base)


def write_json_atomic(path, obj):
    """Write ``obj`` to ``path`` through a temporary file and a rename."""
    path = Path(path)
    tmp = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    with open(str(tmp), "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))


def read_json(path):
    """The JSON in ``path``, or None when it is missing or not (yet) valid."""
    try:
        with open(str(path)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _complete_lines(data):
    """Parsed JSON objects of the complete lines in ``data``; a torn last line is skipped."""
    rows = []
    for line in data.split("\n")[:-1]:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def append_event(run_dir, kind, **fields):
    """Append one event line to ``events.jsonl``."""
    event = {"t": utc_now(), "kind": kind}
    event.update(fields)
    with open(str(Path(run_dir) / EVENTS), "a") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def read_events(run_dir):
    try:
        with open(str(Path(run_dir) / EVENTS)) as fh:
            return _complete_lines(fh.read())
    except OSError:
        return []


def analysis_id(bundle_dir, settings):
    """16 hex characters naming one bundle with one set of settings."""
    h = hashlib.sha256()
    for name in ("manifest.json", "state.npz"):
        with open(str(Path(bundle_dir) / name), "rb") as fh:
            h.update(hashlib.sha256(fh.read()).digest())
    h.update(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode())
    return h.hexdigest()[:16]


class ScoresWriter:
    """Append score rows to ``scores.jsonl``, one write per batch."""

    def __init__(self, path):
        self.path = Path(path)

    def append(self, rows):
        if not rows:
            return
        text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        with open(str(self.path), "a") as fh:
            fh.write(text)
            fh.flush()


class ScoresTail:
    """Read new complete rows of ``scores.jsonl``; the first row per (segment, frame) wins."""

    def __init__(self, path):
        self.path = Path(path)
        self._offset = 0
        self._seen = set()

    def poll(self):
        try:
            with open(str(self.path), "rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self._offset += end + 1
        out = []
        for row in _complete_lines(data[:end + 1].decode("utf-8", "replace")):
            key = (row.get("segment"), row.get("frame"))
            if key in self._seen:
                continue
            self._seen.add(key)
            out.append(row)
        return out


def read_scores(path):
    return ScoresTail(path).poll()


def read_include_info(run_dir):
    """``include.txt`` as a dict, or None until the include has written all of it."""
    try:
        with open(str(Path(run_dir) / INCLUDE_INFO)) as fh:
            lines = fh.read().split("\n")
    except OSError:
        return None
    if "end" not in (line.strip() for line in lines):
        return None
    info = {}
    for line in lines:
        line = line.strip()
        if not line or line == "end":
            continue
        key, _, value = line.partition(" ")
        info[key] = value.strip()
    return info
