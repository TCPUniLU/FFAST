"""The LAMMPS module check of "Run with FFAST", with a fake shell (no LAMMPS needed)."""

from pathlib import Path

import pytest

from ffast.live.lammps_check import (
    STAGES, check_input, check_lammps, check_pair_styles, parse_help, parse_run,
)
from ffast.live.lammps_input import read_commands

FIX = Path(__file__).parent / "fixtures" / "run_with_ffast"
HELP = (FIX / "lmp_help_constructed.txt").read_text()
MODULES = ["module load env/release/2023.1", "module load LAMMPS/2Aug2023-foss-2023a-kokkos"]


def _run_output(reached, error=None):
    out = ["LAMMPS (2 Aug 2023 - Update 1)", 'print "FFAST-CHECK snapshot"']
    for stage in STAGES[:STAGES.index(reached) + 1]:
        out.append(f"FFAST-CHECK {stage}")
    if error:
        out.append(error)
    return "\n".join(out)


class FakeShell:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.scripts = []

    def __call__(self, script, cwd, timeout):
        self.scripts.append(script)
        self.cwd = cwd
        return self.answers.pop(0)


def test_help_gives_version_and_pair_styles():
    version, styles = parse_help(HELP)
    assert version == "2 Aug 2023"
    assert {"mace", "mace/kk", "zero", "lj/cut"} <= styles
    assert "harmonic" not in styles and "halt" not in styles


def test_run_markers_and_error_line():
    reached, error = parse_run(_run_output("run", "ERROR: Cannot reset timestep with active dump (src/update.cpp:500)"))
    assert reached == "run" and error.startswith("ERROR: Cannot reset")


def test_a_build_that_runs_everything():
    shell = FakeShell((0, HELP), (0, _run_output("done")))
    res = check_lammps(MODULES, "LAMMPS/2Aug2023-foss-2023a-kokkos", "lmp", runner=shell, use_cache=False)
    assert res.found and res.version == "2 Aug 2023"
    assert res.ffast_commands_ok and res.snapshot_ok and res.reset_after_dump_ok
    checks, refusal = res.checks(needs_snapshot=True)
    assert refusal is None and all(c.status == "ok" for c in checks)
    for script in shell.scripts:
        assert script.splitlines()[0].startswith("module --force purge")
        assert script.splitlines()[1:3] == MODULES
    assert shell.scripts[1].endswith("lmp -in test.in -log test.log -echo screen")
    assert " " not in str(shell.cwd)


def test_a_build_that_refuses_reset_timestep_after_a_dump():
    # the line LAMMPS 2 Aug 2023 printed on MeluXina (step-5 proof, 2026-11-06)
    err = "ERROR: Cannot reset timestep with active dump - must undump first (src/output.cpp:640)"
    shell = FakeShell((0, HELP), (1, _run_output("reset_after_dump", err)))
    res = check_lammps(MODULES, "LAMMPS/x", "lmp", runner=shell, use_cache=False)
    assert res.ffast_commands_ok and not res.reset_after_dump_ok
    checks, refusal = res.checks(needs_snapshot=True)
    assert refusal is None and any(c.status == "warn" and "reset_timestep" in c.text for c in checks)


def test_a_build_without_the_dump_options_is_refused():
    shell = FakeShell((0, HELP), (1, _run_output("include", "ERROR: Illegal dump_modify command (src/dump.cpp:900)")))
    res = check_lammps(MODULES, "LAMMPS/old", "lmp", runner=shell, use_cache=False)
    _, refusal = res.checks(needs_snapshot=False)
    assert refusal and "Illegal dump_modify" in refusal


def test_a_refused_snapshot_matters_only_when_the_input_minimizes():
    shell = FakeShell((0, HELP), (1, "LAMMPS (x)\nERROR: Illegal write_dump command"))
    res = check_lammps(MODULES, "LAMMPS/old", "lmp", runner=shell, use_cache=False)
    assert res.checks(needs_snapshot=True)[1] and "write_dump" in res.checks(needs_snapshot=True)[1]


def test_a_module_that_does_not_load():
    shell = FakeShell((1, "Lmod has detected the following error: The following module(s) are unknown: LAMMPS/nope"))
    res = check_lammps(["module load LAMMPS/nope"], "LAMMPS/nope", "lmp", runner=shell, use_cache=False)
    assert not res.found
    _, refusal = res.checks(needs_snapshot=False)
    assert "unknown: LAMMPS/nope" in refusal


def test_an_mpi_build_is_retried_through_srun():
    shell = FakeShell((0, HELP), (1, "PMI2_Init failed to initialize. Return code: 14"), (0, _run_output("done")))
    res = check_lammps(MODULES, "LAMMPS/x", "lmp_mpi", runner=shell, use_cache=False)
    assert res.launched_with_srun and res.reset_after_dump_ok
    assert "srun --overlap -n 1 lmp_mpi -in test.in" in shell.scripts[2]


def test_results_are_cached_per_module_lines_and_executable():
    shell = FakeShell((0, HELP), (0, _run_output("done")))
    a = check_lammps(["module load LAMMPS/cache-test"], "LAMMPS/cache-test", "lmp", runner=shell)
    b = check_lammps(["module load LAMMPS/cache-test"], "LAMMPS/cache-test", "lmp", runner=shell)
    assert a is b and len(shell.scripts) == 2


@pytest.mark.parametrize("styles, hybrid, words", [
    (["mace"], False, None),
    (["allegro"], False, "allegro"),
    (["mace", "no_domain_decomposition", "zbl"], True, None),
    (["nequip", "foo"], True, "none of the hybrid"),
])
def test_pair_style_check(styles, hybrid, words):
    res = check_lammps(MODULES, "x", "lmp", runner=FakeShell((0, HELP), (0, _run_output("done"))), use_cache=False)
    checks, refusal = check_pair_styles(styles, hybrid, False, res)
    if words is None:
        assert refusal is None and checks[0].status == "ok"
    else:
        assert words in refusal


def test_the_test_input_uses_every_command_ffast_adds(tmp_path):
    names = [c.name for c in read_commands(check_input(tmp_path))]
    assert names.count("reset_timestep") == 2 and names.count("run") == 2
    assert "include" in names and "if" in names       # the snapshot line
    included = [c.name for c in read_commands((tmp_path / "ffast_check.lmp").read_text())]
    assert {"dump", "dump_modify", "fix", "variable"} <= set(included)
