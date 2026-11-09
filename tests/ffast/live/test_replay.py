"""Replay through its LAMMPS-compatible command line (ADR 0092)."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from ffast.live import run_folder as rf
from ffast.session.examples import lammps_example_dir


def wait_for(check, process):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        assert process.poll() is None, process.communicate()
        time.sleep(0.01)
    raise AssertionError("replay did not reach the expected file state")


@pytest.mark.parametrize("stop", [False, True])
def test_replay_waits_at_gate_then_heats_or_stops(tmp_path, stop):
    source = lammps_example_dir() / "recorded-alarm"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ffast.live.replay",
            str(source),
            "--sleep",
            "0",
            "--gate-step",
            "10000",
            "-var",
            "ffast_dir",
            str(tmp_path),
            "-log",
            str(tmp_path / rf.LOG),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(
            lambda: (tmp_path / rf.FRAMES).exists()
            and "\n10000\n" in (tmp_path / rf.FRAMES).read_text(),
            process,
        )
        before = (tmp_path / rf.FRAMES).read_bytes()
        assert b"\n10100\n" not in before
        (tmp_path / (rf.STOP if stop else "GO")).touch()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        text = (tmp_path / rf.FRAMES).read_text()
        if stop:
            assert text.encode() == before
            assert (
                "ffast_halt met on step 10000"
                in (tmp_path / rf.LOG).read_text()
            )
        else:
            assert text == (source / rf.FRAMES).read_text()
        assert rf.read_include_info(tmp_path)["stop_on_alarm"] == "0"
        assert Path(rf.read_include_info(tmp_path)["bundle"]).is_dir()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
