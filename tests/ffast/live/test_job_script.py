"""Finding and wrapping the LAMMPS launch in real-world Slurm job scripts."""

import pytest

from ffast.live.job_script import (
    WRAPPER, Launch, copy_name, find_launch, lammps_module, module_lines, wrap,
)
from ffast.live.lammps_input import Refusal

DIR = "/project/home/pXXXXXX/aspirin"
INPUT = DIR + "/run.in"
MARKER = "# Written by Run with FFAST"

MELUXINA = """#!/bin/bash -l
#SBATCH --job-name=aspirin
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=01:00:00
#SBATCH --qos=default

module load env/release/2023.1
module load LAMMPS/2Aug2023-foss-2023a-kokkos

cd $SLURM_SUBMIT_DIR
export OMP_NUM_THREADS=1
srun lmp -in run.in -var seed 42
echo done
"""


def _launch(text, input_path=INPUT):
    out = find_launch(text, input_path, DIR)
    assert isinstance(out, Launch), out.text() if isinstance(out, Refusal) else out
    return out


def _copy(text):
    launch = _launch(text)
    return wrap(text, launch, "run.ffast.in", MARKER).splitlines()


def test_meluxina_job_script_is_wrapped_and_points_at_the_copy():
    lines = _copy(MELUXINA)
    assert lines[0] == "#!/bin/bash -l" and lines[1] == MARKER
    assert f"{WRAPPER} srun lmp -in run.ffast.in -var seed 42" in lines
    assert lines.count("echo done") == 1
    assert len(lines) == len(MELUXINA.splitlines()) + 1


@pytest.mark.parametrize("line, expected", [
    ("mpirun -np 4 lmp_mpi -in run.in", f"{WRAPPER} mpirun -np 4 lmp_mpi -in run.ffast.in"),
    ("mpiexec -n $SLURM_NTASKS /opt/lammps/bin/lmp -i run.in",
     f"{WRAPPER} mpiexec -n $SLURM_NTASKS /opt/lammps/bin/lmp -i run.ffast.in"),
    ("lmp < run.in > out.txt", f"{WRAPPER} lmp < run.ffast.in > out.txt"),
    ("lmp <run.in", f"{WRAPPER} lmp <run.ffast.in"),
    ('srun lmp -in "run.in"', f'{WRAPPER} srun lmp -in "run.ffast.in"'),
    ("time srun --cpu-bind=cores lmp -k on t 4 -sf kk -in run.in",
     f"time {WRAPPER} srun --cpu-bind=cores lmp -k on t 4 -sf kk -in run.ffast.in"),
    ("OMP_NUM_THREADS=1 srun lmp -in " + INPUT, f"OMP_NUM_THREADS=1 {WRAPPER} srun lmp -in /project/home/pXXXXXX/aspirin/run.ffast.in"),
    ("cd run && srun lmp -in ../run.in", None),
    ("srun -p cpu lmp -in run.in", f"{WRAPPER} srun -p cpu lmp -in run.ffast.in"),
])
def test_launch_forms(line, expected):
    text = "#!/bin/bash\n" + line + "\n"
    if expected is None:
        assert isinstance(find_launch(text, INPUT, DIR + "/run"), Launch)
        return
    launch = _launch(text)
    copy_input = "/project/home/pXXXXXX/aspirin/run.ffast.in" if INPUT in line else "run.ffast.in"
    assert wrap(text, launch, copy_input, MARKER).splitlines()[2] == expected


def test_continuation_lines_become_one_line():
    text = "#!/bin/bash\nsrun --ntasks=1 \\\n     lmp -in run.in \\\n     -var t 300\necho end\n"
    launch = _launch(text)
    assert (launch.first, launch.last) == (2, 4)
    lines = wrap(text, launch, "run.ffast.in", MARKER).splitlines()
    assert lines[2].split() == [WRAPPER, "srun", "--ntasks=1", "lmp", "-in", "run.ffast.in", "-var", "t", "300"]
    assert lines[3] == "echo end"


def test_already_wrapped_keeps_one_wrapper():
    text = "#!/bin/bash\n~/.ffast/bin/ffast-lammps srun lmp -in run.in\n"
    launch = _launch(text)
    assert launch.wrapped
    line = wrap(text, launch, "run.ffast.in", MARKER).splitlines()[2]
    assert line == "~/.ffast/bin/ffast-lammps srun lmp -in run.ffast.in"


@pytest.mark.parametrize("text, words", [
    ("#!/bin/bash\necho no lammps here\n", "no command starts LAMMPS"),
    ("#!/bin/bash\nsrun lmp -in run.in\nsrun lmp -in run.in\n", "started 2 times"),
    ("#!/bin/bash\nsrun lmp -partition 2x1 -in run.in\n", "partition"),
    ("#!/bin/bash\nsrun lmp -p 2x1 -in run.in\n", "partition"),
    ("#!/bin/bash\nsrun lmp -in other.in\n", "not the picked input"),
    ("#!/bin/bash\nsrun lmp -in $INPUT\n", "variable"),
    ("#!/bin/bash\nsrun lmp -var x 1\n", "names no input"),
])
def test_refusals(text, words):
    out = find_launch(text, INPUT, DIR)
    assert isinstance(out, Refusal) and words in out.reason, out


def test_comments_and_sbatch_lines_are_not_launches():
    text = "#!/bin/bash\n#SBATCH --job-name=lmp\n# srun lmp -in old.in\nsrun lmp -in run.in\n"
    assert _launch(text).first == 4


def test_cd_before_a_relative_input_is_warned():
    text = "#!/bin/bash\ncd /scratch/me\nsrun lmp -in run.in\n"
    assert _launch(text).warnings
    text = "#!/bin/bash\ncd $SLURM_SUBMIT_DIR\nsrun lmp -in run.in\n"
    assert not _launch(text).warnings


def test_module_lines_and_the_lammps_module():
    text = ("#!/bin/bash -l\nmodule purge\nmodule load env/release/2023.1\n"
            "ml Python/3.11 LAMMPS/23Jun2022-foss-2022a-kokkos\n# module load LAMMPS/old\n"
            "module use /home/me/modules\nsrun lmp -in run.in\n")
    lines = module_lines(text)
    assert lines == ["module purge", "module load env/release/2023.1",
                     "ml Python/3.11 LAMMPS/23Jun2022-foss-2022a-kokkos", "module use /home/me/modules"]
    assert lammps_module(lines) == "LAMMPS/23Jun2022-foss-2022a-kokkos"
    assert lammps_module(["module load GCC", "ml purge"]) is None


@pytest.mark.parametrize("name, expected", [
    ("run.in", "run.ffast.in"), ("in.aspirin", "in.ffast.aspirin"), ("job.sh", "job.ffast.sh"),
    ("job", "job.ffast"), ("inputs/run.in", "inputs/run.ffast.in"), ("/a/b/x.lmp.in", "/a/b/x.ffast.lmp.in"),
])
def test_copy_names(name, expected):
    assert copy_name(name) == expected
