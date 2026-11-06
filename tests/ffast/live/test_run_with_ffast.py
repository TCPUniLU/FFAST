"""Prepare and write the "Run with FFAST" copies on a fake cluster folder."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from ffast.live import run_with_ffast as rwf
from ffast.live.lammps_input import read_commands
from tests.ffast.live.bundle_helpers import INITIAL, make_bundle
from tests.ffast.live.test_lammps_check import HELP, FakeShell, _run_output

INPUTS = Path(__file__).parent / "fixtures" / "run_with_ffast" / "inputs"
JOB = """#!/bin/bash -l
#SBATCH --ntasks=1
module load env/release/2023.1
module load LAMMPS/2Aug2023-foss-2023a-kokkos
cd $SLURM_SUBMIT_DIR
srun lmp -in run.in
"""


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp("ref"))


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("FFAST_LAMMPS_DATA_RECORDS", str(tmp_path / "records"))
    d = tmp_path / "aspirin"
    d.mkdir()
    shutil.copy(INPUTS / "minimize.in", d / "run.in")
    (d / "job.sh").write_text(JOB)
    os.chmod(d / "job.sh", 0o750)
    return d


def _shell(reached="done", error=None):
    return FakeShell((0, HELP), (0 if reached == "done" else 1, _run_output(reached, error)))


def _prepare(work, bundle, **kw):
    from ffast.live import lammps_check

    kw.setdefault("runner", _shell())
    lammps_check._CACHE.clear()
    return rwf.prepare(str(work / "run.in"), str(work / "job.sh"), str(bundle[0]), **kw)


def test_prepare_shows_both_copies_and_writes_nothing(work, bundle):
    before = sorted(p.name for p in work.iterdir())
    out = _prepare(work, bundle)
    assert out["ok"], out.get("refusal")
    assert sorted(p.name for p in work.iterdir()) == before
    kinds = {c["kind"]: c for c in out["copies"]}
    assert kinds["input"]["copy"] == str(work / "run.ffast.in")
    assert f"+include {bundle[1]}" in kinds["input"]["diff"]
    assert "+if \"${ffast_on} == 1\" then \"write_dump" in kinds["input"]["diff"]   # minimize → snapshot
    assert "+$HOME/.ffast/bin/ffast-lammps srun lmp -in run.ffast.in" in kinds["job"]["diff"]
    assert "-srun lmp -in run.in" in kinds["job"]["diff"]
    assert out["submit"] == {"folder": str(work), "script": "job.ffast.sh"}
    assert out["elements"] == ["C", "H", "O"]
    texts = [c["text"] for s in out["sections"].values() for c in s]
    assert any("LAMMPS/2Aug2023" in t for t in texts)
    assert any("pair_style mace" in t for t in texts)


def test_write_makes_copies_and_never_touches_the_originals(work, bundle):
    shas = {p: rwf.sha256_file(work / p) for p in ("run.in", "job.sh")}
    out = _prepare(work, bundle)
    res = rwf.write(str(work / "run.in"), str(work / "job.sh"), str(bundle[0]), out["originals"], runner=_shell())
    assert res["ok"], res.get("refusal")
    assert {p: rwf.sha256_file(work / p) for p in ("run.in", "job.sh")} == shas
    copy = (work / "run.ffast.in").read_text()
    assert copy.splitlines()[0].startswith("# " + rwf.MARKER)
    names = [c.name for c in read_commands(copy)]
    assert names.count("include") == 1
    job = (work / "job.ffast.sh").read_text().splitlines()
    assert job[0] == "#!/bin/bash -l" and rwf.MARKER in job[1]
    assert os.stat(work / "job.ffast.sh").st_mode & stat.S_IXUSR
    # the written copy is what the review showed
    again = _prepare(work, bundle)
    assert all(c["added"] == p["added"] for c, p in zip(again["copies"], out["copies"]))


def test_write_refuses_an_original_changed_since_the_review(work, bundle):
    out = _prepare(work, bundle)
    (work / "job.sh").write_text(JOB + "echo changed\n")
    res = rwf.write(str(work / "run.in"), str(work / "job.sh"), str(bundle[0]), out["originals"], runner=_shell())
    assert not res["ok"] and "job.sh changed since the review" in res["refusal"]["text"]
    assert not (work / "run.ffast.in").exists()


def test_a_file_without_the_marker_is_never_overwritten(work, bundle):
    (work / "run.ffast.in").write_text("my own file\n")
    out = _prepare(work, bundle)
    assert not out["ok"] and "not written by Run with FFAST" in out["refusal"]["reason"]
    assert (work / "run.ffast.in").read_text() == "my own file\n"


def test_an_earlier_ffast_copy_is_replaced(work, bundle):
    (work / "run.ffast.in").write_text(f"# {rwf.MARKER} long ago\nold\n")
    out = _prepare(work, bundle)
    assert out["ok"]
    rwf.write(str(work / "run.in"), str(work / "job.sh"), str(bundle[0]), out["originals"], runner=_shell())
    assert "old" not in (work / "run.ffast.in").read_text().splitlines()


def test_data_file_from_the_bundle_geometry_is_written_and_recorded(work, bundle):
    import detectana

    from ffast.live.data_file import find_record

    out = _prepare(work, bundle, data="bundle")
    assert out["ok"], out.get("refusal")
    data = next(c for c in out["copies"] if c["kind"] == "data")
    assert data["copy"] == str(work / "run.ffast.data") and data["atoms"] == 21
    assert f"+read_data {work / 'run.ffast.data'}" in out["copies"][0]["diff"]
    rwf.write(str(work / "run.in"), str(work / "job.sh"), str(bundle[0]), out["originals"],
              data="bundle", runner=_shell())
    assert "written by ffast lammps data" in (work / "run.ffast.data").read_text().splitlines()[0]
    rec = find_record(detectana.load(bundle[0]).reference_positions)
    assert rec is not None and rec["data_file"] == str(work / "run.ffast.data")


def test_data_file_from_a_structure_file_frame(work, bundle):
    out = _prepare(work, bundle, data="file", data_path=str(INITIAL), data_index=0)
    assert out["ok"], out.get("refusal")
    assert str(INITIAL.resolve()) in out["originals"]


def test_a_refused_input_names_the_file_and_the_hand_edit(work, bundle):
    shutil.copy(INPUTS / "jump_loop.in", work / "run.in")
    out = _prepare(work, bundle)
    assert not out["ok"] and out["refusal"]["file"] == "input"
    assert str(bundle[1]) in out["refusal"]["hand_edit"]
    assert out["sections"]["job"]          # the job checks that passed are still shown


def test_a_build_that_refuses_reset_after_a_dump_refuses_such_an_input(work, bundle):
    (work / "run.in").write_text("units metal\nread_data a.data\nfix nve all nve\nrun 10\n"
                                 "reset_timestep 0\nrun 10\n")
    out = _prepare(work, bundle, runner=_shell("reset_after_dump", "ERROR: Cannot reset timestep"))
    assert not out["ok"] and out["refusal"]["line"] == 5


def test_a_missing_pair_style_is_refused(work, bundle):
    (work / "run.in").write_text("units metal\nread_data a.data\npair_style allegro\nrun 10\n")
    out = _prepare(work, bundle)
    assert not out["ok"] and out["refusal"]["file"] == "module" and "allegro" in out["refusal"]["reason"]


def test_the_job_must_run_the_picked_input(work, bundle):
    (work / "job.sh").write_text(JOB.replace("run.in", "other.in"))
    out = _prepare(work, bundle)
    assert not out["ok"] and out["refusal"]["file"] == "job"


def test_server_prepares_and_writes_through_its_requests(work, bundle, monkeypatch):
    import asyncio
    import types

    from ffast.live import lammps_check
    from ffast.protocol import control
    from ffast.protocol.rpc import unpack
    from ffast.session.server_session import ServerSession

    lammps_check._CACHE.clear()
    shell = FakeShell((0, HELP), (0, _run_output("done")))
    monkeypatch.setattr(lammps_check, "_subprocess_runner", shell)
    queue = asyncio.Queue()
    session = ServerSession(types.SimpleNamespace(), queue)
    req = {"input": str(work / "run.in"), "job": str(work / "job.sh"), "bundle": str(bundle[0])}

    async def ask(event, kw):
        await session.dispatch(event, (), kw)
        return unpack(queue.get_nowait())

    event, _, prepared = asyncio.run(ask(control.PREPARE_RUN_WITH_FFAST, dict(req, stop_on_alarm=True)))
    assert event == control.RUN_WITH_FFAST_PREPARED and prepared["ok"], prepared
    assert "+variable ffast_stop_on_alarm index 1" in prepared["copies"][0]["diff"]
    assert not (work / "run.ffast.in").exists()
    event, _, written = asyncio.run(ask(control.WRITE_RUN_WITH_FFAST,
                                        dict(req, stop_on_alarm=True, originals=prepared["originals"])))
    assert event == control.RUN_WITH_FFAST_WRITTEN and written["ok"], written
    assert (work / "run.ffast.in").exists() and (work / "job.ffast.sh").exists()
