# A live run's scores are analysis values: read with gaps, never filled

Status: Built 2026-11-07 on branch `lammps-live` (bbb47f7..6cbad70), not merged.
Plan: `docs/implementation/2026-11-07-analysis-values-and-pause.md`.

## The problem

Every scored frame of a live run stores more than the plot shows: T² and SPE for
each track (bonds, angles, dihedrals, ring), the threshold of each, and the
chemistry counts. The live score card draws only the worst score ÷ threshold.
The user asked for "different types of scores as metrics, to build different
live panels that follow one of them" (`to_fix_v3.md`).

Two facts shape the answer:

- The scorer runs behind LAMMPS. A run with 1,000 frames may have scores for
  frames 0–949 only; a crashed scorer leaves a gap for good.
- A **Dataset field** (ADR 0023) is carried by a file and is valid for every
  frame or resolves to nothing. Scores come from the run's analysis folder, not
  the dump, and are partial while the run goes.

## What was decided

1. A run's scores become **analysis values**: one number per frame, read from
   the run's analysis. A frame without a score has no value, which is a gap in
   the line.
2. **The viewer never fills a gap by scoring the frame itself.** The scorer's
   scores are the ones the alarm rule saw and the ones that can stop the run.
   A plot that mixed in numbers from a second source could show a mark the
   alarm never saw. Most gaps close within seconds as the scorer catches up.
   A gap after a crash says, truthfully, that the frame was never scored.
3. The values are each test's score ÷ its threshold (1 = threshold, so any two
   share an axis), the worst of them, and the counts of broken bonds and
   too-close pairs. Raw T² and SPE and planarity are left out for now; adding
   them later changes nothing else, while taking one out would break saved tabs.
4. They are ordinary entries of the metric list, in a group **Run scores**,
   usable on any timeline or density panel (ADR 0089: every tab works the same
   way). The list holds every test of every open run. A run without that test,
   and a dataset that is not a run, say in the panel why there is no value.
5. A panel following one value draws a plain curve. A ÷ threshold value gets the
   dashed line at 1; a count gets no line. Marks, alarm bands and alarm moments
   stay on the live score card, which is unchanged: they belong to the whole
   analysis, and a mark under a curve far below its line reads as a bug.
6. Every metric panel showing a live run redraws with each live update,
   geometry metrics included, so panels side by side end at the same frame.
7. A run shows its first analysis only, as before (ADR 0089, "Analyses").

## Pausing a tab

Panels that follow a run move while the user studies them. A tab gets a
**Pause** button: its plots and its 3D frame range stop following; its Run card
and Events keep updating, so an alarm is still seen, and the Pause button says
"new alarm" until the tab resumes. Pausing is per tab, is never saved (a reload
ends it), and never touches the run, the scorer or Stop on alarm. It is
recorded here because it is part of the same change; it is easy to reverse and
needs no ADR of its own.

## Considered and rejected

- **Stretch Dataset field to cover analysis values.** One word for both, but
  ADR 0023's all-or-nothing rule would get an exception, and a value with gaps
  would hide behind a name that promises none.
- **A server metric calculated from the scores file.** Reuses the plumbing, but
  dresses a stored number as a calculation, and its cache would need to know
  about the scorer.
- **Score missing frames in the viewer.** The server already loads DetectAna and
  the bundle for attribution, so it could. Rejected for decision 2: two sources
  for one number in a safety tool, double work on the cluster node. Scoring any
  dataset on demand is ADR 0089's parked "OOD score as a metric".
- **Each panel shows its own value's marks, or the run's marks.** See decision 5.
- **Pause per run, or for the whole app.** Per run keeps the axes moving in a tab
  that shows several runs; the whole app cannot watch one run while studying
  another.

## What this gives up

- A plot of an analysis value ends a few frames before the run's last frame
  while the scorer catches up.
- After a scorer crash the gap stays until someone rescores the run (the cheaper
  answer named in ADR 0089: a second analysis folder).
- Redrawing geometry metrics on every update costs a recomputation over all
  frames of a long run; computing only new frames is a later change.
