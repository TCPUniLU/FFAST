"""Local build discovery at the external command-runner boundary."""

from ffast.live.local_lammps import checked_builds
from tests.ffast.live.test_lammps_check import HELP, FakeShell, _run_output


def test_path_discovery_lists_passing_and_failed_builds_without_slurm(
    tmp_path,
):
    for name in ["lmp", "lmp_old", "lmp_help.txt"]:
        p = tmp_path / name
        p.touch()
        p.chmod(0o644 if name.endswith(".txt") else 0o755)
    shell = FakeShell(
        (0, HELP),
        (0, _run_output("done")),
        (0, HELP),
        (1, "ERROR: Illegal dump_modify command"),
    )
    builds = checked_builds(runner=shell, environ={"PATH": str(tmp_path)})
    assert [(b["executable"].split("/")[-1], b["ok"]) for b in builds] == [
        ("lmp", True),
        ("lmp_old", False),
    ]
    assert "Illegal dump_modify" in builds[1]["reason"]
    assert all(
        "module " not in script and "srun " not in script
        for script in shell.scripts
    )


def test_mpi_only_build_explains_the_single_process_requirement(tmp_path):
    exe = tmp_path / "lmp_mpi"
    exe.touch()
    exe.chmod(0o755)
    shell = FakeShell((0, HELP), (1, "MPI_Init failed; use mpirun"))
    [build] = checked_builds(str(exe), runner=shell)
    assert (
        not build["ok"]
        and "starts only under mpirun: one process only for now"
        in build["reason"]
    )
