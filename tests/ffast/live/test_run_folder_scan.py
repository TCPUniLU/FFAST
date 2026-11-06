"""Finding a run's job script, input, data file and reference in its folder."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ffast.live import run_folder_scan as rfs
from ffast.live.references import record_reference

INPUTS = Path(__file__).parent / "fixtures" / "run_with_ffast" / "inputs"
JOB = "#!/bin/bash -l\n#SBATCH --ntasks=1\nmodule load LAMMPS/x\nsrun lmp -in {inp}\n"


def _bundle(path: Path, types=("C", "H", "O"), n=21) -> Path:
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({
        "created_utc": "2026-11-06T10:00:00+00:00",
        "molecule": {"n_atoms": n, "elements": ["C"] * 9 + ["H"] * 8 + ["O"] * 4}}))
    path.with_suffix(".lmp").write_text(f"print '''\nelements {' '.join(types)}\nend'''\n")
    return path


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("FFAST_REFERENCES", str(tmp_path / "refs"))
    monkeypatch.setenv("FFAST_RUN_WITH_FFAST", str(tmp_path / "memory.json"))
    d = tmp_path / "proj" / "run1"
    d.mkdir(parents=True)
    shutil.copy(INPUTS / "minimize.in", d / "in.plain")
    (d / "job.sh").write_text(JOB.format(inp="in.plain"))
    (d / "notes.txt").write_text("not a job\n")
    return d


def test_one_job_script_gives_input_data_and_element_order(run):
    out = rfs.scan(str(run))
    assert out["problem"] is None
    assert [j["name"] for j in out["jobs"]] == ["job.sh"]
    assert out["job"] == str(run / "job.sh") and out["input"] == str(run / "in.plain")
    assert out["data"] == str(run / "aspirin.data")
    assert out["pair_elements"] == ["C", "H", "O"]
    assert out["jobs"][0]["launch"] == "srun lmp -in in.plain" and out["jobs"][0]["line"] == 4


def test_copies_are_skipped_and_several_jobs_are_listed(run):
    (run / "job.ffast.sh").write_text(JOB.format(inp="in.ffast.plain"))
    shutil.copy(INPUTS / "simple.in", run / "in.long")
    (run / "long.sh").write_text(JOB.format(inp="in.long"))
    out = rfs.scan(str(run))
    assert [j["name"] for j in out["jobs"]] == ["job.sh", "long.sh"]
    chosen = rfs.scan(str(run), job=str(run / "long.sh"))
    assert chosen["input"] == str(run / "in.long") and chosen["data"] == str(run / "aspirin.data")


def test_a_job_whose_input_is_missing_says_so(run):
    (run / "job.sh").write_text(JOB.format(inp="gone.in"))
    out = rfs.scan(str(run))
    assert out["job"] is None and "gone.in, which does not exist" in out["problem"]


def test_no_job_script_here(run):
    out = rfs.scan(str(run.parent))
    assert out["jobs"] == [] and out["problem"].startswith("No job script here")


def test_references_in_order_and_the_matching_one_is_preselected(run):
    here = _bundle(run / "here.bundle", types=("O", "H", "C"))        # wrong type order for this input
    above = _bundle(run.parent / "above.bundle")
    made = _bundle(run.parent.parent / "refs-made" / "made.bundle")
    record_reference(made)
    out = rfs.scan(str(run))
    rows = [(Path(r["bundle"]).name, r["where"], r["match"]) for r in out["references"]]
    assert rows == [("here.bundle", "in this folder", False), ("above.bundle", "in the folder above", True),
                    ("made.bundle", "made by FFAST", True)]
    assert out["reference"] == str(above.resolve())
    assert out["references"][1]["formula"] == "C9H8O4" and out["references"][1]["n_atoms"] == 21


def test_what_was_used_last_comes_first_and_an_empty_folder_means_the_last_one(run, tmp_path):
    _bundle(run.parent / "above.bundle")
    other = _bundle(tmp_path / "elsewhere" / "toy.bundle")
    shutil.copy(INPUTS / "simple.in", run / "in.long")
    (run / "long.sh").write_text(JOB.format(inp="in.long"))
    rfs.remember(run, run / "long.sh", other)
    out = rfs.scan("")
    assert out["folder"] == str(run.resolve())
    assert out["job"] == str(run / "long.sh") and out["input"] == str(run / "in.long")
    assert out["references"][0]["where"] == "used last time with this folder"
    assert out["reference"] == str(other.resolve())


def test_no_folder_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("FFAST_RUN_WITH_FFAST", str(tmp_path / "memory.json"))
    assert rfs.scan(None)["problem"].startswith("Pick the folder")


def test_writing_the_copies_remembers_the_folder(run, tmp_path):
    from tests.ffast.live.bundle_helpers import make_bundle
    from tests.ffast.live.test_lammps_check import HELP, FakeShell, _run_output
    from ffast.live import lammps_check, run_with_ffast

    (tmp_path / "ref").mkdir()
    bundle, _ = make_bundle(tmp_path / "ref")
    lammps_check._CACHE.clear()
    out = run_with_ffast.prepare(str(run / "in.plain"), str(run / "job.sh"), str(bundle),
                                 runner=FakeShell((0, HELP), (0, _run_output("done"))))
    assert out["ok"], out.get("refusal")
    run_with_ffast.write(str(run / "in.plain"), str(run / "job.sh"), str(bundle), out["originals"],
                         runner=FakeShell((0, HELP), (0, _run_output("done"))))
    memory = rfs.read_memory()
    assert memory["last_folder"] == str(run.resolve())
    assert memory["folders"][str(run.resolve())]["reference"] == str(bundle.resolve())
    # the copies written there are not offered as job scripts next time
    assert [j["name"] for j in rfs.scan(str(run))["jobs"]] == ["job.sh"]


def test_the_server_answers_a_scan(run):
    import asyncio
    import types

    from ffast.protocol import control
    from ffast.protocol.rpc import unpack
    from ffast.session.server_session import ServerSession

    queue = asyncio.Queue()
    session = ServerSession(types.SimpleNamespace(), queue)
    asyncio.run(session.dispatch(control.SCAN_RUN_FOLDER, (), {"folder": str(run)}))
    event, _, kw = unpack(queue.get_nowait())
    assert event == control.RUN_FOLDER_SCANNED and kw["job"] == str(run / "job.sh") and kw["asked"] == str(run)
