"""Practice lifecycle through the local launcher and file controls."""

import asyncio
import os
import signal
from pathlib import Path

import pytest

from ffast.live import run_folder as rf
from ffast.live.local_launch import (
    delete_practice_run,
    heat_practice_run,
    start_practice_run,
)
from ffast.live.runs_list import list_runs


def practice(tmp_path, *, marked=True, inside=True):
    base = tmp_path / "practice"
    folder = (base if inside else tmp_path / "ordinary") / "r1"
    folder.mkdir(parents=True)
    env = {
        rf.PRACTICE_RUNS_ENV: str(base),
        rf.LIVE_RUNS_ENV: str(tmp_path / "pointers"),
    }
    run = {"run_id": "r1", "producer": {"last_step": 10000}, "end": None}
    if marked:
        run["practice"] = {"gate_step": 10000}
    rf.write_json_atomic(folder / rf.RUN_JSON, run)
    rf.register_run("r1", folder, env)
    return env, folder, run


def test_pause_heat_and_delete_are_visible_in_the_normal_list(tmp_path):
    env, folder, run = practice(tmp_path)
    [row] = list_runs(rf.live_runs_dir(env))
    assert row["practice"] == {"gate_step": 10000} and row["paused"]
    heat_practice_run("r1", env)
    assert (folder / "GO").exists()
    assert not list_runs(rf.live_runs_dir(env))[0]["paused"]
    with pytest.raises(ValueError, match="stop"):
        delete_practice_run("r1", env)
    run["end"] = {"state": "stopped"}
    rf.write_json_atomic(folder / rf.RUN_JSON, run)
    delete_practice_run("r1", env)
    assert not folder.exists() and list_runs(rf.live_runs_dir(env)) == []


@pytest.mark.parametrize("marked,inside", [(False, True), (True, False)])
def test_heat_and_delete_refuse_unmarked_or_outside_folders(
    tmp_path, marked, inside
):
    env, folder, _ = practice(tmp_path, marked=marked, inside=inside)
    for action in (heat_practice_run, delete_practice_run):
        with pytest.raises(ValueError):
            action("r1", env)
    assert folder.exists() and not (folder / "GO").exists()


@pytest.mark.parametrize("paused", [False, True])
async def test_start_publishes_a_pointer_and_frames_and_wrapper_records_stop(
    tmp_path, paused
):
    env = dict(
        os.environ,
        FFAST_PRACTICE_RUNS=str(tmp_path / "practice"),
        FFAST_LIVE_RUNS=str(tmp_path / "pointers"),
        FFAST_REFERENCES=str(tmp_path / "references"),
        FFAST_PRACTICE_SLEEP="0" if paused else "0.3",
    )
    run_id, path = await start_practice_run(environ=env)
    folder = Path(path)
    try:
        async with asyncio.timeout(30):
            while not (folder / rf.FRAMES).exists():
                await asyncio.sleep(0.02)
        assert (
            rf.read_json(folder / rf.RUN_JSON)["practice"]["gate_step"]
            == 10000
        )
        from ffast.live.references import recorded

        [reference] = recorded(env)
        assert (
            reference["name"] == "toy aspirin (tutorial)"
            and reference["problem"] is None
        )
        if paused:
            async with asyncio.timeout(15):
                while not rf.practice_paused(
                    folder, rf.read_json(folder / rf.RUN_JSON)
                ):
                    await asyncio.sleep(0.02)
        rf.request_stop(folder, "user", "test stop")
        async with asyncio.timeout(30):
            while not (rf.read_json(folder / rf.RUN_JSON) or {}).get("end"):
                await asyncio.sleep(0.02)
        assert rf.read_json(folder / rf.RUN_JSON)["end"]["state"] == "stopped"
    finally:
        run = rf.read_json(folder / rf.RUN_JSON)
        if run and not run.get("end"):
            # The real wrapper is this test process's child.
            os.kill(int(run_id.split("-")[-2]), signal.SIGTERM)


async def test_start_reports_the_wrappers_own_error(tmp_path):
    env = dict(
        os.environ,
        FFAST_PRACTICE_RUNS=str(tmp_path / "has spaces"),
        FFAST_LIVE_RUNS=str(tmp_path / "pointers"),
        FFAST_REFERENCES=str(tmp_path / "references"),
    )
    with pytest.raises(ValueError, match="contains a space"):
        await start_practice_run(environ=env)


def test_practice_run_is_interrupted_when_its_server_exits(tmp_path):
    import subprocess
    import sys
    import time

    env = dict(
        os.environ,
        FFAST_PRACTICE_RUNS=str(tmp_path / "practice"),
        FFAST_LIVE_RUNS=str(tmp_path / "pointers"),
        FFAST_REFERENCES=str(tmp_path / "references"),
        FFAST_PRACTICE_SLEEP="0",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio; from ffast.live.local_launch import start_practice_run; "
            "asyncio.run(start_practice_run())",
        ],
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    [pointer] = list((tmp_path / "pointers").glob("*.json"))
    folder = Path(rf.read_json(pointer)["run_dir"])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = rf.read_json(folder / rf.RUN_JSON) or {}
        if run.get("end"):
            assert run["end"]["state"] == "interrupted"
            return
        time.sleep(0.02)
    raise AssertionError("practice wrapper did not record its server's exit")


async def test_a_scorer_that_cannot_start_reports_the_wrapper_error(tmp_path):
    import json

    env = dict(
        os.environ,
        FFAST_PRACTICE_RUNS=str(tmp_path / "practice"),
        FFAST_LIVE_RUNS=str(tmp_path / "pointers"),
        FFAST_REFERENCES=str(tmp_path / "references"),
        FFAST_LIVE_SCORER_CMD=json.dumps(["/nonexistent-ffast-scorer"]),
    )
    with pytest.raises(ValueError, match="nonexistent-ffast-scorer"):
        await start_practice_run(environ=env)


def test_tutorial_copy_refuses_user_folders_and_keeps_repo_input_unchanged(
    tmp_path,
):
    from ffast.live.local_launch import prepare_tutorial_input
    from ffast.session.examples import lammps_example_dir

    env = dict(
        os.environ,
        FFAST_PRACTICE_RUNS=str(tmp_path / "practice"),
        FFAST_REFERENCES=str(tmp_path / "references"),
    )
    original = (lammps_example_dir() / "tutorial" / "in.tutorial").read_bytes()
    folder = tmp_path / "practice" / "tutorial-input"
    folder.mkdir(parents=True)
    mine = folder / "notes.txt"
    mine.write_text("mine")
    with pytest.raises(ValueError, match="not made by FFAST"):
        prepare_tutorial_input(env)
    assert mine.read_text() == "mine"
    mine.unlink()
    folder.rmdir()
    out = prepare_tutorial_input(env)
    assert Path(out["input"]).is_file() and Path(out["reference"]).is_dir()
    from ffast.live.run_folder_scan import scan

    found = scan(out["folder"], target="local", environ=env)
    assert (
        found["input"] == out["input"]
        and found["reference"] == out["reference"]
    )
    assert found["data"] == str(folder / "aspirin.data")
    assert found["problem"] is None
    assert (
        lammps_example_dir() / "tutorial" / "in.tutorial"
    ).read_bytes() == original
