"""`ffast-lammps` with a fake LAMMPS (ADR 0086)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from ffast.live import run_folder as rf
from ffast.live import wrapper as w
from tests.ffast.live.bundle_helpers import initial_atoms, make_bundle
from tests.ffast.live.dump_helpers import dump_text

HERE = Path(__file__).parent
FAKE = str(HERE / "lmp_fake.py")
REPO = HERE.parents[2]


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp("b"))[0]


@pytest.fixture
def env(tmp_path, bundle_dir):
    base = initial_atoms().get_positions()
    rng = np.random.default_rng(3)
    frames = [base] + [base + rng.normal(0, 0.02, base.shape) for _ in range(4)]
    dump = tmp_path / "source.lammpstrj"
    dump.write_text(dump_text(frames))
    e = dict(os.environ)
    e.update({
        "FFAST_RUN_DIR": str(tmp_path / "runs"),
        "FFAST_LAMMPS_DATA_RECORDS": str(tmp_path / "records"),
        "FFAST_LIVE_SCORER_CMD": json.dumps([sys.executable, "-m", "ffast.live.scorer", "--poll", "0.02"]),
        "FAKE_LMP_BUNDLE": str(bundle_dir),
        "FAKE_LMP_DUMP": str(dump),
        "FAKE_LMP_SLEEP": "0.02",
    })
    return e


def _run_dir(e):
    [d] = list(Path(e["FFAST_RUN_DIR"]).iterdir())
    return d


def _wrapper(e, *cmd, **kw):
    return w.Wrapper([sys.executable, FAKE, "-in", "run.in", *cmd], environ=e, cwd=str(REPO),
                     poll_s=0.05, **kw)


def test_a_finished_run_is_scored_to_the_end_and_recorded(env):
    rc = _wrapper(env).main()
    assert rc == 0
    d = _run_dir(env)
    run = rf.read_json(d / rf.RUN_JSON)
    assert run["format"] == "ffast-live-run" and run["format_version"] == 1
    assert run["end"]["state"] == "finished" and run["end"]["drained"] is True
    assert run["lammps_version"] == "LAMMPS (fake build)"
    assert run["units"] == "metal" and run["producer"]["last_step"] == 400
    [(aid, progress)] = run["analyses"].items()
    assert progress["state"] == "done" and progress["scored_frames"] == 5
    assert len(rf.read_scores(d / rf.ANALYSIS_DIR / aid / rf.SCORES)) == 5
    assert rf.read_json(d / rf.PRODUCER_DONE)["exit_code"] == 0
    kinds = [e["kind"] for e in rf.read_events(d)]
    assert kinds[:2] == ["start", "segment"] and kinds[-1] == "end"
    assert "lammps_exit" in kinds and "drained" in kinds


def test_lammps_gets_the_switch_the_folder_and_a_log_in_it(env, tmp_path):
    env["FAKE_LMP_ARGS"] = str(tmp_path / "args.json")
    _wrapper(env).main()
    args = json.loads((tmp_path / "args.json").read_text())
    d = _run_dir(env)
    assert args == ["-in", "run.in", "-var", "ffast_on", "1", "-var", "ffast_dir", str(d),
                    "-log", str(d / rf.LOG)]


def test_a_log_given_by_the_user_is_kept(env, tmp_path):
    env["FAKE_LMP_ARGS"] = str(tmp_path / "args.json")
    _wrapper(env, "-log", str(tmp_path / "mine.log")).main()
    args = json.loads((tmp_path / "args.json").read_text())
    assert args.count("-log") == 1


def test_each_run_gets_a_fresh_folder(env):
    _wrapper(env).main()
    _wrapper(env).main()
    assert len(list(Path(env["FFAST_RUN_DIR"]).iterdir())) == 2


def test_a_crash_is_recorded_and_its_exit_code_returned(env):
    env["FAKE_LMP_EXIT"] = "3"
    assert _wrapper(env).main() == 3
    assert rf.read_json(_run_dir(env) / rf.RUN_JSON)["end"]["state"] == "crashed"


def test_an_input_without_the_include_says_so(env):
    env["FAKE_LMP_NO_INCLUDE"] = "1"
    assert _wrapper(env).main() == 0
    texts = [e.get("text", "") for e in rf.read_events(_run_dir(env))]
    assert any("without running the FFAST include" in t for t in texts)


def test_the_wrapper_waits_for_a_slow_scorer_to_drain(env, tmp_path):
    slow = tmp_path / "slow.py"
    slow.write_text("import sys, time, pathlib\n"
                    "d = pathlib.Path(sys.argv[1])\n"
                    "while not (d / 'producer_done.json').exists(): time.sleep(0.01)\n"
                    "time.sleep(1.0)\n"
                    "(d / 'slow_done').write_text('x')\n")
    env["FFAST_LIVE_SCORER_CMD"] = json.dumps([sys.executable, str(slow)])
    _wrapper(env).main()
    d = _run_dir(env)
    assert (d / "slow_done").exists()
    [drained] = [e for e in rf.read_events(d) if e["kind"] == "drained"]
    assert drained["seconds"] >= 0.9


def test_a_scorer_that_does_not_drain_in_time_is_stopped_and_reported(env, tmp_path):
    stuck = tmp_path / "stuck.py"
    stuck.write_text("import time\nwhile True: time.sleep(1)\n")
    env["FFAST_LIVE_SCORER_CMD"] = json.dumps([sys.executable, str(stuck)])
    _wrapper(env, drain_s=0.5).main()
    run = rf.read_json(_run_dir(env) / rf.RUN_JSON)
    assert run["end"]["drained"] is False and run["end"]["state"] == "finished"


def test_a_scorer_that_dies_early_leaves_a_warning_with_its_log(env, tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import sys\nprint('boom: no detectana')\nsys.exit(1)\n")
    env["FFAST_LIVE_SCORER_CMD"] = json.dumps([sys.executable, str(bad)])
    env["FAKE_LMP_SLEEP"] = "0.2"
    assert _wrapper(env).main() == 0
    [warn] = [e for e in rf.read_events(_run_dir(env)) if e["kind"] == "warning"]
    assert "boom: no detectana" in warn["scorer_log"]


def test_sigterm_interrupts_lammps_and_is_recorded(env):
    env["FAKE_LMP_SLEEP"] = "5"
    p = subprocess.Popen([sys.executable, "-m", "ffast.live.wrapper", sys.executable, FAKE, "-in", "x"],
                         env=env, cwd=str(REPO), stderr=subprocess.PIPE)
    deadline = time.time() + 20
    while not list(Path(env["FFAST_RUN_DIR"]).glob("*/frames.lammpstrj")):
        assert time.time() < deadline
        time.sleep(0.05)
    p.send_signal(signal.SIGTERM)
    rc = p.wait(30)
    assert rc == 128 + signal.SIGTERM
    run = rf.read_json(_run_dir(env) / rf.RUN_JSON)
    assert run["end"]["state"] == "interrupted" and run["end"]["signal"] == signal.SIGTERM


def test_a_run_folder_path_with_a_space_is_refused(env, tmp_path):
    env["FFAST_RUN_DIR"] = str(tmp_path / "with space")
    p = subprocess.run([sys.executable, "-m", "ffast.live.wrapper", sys.executable, FAKE],
                       env=env, cwd=str(REPO), capture_output=True, text=True)
    assert p.returncode == 2 and "FFAST_RUN_DIR" in p.stderr
    assert not (tmp_path / "with space").exists()


@pytest.mark.parametrize("cmd", [
    ["srun", "lmp", "-partition", "2x4", "-in", "x"],
    ["srun", "-n", "4", "lmp_mpi", "-p", "2x2", "-in", "x"],
    ["mpirun", "./a.out", "-partition", "2x2"],
])
def test_multi_partition_runs_are_refused(cmd):
    with pytest.raises(w.UsageError, match="multi-partition"):
        w.check_command(cmd)


def test_slurm_partition_flag_is_not_mistaken_for_lammps():
    w.check_command(["srun", "-p", "cpu", "lmp", "-in", "x"])


def test_the_scorer_starts_with_the_bootstrap_modules_and_one_thread(tmp_path):
    settings = tmp_path / "live.json"
    settings.write_text(json.dumps({"modules": ["env/staging/2024.1", "Python/3.11.10"],
                                    "venv": "~/.ffast/venv"}))
    argv, env = w.scorer_command(tmp_path / "run", {"FFAST_LIVE_SETTINGS": str(settings),
                                                    "PYTHONPATH": "/lammps/python", "PATH": "/bin"})
    assert argv[:2] == ["bash", "-lc"] and argv[3] == str(tmp_path / "run")
    script = argv[2]
    assert "module --force purge" in script
    assert script.index("module load env/staging/2024.1") < script.index("module load Python/3.11.10")
    assert "source " + os.path.expanduser("~/.ffast/venv") + "/bin/activate" in script
    assert script.endswith('exec ffast-live "$0"')
    assert "PYTHONPATH" not in env and env["OMP_NUM_THREADS"] == "1" and env["PATH"] == "/bin"
