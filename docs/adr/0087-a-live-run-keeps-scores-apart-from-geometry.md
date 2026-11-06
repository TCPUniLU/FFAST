# A live run keeps its scores apart from its geometry

Status: Accepted 2026-11-04. Built 2026-11-05/06 on the local branch
`lammps-live`, not merged (run folder, live-run tab, Live runs list,
attribution, Make reference; plan steps 2 and 4). Companion to ADR 0086 (how a LAMMPS run
is watched). Leaves ADR 0075 (dataset preview) unchanged. Design and reviews:
`docs/research/lammps-live-2026-11-04/`.

## The problem

A watched LAMMPS run produces two kinds of data: the frames themselves, and
DetectAna's scores for them against one reference bundle. The first design put
the scores inside the frames as extxyz `info` and `arrays` keys, showed the
growing file as a dataset preview (ADR 0075), and let it become a normal dataset
at the end. Two reviews found it could not work:

- A dataset's fingerprint covers geometry only (elements, positions, energies,
  forces). The same frames scored against two bundles look identical to FFAST, so
  cached results keyed by field name and fingerprint can be reused across
  different analyses: a silent wrong answer.
- ADR 0075 makes a preview look-only on purpose: no metrics, no analysis. Live
  plots and colouring needed exactly what it forbids.
- FFAST's fast reader and frame index read `.xyz`/`.extxyz` only and rescan the
  whole file when it changes; ASE's LAMMPS reader loads every line into memory.
  Neither suits a file that grows for days.
- `saveDataset` rebuilds atoms from coordinates and energies, so cutting and
  exporting drops the simulation step, time and atom IDs.

## What was decided

**The run folder** (one per wrapper invocation, never reused; ADR 0086) holds:

- `frames.lammpstrj`: the dump, geometry only, kept complete.
- `analysis/<analysis-id>/scores`: one row per scored frame, keyed by run segment
  and frame index, with per-track T² and SPE (or "inactive"), flags and chemistry
  flags. Append-only. The analysis ID covers the bundle and the effective
  settings, so two analyses of the same run sit side by side.
- `analysis/<analysis-id>/alarms/`: a small permanent report per alarm (top
  descriptor groups, per-atom attribution strength, bundle ID, DetectAna version).
- `events.jsonl`: start, estimated onset, confirmation, stop requested, stop
  observed, warnings, segments.
- `run.json`: run and segment IDs, LAMMPS version, units, settings, producer
  progress, scored-through progress, scorer heartbeat and end state, replaced
  atomically (write, then rename), with a format version.

**One incremental reader for LAMMPS dumps**, shared by the scorer and the viewer.
It reads only new bytes, treats an incomplete last frame as "not yet" (a frame is
complete when all the lines its header announces are present), detects a replaced
file, and knows the final boundary when the run ends. It supports the layout FFAST
generates; other dump layouts and the binary format are not promised. Units appear
only in the first frame, so the reader carries them forward. Reading, cutting and
exporting keep atom IDs, step, segment, elapsed time and units.

**A small live-run view** draws scores, marks, alarms and the three onset moments
with FFAST's existing plot and 3D components. It does not use preview metrics, so
ADR 0075 stays as written. When the run ends, the frames can be opened as a normal
dataset; its fingerprint is geometry only, and the scores stay in their analysis
folder. Joining scores into exported subsets is deferred, and the page says so.

**Attribution is computed when asked.** The frame the explained 3D panel shows
(frame slider, playback, or a clicked score point; the panel is picked in the
attribution card, ADR 0089 amendment of 2026-11-06, and was the main view
before) asks DetectAna, through its public
API and the bundle, for the reconstruction-based contribution of each
descriptor group. One request per run is in flight; frames passed meanwhile are
skipped and the last one shown is asked next. It is shown as attribution strength on a fixed scale, never as
shares of the score, because the values overlap. Only alarm reports are stored.

**"Live runs" list**: from the run folders, with producer progress, scored-through
progress and heartbeat shown separately, so a stalled scorer is not mistaken for a
stalled simulation. Opening a run folder by path works too.

## Alternatives that were live

- **Scores inside the frames**, with ADR 0075 loosened: less code, but wrong cache
  reuse is silent.
- **A separate live-run object with its own plotting paths**: clean isolation, but
  every view written twice.
- **Converting the dump to extxyz** so FFAST's existing reader works: every frame
  stored twice, which does not scale to the larger molecules planned later.
- **Storing attribution for every frame**: always available offline, but a new
  format that grows with molecule size.

## What this gives up

- New code: a LAMMPS dump reader with an incremental index, a run-folder format,
  and a small view.
- Attribution for non-alarm frames needs the bundle and a compatible DetectAna
  version; if either is gone, FFAST says so instead of showing it.
- Scores do not travel with exported frames yet.
