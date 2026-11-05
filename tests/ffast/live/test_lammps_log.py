"""Reading log.lammps: planned length, process count, halt step, restart commands.

The excerpts copy the lines both MeluXina builds wrote in the step-2 proof
(docs/research/lammps-live-2026-11-04/thin-slice-proof/results), paths shortened.
"""

from __future__ import annotations

from ffast.live.lammps_log import LogFollower, PlannedLength

PROOF_LOG = """\
LAMMPS (2 Aug 2023 - Update 2)
units           metal
read_data       aspirin.data
Reading data file ...
  orthogonal box = (27.9945 -10.1505 -10.65) to (52.4734 13.1035 11.6695)
  1 by 2 by 1 MPI processor grid
variable        nsteps index 20000
include         /x/aspirin.lmp
if "${ffast_on} == 0" then "print 'FFAST: not run through ffast-lammps; monitoring off'" "jump SELF ffast_end"
run             ${nsteps}
run             20000
Loop time of 0.153213 on 2 procs for 20000 steps with 21 atoms
"""


def _plan(*lines, first=0):
    p = PlannedLength()
    for line in lines:
        p.feed(line)
    return p.end_step(first)


def test_run_lines_give_the_planned_end():
    assert _plan("run 1000") == 1000
    assert _plan("run 1000", "run 500", first=200) == 1700
    assert _plan("run 1000", "run 5000 upto") == 5000
    assert _plan("run 1000 start 0 stop 100000 pre no post no") == 1000
    assert _plan("run 10000 every 1000 NULL") == 10000
    assert _plan("run ${nsteps}", "run 20000") == 20000        # raw echo, then substituted
    assert _plan() is None


def test_a_run_count_that_is_not_a_number_makes_the_plan_unknown():
    p = PlannedLength()
    assert p.feed("run notanumber")
    assert p.end_step(0) is None and p.unknown == "run notanumber"


def test_the_proof_log_gives_processes_and_plan(tmp_path):
    log = tmp_path / "log.lammps"
    log.write_text(PROOF_LOG)
    f = LogFollower(log)
    changes = f.poll()
    assert ("processes", 2) in changes
    assert ("first_run", False) in changes and f.restart_before_run is False
    assert f.planned.end_step(0) == 20000
    assert [c for c in changes if c[0] == "planned"] == [("planned", "run             20000")]


def test_lines_arrive_in_pieces_and_a_halt_is_read(tmp_path):
    log = tmp_path / "log.lammps"
    log.write_text("  1 by 1 by 1 MPI processor grid\nrestart 1000 a.restart\nrun 1000")
    f = LogFollower(log)
    assert f.poll() == [("processes", 1)]                      # "run 1000" has no newline yet
    with open(log, "a") as fh:
        fh.write("0\nWARNING: Fix halt condition for fix-id ffast_halt met on step 7300 with "
                 "value 1 (src/fix_halt.cpp:237)\n")
    changes = f.poll()
    assert ("first_run", True) in changes and ("halt", 7300) in changes
    assert f.planned.end_step(0) == 10000 and f.halt_step == 7300


def test_another_fix_halt_is_not_ours(tmp_path):
    log = tmp_path / "log.lammps"
    log.write_text("Fix halt condition for fix-id user_halt met on step 5 with value 1\n")
    f = LogFollower(log)
    assert f.poll() == [] and f.halt_step is None


def test_a_missing_log_is_not_yet(tmp_path):
    assert LogFollower(tmp_path / "nope").poll() == []
