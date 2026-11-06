"""Find a run's job script, input, data file and reference in its folder.

"Run with FFAST" asks for one run folder instead of three paths (plan §7). This
module reads the folder (it runs nothing) and says what it found and how:

- job scripts: files with ``#SBATCH`` in their first 4 KB and exactly one
  LAMMPS launch; ``*.ffast.*`` copies are skipped;
- the input each one runs (its launch line) and the data file that input reads;
- references: the one used last time with this folder, ``.bundle`` folders here
  and one folder up, then the ones FFAST made (newest first); the first whose
  LAMMPS type order equals the input's ``pair_coeff * * … <elements>`` is
  preselected.

What was used with each folder is remembered on the cluster, in
``~/.ffast/run-with-ffast.json`` (``$FFAST_RUN_WITH_FFAST``), when the copies
are written.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ffast.live import job_script as js
from ffast.live.lammps_input import read_commands
from ffast.live.references import describe, recorded

ENV = "FFAST_RUN_WITH_FFAST"
DEFAULT = "~/.ffast/run-with-ffast.json"
MAX_JOB_BYTES = 1 << 20
HEAD_BYTES = 4096
ELEMENT_RE = re.compile(r"^(?:[A-Z][a-z]?|NULL)$")


# ── memory ──────────────────────────────────────────────────────────────────

def memory_path(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(os.path.expanduser(environ.get(ENV) or DEFAULT))


def read_memory(environ=None) -> dict:
    try:
        data = json.loads(memory_path(environ).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(folder, job, reference, environ=None) -> None:
    """Remember what was used with ``folder``; never fails the caller."""
    data = read_memory(environ)
    folders = data.get("folders") if isinstance(data.get("folders"), dict) else {}
    folders[str(folder)] = {"job": str(job), "reference": str(reference),
                            "utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    data.update(folders=folders, last_folder=str(folder))
    path = memory_path(environ)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, path)
    except OSError:
        pass


# ── finding things ──────────────────────────────────────────────────────────

def _job_scripts(folder: Path) -> list[dict]:
    out = []
    try:
        files = sorted(p for p in folder.iterdir() if p.is_file())
    except OSError:
        return out
    for p in files:
        if ".ffast." in p.name or p.name.endswith(".ffast"):
            continue
        try:
            if p.stat().st_size > MAX_JOB_BYTES:
                continue
            with open(p, "rb") as fh:
                head = fh.read(HEAD_BYTES)
            if b"#SBATCH" not in head:
                continue
            text = p.read_text(errors="replace")
        except OSError:
            continue
        launch = js.find_launch(text, None, str(folder))
        row = {"path": str(p), "name": p.name, "input": None, "launch": None, "line": None, "problem": None}
        if isinstance(launch, js.Launch):
            row.update(input=launch.input_path, launch=launch.text.strip(), line=launch.first)
            if not Path(launch.input_path).is_file():
                row["problem"] = f"runs {launch.input_token}, which does not exist"
        else:
            row["problem"] = launch.reason
        out.append(row)
    return out


def _input_facts(input_path: str) -> tuple[str | None, list[str] | None]:
    """(data file, pair_coeff element order) of an input; None where it names none."""
    try:
        commands = read_commands(Path(input_path).read_text(errors="replace"))
    except OSError:
        return None, None
    data = None
    for c in commands:
        if c.name == "read_data" and c.args and "$" not in c.args[0]:
            p = c.args[0]
            data = p if os.path.isabs(p) else os.path.join(os.path.dirname(input_path), p)
            break
    elements = None
    for c in commands:
        if c.name == "pair_coeff" and c.args[:2] == ["*", "*"]:
            tail = []
            for w in reversed(c.args[2:]):
                if not ELEMENT_RE.match(w):
                    break
                tail.insert(0, w)
            if tail:
                elements = tail
    return data, elements


def _matches(ref: dict, elements: list[str] | None) -> bool | None:
    if not ref.get("types"):
        return False
    if elements is None:
        return None
    return len(elements) == len(ref["types"]) and all(e in ("NULL", t) for e, t in zip(elements, ref["types"]))


def _references(folder: Path, last: str | None, elements: list[str] | None, environ=None) -> list[dict]:
    rows, seen = [], set()

    def add(path, where):
        key = str(Path(path).expanduser().resolve())
        if key in seen or not Path(key).is_dir():
            return
        seen.add(key)
        row = describe(key)
        row.update(where=where, match=_matches(row, elements))
        rows.append(row)

    if last:
        add(last, "used last time with this folder")
    for d, where in ((folder, "in this folder"), (folder.parent, "in the folder above")):
        try:
            for p in sorted(d.glob("*.bundle")):
                add(p, where)
        except OSError:
            pass
    for r in recorded(environ):
        add(r["bundle"], "made by FFAST")
    return rows


def scan(folder: str | None, job: str | None = None, environ=None) -> dict:
    """What "Run with FFAST" would use from ``folder`` (the last folder used when
    empty); ``job``: the job script the user chose among several."""
    memory = read_memory(environ)
    if not folder:
        folder = memory.get("last_folder")
        if not folder:
            return {"folder": None, "problem": "Pick the folder that holds your job script."}
    path = Path(os.path.expanduser(folder)).resolve()
    out = {"folder": str(path), "problem": None, "jobs": [], "job": None, "input": None, "data": None,
           "pair_elements": None, "references": [], "reference": None}
    if not path.is_dir():
        out["problem"] = f"{path} is not a folder"
        return out
    out["jobs"] = _job_scripts(path)
    usable = [j for j in out["jobs"] if not j["problem"]]
    if not out["jobs"]:
        out["problem"] = (f"No job script here: {path} has no file with #SBATCH lines that starts LAMMPS. "
                          "Pick the folder that holds your job script.")
    elif not usable:
        out["problem"] = f"{out['jobs'][0]['name']}: {out['jobs'][0]['problem']}"
    before = (memory.get("folders") or {}).get(str(path)) or {}
    wanted = job or before.get("job")
    job = next((j for j in usable if j["path"] == wanted), usable[0] if usable else None)
    if job:
        out.update(job=job["path"], input=job["input"])
        out["data"], out["pair_elements"] = _input_facts(job["input"])
    out["references"] = _references(path, before.get("reference"), out["pair_elements"], environ)
    choice = next((r for r in out["references"] if r["match"] is not False and not r["problem"]), None)
    out["reference"] = choice["bundle"] if choice else None
    return out
