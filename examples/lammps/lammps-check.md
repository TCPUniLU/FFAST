# Local LAMMPS checks

Checked on macOS arm64 on 2026-11-09/10. Both builds are conda-forge LAMMPS
22 Jul 2025 Update 6, installed with micromamba into separate temporary
environments, never into `ffast_env`:

- `/private/tmp/ffast-lammps`: `lammps-2025.07.22-cpu_py314_h6680721_nompi_7`.
- `/private/tmp/ffast-lammps-mpi`: `lammps-2025.07.22-cpu_py314_h4a84ca2_mpi_mpich_7`;
  the experiment used `mpirun -n 2 lmp_mpi`.

## Commands verified

`run 300 every 100 "shell sleep 0.3"` completed three 100-step pieces and
waited for each sleep. The serial gate was reached about 1.0 s after launch
(0.9 s of requested sleeps plus startup). The first MPI launch took 10.7 s
including MPI startup; the next took 1.3 s. These are launch-to-gate times,
not simulation performance measurements.

The originally proposed `shell sh -c "while …"` failed: LAMMPS removes the
argument quotes before passing the command to the shell. Quoting the whole
command also failed: `${ffast_dir}` inside those quotes was not substituted
by LAMMPS, so the shell looked in the wrong directory. The verified input is:

```lammps
shell while [ ! -e ${ffast_dir}/GO ] && [ ! -e ${ffast_dir}/STOP ]; do sleep 0.1; done
```

The wrapper requires a run-folder path without spaces. With this line, all
four experiments held at step 300 for a further 0.5 s with no new frames:

| Build | File written | Last saved step | Outcome |
|---|---|---:|---|
| Serial | GO | 600 | second run completed |
| Serial | STOP | 400 | `fix halt` observed STOP at its next 100-step check |
| Two MPICH processes | GO | 600 | second run completed |
| Two MPICH processes | STOP | 400 | `fix halt` observed STOP at its next 100-step check |

Commands after the second run executed in every case. STOP releases the shell
wait; `fix halt` acts once dynamics resumes. It does not interrupt a shell
command. The gate therefore needs its own STOP condition.

Only process 0 executes this external shell command; the other process makes
no progress through the next collective simulation operation until process 0
returns. The two-process experiment verified no advancing dump frames during
the wait, then successful GO/STOP completion. It did not measure MPI CPU use.
Local product launch remains one process only.

The shell execution and substitution explanation follows the official
[LAMMPS shell command](https://docs.lammps.org/shell.html) and
[input parsing rules](https://docs.lammps.org/Commands_parse.html).

## Full toy run

The real wrapper/scorer loaded the generated `aspirin.ffast.data`, verified
atom identity from `start.lammpstrj`, paused at step 10,000 and resumed on GO.
A user stop then produced `stop_requested`, `stop_sent` and `stop_observed`
and an ended record with `state: stopped` (step 12,600 in this run).
A separate STOP-at-gate run released the wait and stopped at step 10,100.

This build raised an OOD alarm at step 9,900, near the end of the 300 K
segment, and a chemistry alarm after heating. Tutorial 2 reads the actual
alarm state; it promises neither the replay's alarm kind nor its onset step.

The entire 13-step browser tutorial also passed against this real executable,
including input review, data-copy review, launch, heat, the real Live runs
Stop now confirmation and deletion. The same browser regression normally uses
a fake `lmp` (the recorded replay) for CI; set `FFAST_TEST_LAMMPS` to an
executable path to repeat it with a real build.

The original `aspirin.data` provenance remains unresolved; this check does not
clear the existing public-push blocker.
