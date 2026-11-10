# Any finished trajectory can be scored against a reference

Status: Proposed 2026-11-10, decisions grilled with the user the same day.
Step 1 built 2026-11-10 to 2026-11-11 (commits 1c4b78a..74a5478; plan
`docs/implementation/2026-11-10-score-any-trajectory.md`). Step 2 is not built. Reopens
the idea ADR 0089 parked ("scoring a finished trajectory" was one of its reopen
triggers). Keeps ADR 0068's no-periodic-box limit and ADR 0087's rule that
scores live apart from geometry.

## The problem

Only a LAMMPS live run gets out-of-distribution scores and chemistry flags.
A trajectory written by any other program (i-PI, ASE, CP2K, a converted LAMMPS
run) cannot be checked, although the science does not care where positions
come from: DetectAna's `score`, `exceedance` and `check_chemistry` take
positions only, plus a check that the reference can score the frame.

A later need shaped the design: references for force fields trained on many
molecules (for example GP force fields), applied to MD of a molecule that may
not be in the training set. DetectAna cannot do this yet; FFAST must not block it.

## What was decided

**Step 1: score a finished dataset.**

1. **Where.** Any loaded dataset, in any format FFAST reads, gets **Score
   against reference…** in its "⋯" menu. The dialog asks for the reference (the
   known list in `~/.ffast/references/`, or a `.bundle` path), the time between
   frames, and the persistence (default 5 ps, as for live runs). Without
   DetectAna the item is shown greyed out with the install hint.
2. **Which frames.** The reference decides, frame by frame (DetectAna's
   `validate_frame`). A frame it accepts is scored; a frame it refuses has a
   gap and the reference's own reason. The dataset is refused only when no
   frame is accepted. FFAST never hard-codes "same atoms in the same order":
   today's single-molecule references accept only their molecule, and a future
   multi-molecule reference will accept more without a change in FFAST.
3. **No periodic box.** FFAST's reader drops the `pbc` flag, so a dataset with
   a non-zero cell is refused, saying so. Keeping `pbc` waits for a real file
   that needs it.
4. **Time and alarms.** The time between frames comes from an extxyz `time`
   field when every frame has one; otherwise the user gives it; left blank,
   frames are marked but no alarm is raised, and the analysis says why. With
   time, the alarm rule, alarm reports and onset are the same as for a live
   run. Nothing is stopped.
5. **The result is a scored item.** Scoring adds an item under the dataset in
   the left-hand list, named "dataset · reference · settings". It appears at
   once and fills in frame by frame like a live run; cancelling keeps the
   frames scored so far, marked incomplete, with **Continue scoring** in its
   menu. Several scored items of one dataset are compared by ticking them
   together, as two live runs are (ADR 0089). Attribution works as for a run.
6. **Two guards on scored items.** (a) Every value other than the scores
   (energies, forces, their errors, fields, predictions) is the parent's: no
   cache is filled twice for the same geometry. (b) The stored analysis knows
   nothing about list items (rule 7), so this display can change later without
   touching saved scores.
7. **Storage.** An analysis is written once, in the live run's row format,
   under `~/.ffast/analyses/`, keyed by the dataset fingerprint (from file
   content, stable across restarts and renames), the reference's
   `bundle_sha256` and the settings. The same key reuses the saved scores
   without scoring again; any change makes a new analysis. Loading the file
   again in a later session brings its scored items back. Deleting a scored
   item hides it; its analysis stays on disk (disk cleanup is later work).
8. **Sub-datasets** of a scored item keep its scores for the frames they keep;
   they are never scored separately.
9. **Live runs** can be rescored the same way: the new analysis is a scored
   item under the run. Only the analysis the run was started with can stop it.
10. One frame is one geometry: centroid thresholds. Path-integral bead files
    are later work.

**Step 2 (planned, not built): follow a growing xyz file.** The live-run
scorer reads frames from a file another program is still writing: an
xyz/extxyz frame reader in place of the LAMMPS dump reader, the same rows,
alarms and reports. There is no wrapper, so FFAST cannot stop that run and
Stop on alarm is not offered. It needs a reader that tolerates a half-written
last frame (FFAST's frame index rescans the whole file today, ADR 0087). Start
it when a real non-LAMMPS run needs watching.

## When to change the display

Scored items mix "what was loaded" with "what was concluded about it", and an
analysis has the same shape as a prediction (dataset + something → numbers per
frame), which the selector shows as a second row. Move analyses to their own
group in the data selector, beside Predictions (ADR 0089's finding), when the
dataset list gets crowded with scored items or many trajectories are screened
against several references.

## Considered and rejected

- **One dataset, one analysis shown, the others kept hidden.** The user must be
  able to pick and compare analyses.
- **A "Score sheets" group in the selector now.** The cleaner model, kept as
  the switch point above; scored items reuse run comparison with almost no new
  interface.
- **Refuse a dataset if any frame does not match.** Blocks mixed training sets
  and future multi-molecule references.
- **The reference as a "prediction".** Puts references among force fields in
  every error metric (ADR 0089).
- **Scoring as an ordinary metric, recomputed on request.** Scoring can be
  slow and the user would not see when it runs or with which reference.
- **Writing scores into the extxyz file.** Mixes scores with geometry (ADR 0087);
  the file may also be read-only.

## What this gives up

- Ticking two scored items of one dataset in an error plot shows the same
  errors twice.
- Deleted scored items leave their analyses on disk until cleanup exists.
- A trajectory with no time, and none given, has marks but no alarms.
