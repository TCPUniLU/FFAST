"""Prepare and write the "Run with FFAST" copies on the server (ADR 0086, plan step 5).

``prepare`` reads the picked input script, job script and reference bundle,
checks the LAMMPS module (``lammps_check``), places the include
(``lammps_input``), wraps the launch (``job_script``) and returns the checks,
the copies as unified diffs and the sha256 of each original. Nothing is
written. ``write`` does the same again, refuses if an original changed since
the review (P12), and writes the copies next to the originals (R4): never over
a file without FFAST's marker. The originals are never opened for writing.

Plan: ``docs/implementation/2026-11-06-run-with-ffast.md``.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import stat
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ffast.live import job_script as js
from ffast.live.lammps_check import check_lammps, check_pair_styles
from ffast.live.lammps_input import Check, Refusal, plan_input, read_commands

MARKER = "Written by Run with FFAST"
DATA_MARKERS = (MARKER, "written by ffast lammps data")
ELEMENTS_RE = re.compile(r"^elements\s+(.+?)\s*$", re.M)


class Refused(Exception):
    """A user-facing refusal; ``file`` says which picked file it is about."""

    def __init__(self, refusal: Refusal, file: str):
        super().__init__(refusal.text())
        self.refusal = refusal
        self.file = file


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def marker_line(original: Path) -> str:
    # The date only: the copy written after the review then equals the diff shown.
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"# {MARKER} {when} from {original}; the original is unchanged."


def include_of(bundle: str) -> tuple[Path, Path]:
    """(bundle dir, include file) for a picked ``.bundle`` folder or ``.lmp`` file."""
    from ffast.live.reference import bundle_paths

    return bundle_paths(bundle)


def include_elements(include: Path) -> list[str]:
    m = ELEMENTS_RE.search(include.read_text())
    if not m:
        raise Refused(Refusal(f"{include} names no `elements` line; it was not written by FFAST", None,
                              "make the reference again with `ffast bundle` or Make reference"), "bundle")
    return m.group(1).split()


def _no_space(path: Path, what: str, file: str) -> None:
    if any(ch.isspace() for ch in str(path)):
        raise Refused(Refusal(f"the {what} path {path} contains a space; LAMMPS cannot use it", None,
                              "move it to a folder whose path has no spaces"), file)


def _has_marker(path: Path, markers=(MARKER,)) -> bool:
    try:
        head = path.read_text(errors="replace").splitlines()[:4]
    except OSError:
        return False
    return any(m in line for line in head for m in markers)


def _diff(original: Path, copy: Path, old: str, new: str) -> dict:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                      fromfile=f"{original} (original, unchanged)",
                                      tofile=f"{copy} (copy)", lineterm="", n=3))
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return {"diff": "\n".join(lines), "added": added, "removed": removed}


def _checks(items: list[Check]) -> list[dict]:
    return [asdict(c) for c in items]


def _data_frame(bundle_dir: Path, source: str, path: str | None, index: int):
    """(positions, symbols, source label) of the frame the data file is written from (R5)."""
    from ffast.live.detectana_api import require_detectana

    detectana = require_detectana()
    bundle = detectana.load(bundle_dir)
    if source == "bundle":
        import numpy as np

        return np.asarray(bundle.reference_positions, dtype=float), list(bundle.elements), f"bundle:{bundle_dir}"
    from ase.io import read

    frames = read(os.path.expanduser(path), index=":")
    if not -len(frames) <= index < len(frames):
        raise Refused(Refusal(f"{path} has {len(frames)} frames; frame {index} is outside", None,
                              "pick another frame"), "data")
    atoms = frames[index]
    symbols = atoms.get_chemical_symbols()
    if tuple(symbols) != tuple(bundle.elements):
        raise Refused(Refusal(f"{path} frame {index} does not list its atoms in the reference's order", None,
                              "pick a frame of the reference data, or the reference's own geometry"), "data")
    return atoms.get_positions().astype(float), symbols, f"{Path(path).expanduser().resolve()}@{index}"


def prepare(input_path: str, job_path: str, bundle: str, *, data: str = "none",
            data_path: str | None = None, data_index: int = 0, stop_on_alarm: bool = False,
            runner=None) -> dict:
    """Checks, copies and diffs for the review step; writes nothing."""
    try:
        return _prepare(input_path, job_path, bundle, data, data_path, data_index, stop_on_alarm, runner)
    except Refused as exc:
        r = exc.refusal
        return {"ok": False, "refusal": {"file": exc.file, "reason": r.reason, "line": r.line,
                                         "hand_edit": r.hand_edit, "text": r.text()},
                "sections": getattr(exc, "sections", {})}


def _prepare(input_path, job_path, bundle, data, data_path, data_index, stop_on_alarm, runner, *, build=False):
    inp = Path(os.path.expanduser(input_path)).resolve()
    job = Path(os.path.expanduser(job_path)).resolve()
    sections: dict[str, list] = {"module": [], "input": [], "job": []}

    def refuse(refusal, file):
        exc = Refused(refusal, file)
        exc.sections = {k: _checks(v) for k, v in sections.items()}
        raise exc

    for p, what in ((inp, "input script"), (job, "job script")):
        if not p.is_file():
            refuse(Refusal(f"the {what} {p} does not exist", None, f"pick the {what} again"), what.split()[0])
    bundle_dir, include = include_of(bundle)
    if not include.is_file():
        refuse(Refusal(f"no include file {include} next to the reference", None,
                       "make the reference with `ffast bundle` or Make reference"), "bundle")
    _no_space(include, "include file", "bundle")
    elements = include_elements(include)
    input_text, job_text = inp.read_text(), job.read_text()

    # Job script: the launch and the modules.
    launch = js.find_launch(job_text, str(inp), str(job.parent))
    if isinstance(launch, Refusal):
        refuse(launch, "job")
    sections["job"].append(Check("ok", f"one LAMMPS launch (line {launch.first}): {launch.text.strip()}", launch.first))
    sections["job"].extend(Check("warn", w, launch.first) for w in launch.warnings)
    if launch.wrapped:
        sections["job"].append(Check("ok", "the launch already runs through ffast-lammps"))
    wrapper = Path("~/.ffast/bin/ffast-lammps").expanduser()
    if wrapper.exists():
        sections["job"].append(Check("ok", f"ffast-lammps is installed on the cluster ({wrapper})"))
    else:
        sections["job"].append(Check("warn", f"{wrapper} not found: connect once with this FFAST so the "
                                             "cluster setup installs it"))
    if any(ch.isspace() for ch in str(job.parent)):
        sections["job"].append(Check("warn", "the job folder has a space in its path; run folders go to "
                                             "$FFAST_RUN_DIR, which must then be set to a path without spaces"))

    # Data file (R5, P6).
    data_copy = None
    if data != "none":
        # Named after the data file it replaces (aspirin.data → aspirin.ffast.data),
        # in the input's folder; the input's own name when read_data names none.
        reads = [c for c in read_commands(input_text) if c.name == "read_data" and c.args]
        name = Path(reads[0].args[0]).name if reads and "$" not in reads[0].args[0] else f"{inp.name}.data"
        data_copy = inp.parent / js.copy_name(name)
        _no_space(data_copy, "data file", "data")

    # Input placement, before the module check: the check needs to know whether a snapshot is used.
    plan = plan_input(input_text, include_path=str(include), elements=elements,
                      data_file=str(data_copy) if data_copy else None, stop_on_alarm=stop_on_alarm)
    if plan.refusal is not None:
        refuse(plan.refusal, "input")

    # LAMMPS module (R6).
    modules = js.module_lines(job_text)
    module = js.lammps_module(modules)
    lmp = check_lammps(modules, module, launch.executable, runner=runner)
    module_checks, why = lmp.checks(needs_snapshot=plan.snapshot_after is not None)
    sections["module"].extend(module_checks)
    if why:
        refuse(Refusal(why, None, "load a LAMMPS module that runs FFAST's commands, or watch this run "
                                  "by hand (include and ffast-lammps)"), "module")
    pair_checks, why = check_pair_styles(plan.pair_styles, plan.hybrid, plan.plugins, lmp)
    sections["module"].extend(pair_checks)
    if why:
        refuse(Refusal(why, None, "load the LAMMPS module that has this pair style"), "module")
    if not lmp.reset_after_dump_ok:
        plan = plan_input(input_text, include_path=str(include), elements=elements,
                          data_file=str(data_copy) if data_copy else None, stop_on_alarm=stop_on_alarm,
                          reset_after_dump_ok=False)
        if plan.refusal is not None:
            refuse(plan.refusal, "input")
    sections["input"].extend(plan.checks)

    # The copies.
    input_copy = inp.with_name(js.copy_name(inp.name))
    job_copy = job.with_name(js.copy_name(job.name))
    for path in (input_copy, job_copy):
        if path.exists() and not _has_marker(path):
            refuse(Refusal(f"{path} exists and was not written by Run with FFAST", None,
                           "rename or move that file; FFAST never overwrites a file it did not write"),
                   "input" if path == input_copy else "job")
    if data_copy is not None and data_copy.exists() and not _has_marker(data_copy, DATA_MARKERS):
        refuse(Refusal(f"{data_copy} exists and was not written by FFAST", None,
                       "rename or move that file"), "data")
    new_input = plan.apply(input_text, header=[marker_line(inp)])
    input_name = launch.input_token
    copy_token = (str(input_copy) if os.path.isabs(input_name)
                  else os.path.join(os.path.dirname(input_name), input_copy.name))
    new_job = js.wrap(job_text, launch, copy_token, marker_line(job))

    out = {
        "ok": True,
        "refusal": None,
        "sections": {k: _checks(v) for k, v in sections.items()},
        "elements": elements,
        "include": str(include),
        "copies": [
            {"kind": "input", "original": str(inp), "copy": str(input_copy), **_diff(inp, input_copy, input_text, new_input)},
            {"kind": "job", "original": str(job), "copy": str(job_copy), **_diff(job, job_copy, job_text, new_job)},
        ],
        "originals": {str(inp): sha256_file(inp), str(job): sha256_file(job)},
        "submit": {"folder": str(job.parent), "script": job_copy.name},
        "lammps": {"module": module, "version": lmp.version, "executable": launch.executable},
    }
    texts = {str(input_copy): new_input, str(job_copy): new_job}
    if data_copy is not None:
        from ffast.live.data_file import data_file_text

        positions, symbols, label = _data_frame(bundle_dir, data, data_path, data_index)
        text = data_file_text(positions, symbols, elements,
                              title=f"written by ffast lammps data ({MARKER}) from {label} in reference order")
        out["copies"].append({"kind": "data", "original": None, "copy": str(data_copy),
                              "diff": "", "added": len(text.splitlines()), "removed": 0,
                              "atoms": len(symbols), "source": label})
        texts[str(data_copy)] = text
        if data == "file" and data_path:
            src = Path(os.path.expanduser(data_path)).resolve()
            out["originals"][str(src)] = sha256_file(src)
        if build:
            out["_data"] = (positions, label)
    if build:
        out["_texts"] = texts
        out["_bundle_dir"] = bundle_dir
        out["_job_mode"] = stat.S_IMODE(job.stat().st_mode)
    return out


def write(input_path: str, job_path: str, bundle: str, originals: dict[str, str], *, data: str = "none",
          data_path: str | None = None, data_index: int = 0, stop_on_alarm: bool = False, runner=None) -> dict:
    """Write the copies (and the data file) after the user approved the diff."""
    try:
        out = _prepare(input_path, job_path, bundle, data, data_path, data_index, stop_on_alarm, runner, build=True)
    except Refused as exc:
        return prepare(input_path, job_path, bundle, data=data, data_path=data_path,
                       data_index=data_index, stop_on_alarm=stop_on_alarm, runner=runner) | {"ok": False}
    changed = [p for p, sha in out["originals"].items() if originals.get(p) != sha]
    if changed:
        names = ", ".join(Path(p).name for p in changed)
        return {"ok": False, "refusal": {"file": "review", "reason": f"{names} changed since the review",
                                         "line": None, "hand_edit": "check again", "text":
                                         f"{names} changed since the review; check again"}}
    texts = out.pop("_texts")
    written = []
    for path, text in texts.items():
        p = Path(path)
        tmp = p.with_name(p.name + ".ffast-tmp")
        tmp.write_text(text)
        os.replace(tmp, p)
        written.append(path)
    job_copy = Path(out["copies"][1]["copy"])
    os.chmod(job_copy, out.pop("_job_mode") | stat.S_IXUSR)
    if "_data" in out:
        from ffast.live.data_file import bundle_sha256, record_data_file

        positions, label = out.pop("_data")
        data_copy = Path(out["copies"][2]["copy"])
        record_data_file(positions, bundle_sha256=bundle_sha256(out["_bundle_dir"]), source=label,
                         data_file=data_copy)
    out.pop("_bundle_dir", None)
    out["written"] = written
    return out
