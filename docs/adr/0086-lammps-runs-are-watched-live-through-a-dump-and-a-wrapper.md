# LAMMPS runs are watched live through a dump and a wrapper

Status: Accepted 2026-11-04. Not built. Supersedes ADR 0068 in part (live
watching and out-of-distribution scoring now; its one-molecule, no-periodic-
boundary scope stays). Design, two outside reviews and a MeluXina trial:
`docs/research/lammps-live-2026-11-04/` (`plan.md`, `critique.md`,
`critique-v2.md`, `meluxina-trial.md`). Run folder and viewer: ADR 0087.

## The problem

A long MD run with a machine-learned force field drifts out of the region the
model was trained on, and nothing says so: the model keeps returning plausible
forces until a bond has visibly stretched, often days of cluster time later.
DetectAna (`~/GitHub/DetectAna`, same author) can find the frame where a run
leaves the training region, but only after the run, from files. The user wants
the reverse: switch FFAST on for a LAMMPS run, watch it, be told when it goes
wrong, and optionally stop it. LAMMPS without FFAST must be untouched, and
adding a new descriptor must be easy.

## What was decided

**Who owns what.** DetectAna stays a pure science library: the versioned
reference bundle (fit, save, load, score, with a public metadata API), the
descriptor registry, tracks, scores and their thresholds, attribution, the
chemistry flags and the onset rule. It knows nothing about LAMMPS, processes or
FFAST's files. FFAST owns everything the user touches: the include file, the
wrapper, the scorer process, the run folder, the viewer and cluster setup.
DetectAna is an optional extra of FFAST, imported only when used; until a
separate publishing decision, FFAST's cluster bootstrap ships the DetectAna wheel.

**Frames leave LAMMPS through its own dump.** `ffast bundle` writes, next to the
bundle, a generic include file that the user adds once per input script
(`include /abs/path/aspirin_mace.lmp`, after the atoms exist). It sets up a
`dump custom … id type element x y z` with `sort id`, `flush yes`, `time yes`,
`units yes` and `format float %.17g`, and a `fix halt` that checks for a `STOP`
file. No Python runs inside LAMMPS.

**A wrapper in the job script runs each job.** `srun lmp -in run.in` becomes
`ffast-lammps srun lmp -in run.in`. The wrapper claims a fresh run folder on a
path without spaces, passes it with `-var ffast_on 1 -var ffast_dir …`, starts the
scorer `ffast-live` beside LAMMPS (one thread, its own log, a startup check,
modules from the bootstrap settings), knows when LAMMPS ends because it is its
parent, lets the scorer read to the end, and records how the run ended: finished,
stopped at step X, terminated, crashed, or interrupted. Without the wrapper, the
include prints "monitoring off" and the run proceeds. Multi-partition runs are
refused.

**Atom identity is proven, or labelled unverified.** `ffast lammps data` writes
the LAMMPS data file from the bundle's reference data in reference order. The
step-0 frame must equal that file exactly. Otherwise every score says "atom order
unverified"; a bond check runs as a diagnostic, and if it fails scoring stops and
names both possible causes (order differs, or the first frame is already damaged).
Only `metal` and `real` units are accepted; simulated time comes from the dump.

**Alarms need persistence.** A single frame above threshold is only a mark. An
alarm is DetectAna's existing window rule, with persistence set as a time and the
false-alarm chance estimated over the planned length read from `run N`. The first
alarm ends detection for that run segment. Chemistry flags alarm after three
flagged frames in a row. The run records three moments: estimated onset (where
the bad window starts), confirmation, and stop.

**Stopping is off by default.** A one-process run stops cleanly: the scorer
writes `STOP`, `fix halt … error soft` ends the run, later input commands still
execute. A multi-process run is terminated by the wrapper and labelled so.
"Stop requested" and "stop observed at step X" are recorded separately. FFAST
writes no checkpoint; it warns when the input writes no restart file.

## Alternatives that were live

- **A Python hook inside LAMMPS** (`fix python/invoke`), the first design. It
  needs the PYTHON package, its `python` command syntax differs between the 2022
  and 2023 builds, and the hook runs on every MPI rank, so rank coordination and
  hangs become FFAST's problem. The native dump leaves gathering to LAMMPS.
- **Launching the scorer from the include with `shell … &`.** LAMMPS consumes the
  trailing `&` as line continuation, the scorer cannot tell a paused run from a
  finished one, every job shares one folder and one STOP file, and the scorer
  inherits the rank's CPU binding. The wrapper gives each of these a plain fix.
- **A dense rolling dump** (`dump_modify maxfiles`) to keep fine frames around an
  onset. LAMMPS deletes them before a lagging scorer asks, and it creates 100,000
  files per million steps on a shared filesystem. The complete normal dump
  already holds the frames before the onset.
- **A false-alarm rate per simulated time.** It needs an alarm-episode model and
  new statistics; the planned run length is known in practice.
- **DetectAna owning the live loop, or merging DetectAna into FFAST.** The first
  puts infrastructure in a small research package and makes both repos release
  together; the second loses DetectAna as a standalone tool.

## Measured

On both MeluXina builds (`meluxina-trial.md`): every dump option, the STOP check,
`jump SELF` in an include and wrapper termination work; the step-0 identity check
held exactly; the dump plus STOP check cost about 0.2 ms per saved frame for 21
atoms (far below 0.1 % of an ML force-field run saving every 100 steps); the
multi-process `fix halt` freeze found in the source was not reproduced in 40
tries. `ITEM: UNITS` appears only in the first frame. Dump paths with spaces fail,
quoted or not.

## What this gives up

- Two edits instead of one: a line in the input and a word in the job script
  ("Run with FFAST" will later make both in copies).
- A frame reaches the scorer only after LAMMPS writes it; finer than the dump
  interval needs a denser dump.
- A multi-process anomaly stop is abrupt: nothing after the run executes.
- The scorer shares the node's cores: one spare core, or a small slowdown.
- Atom identity cannot be proven for data files FFAST did not write.
- Periodic boundaries and many molecules stay out of scope.
