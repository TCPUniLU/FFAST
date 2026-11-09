# Live tabs are ordinary tabs: a run is chosen in the selector

Status: Built 2026-11-06 on branch `lammps-live` (4c0fb77..5be55a9), not merged. Changes the live-run tab of ADR 0087. Plan:
`docs/implementation/2026-11-06-live-tabs-are-ordinary-tabs.md`.

## The problem

With two live runs open, each live tab showed a dataset × prediction selector
listing both runs. Ticking the other run did nothing: every live panel carried a
fixed run ID (`live_run`) and ignored the selector. The server even marked the
tab `has_data_selector = false`, but the browser draws the selector for any tab
with a 2D plot. Meanwhile the tab's 3D panel followed the left-hand list, so tab
"Live A" could show run B's structure beside run A's scores and attribution.
The user's words: "so misleading and not clear".

## What was decided

**Every tab works the same way.** Its selector chooses what its panels draw; no
panel is pinned to a run. A live tab is an ordinary tab made from one built-in
layout, **Live run**, with its run already ticked.

1. Live panels (score timeline, status, events, attribution) read the runs
   ticked in the tab's selector. The `live_run` field on a panel goes away.
2. The selector lists runs among the datasets, the same list as every tab. A
   run has no second row (see "Analyses" below). A plain dataset ticked in a
   live panel gets a message saying why it has no score.
3. A live tab opens **pinned** to its run: picking something in the left-hand
   list does not change it.
4. Each opened run still gets its own tab. With two or more runs open, a
   one-time hint says that ticking several runs in one tab compares them.
5. With several runs ticked: one score curve per run on a shared threshold line;
   one status row per run; one events list merged by time with a Run column;
   attribution explains the frame clicked last and names its run and step.
6. 3D: the default panel is the run's own view (amended 2026-11-06, see the
   amendment below; it was the main view). A plot click moves the 3D panel of
   the tab showing the clicked run (ADR 0056 rule 4, unchanged), so one own-view
   panel per run compares runs side by side.
7. A new tab control, **Same step in all 3D panels**, available to any tab like
   Energy shift. When on, panels showing different runs follow the same
   simulation step (nearest frame); between other datasets the ADR 0056 rule 3
   link is unchanged.
8. "Stop on alarm" stays in the tab (harmless, reversible). **"Stop now…"
   leaves the tab** and moves to the Live Runs dialog: a tab is for looking,
   and a button in a tab that can show several runs must never kill the wrong
   job.
9. The Live run layout is edited, saved and reset like any built-in tab; new
   runs use the edited layout. Live tabs themselves are never saved.

## Analyses

A run folder can hold several analyses, but today the scorer makes one and the
viewer reads the first. The selector therefore offers no analysis row: a choice
with one option is a control that does nothing, the bug this ADR removes.

**Idea, parked 2026-11-06 (not a commitment):** scoring could become an
ordinary calculation of a (dataset, reference bundle) pair, on demand, for any
dataset whose atoms match the bundle, with references filling the selector's
second row. A run's own analysis would then be a pre-calculated copy of one
pair, and only that pair could stop the run. Step 1 does not depend on it.

The user parked it after a first look: it changes the architecture for a need
nobody has yet. What that look found, so it is not redone:

- A reference cannot simply be a dataset. It is fitted on a training set and
  its thresholds calibrated on a *different* validation set; it holds inferred
  bonds and fitted statistics; a live run depends on the exact saved file
  (`bundle_sha256`). The same training set with another validation set gives
  other alarms.
- A reference acting as a model (a "prediction" in the second row) would reuse
  the plumbing but put references among force fields in every error metric.
- The cleanest shape found: a separate kind of object, "Reference", beside
  Predictions in the selector, recording which datasets it was made from
  (bundles record none today; `manifest.json` has the molecule and date only).
- Scoring needs only DetectAna's `score`, `exceedance` and `check_chemistry`
  on positions, plus the atom-order check. Alarms also need time, which plain
  datasets lack.

Reopen when a real need appears: scoring a training set or a finished
trajectory, rescoring a run with a new reference, or more than one analysis
per run. A cheaper first answer to "rescore this run" is a command that writes
a second analysis folder (the run folder already allows it) and a choice of
analysis in the viewer.

## Considered and rejected

- **Remove the selector from live tabs** (obey `has_data_selector = false`).
  Fixes the symptom, but keeps live tabs a special kind and makes comparing runs
  need yet another special tab.
- **A dedicated "Compare runs" tab.** One more kind of object with its own rules.
- **Stop buttons on every status row** with a confirmation naming the run.
  Rejected for safety: stopping cannot be undone, and the urgent case is already
  covered by Stop on alarm.
- **Follow by frame number between runs** (ADR 0056 rule 3 as is). Runs saved at
  different intervals show unrelated moments side by side.

## What this gives up

- A live tab is no longer guaranteed to show only its own run; its name says
  which run it was opened for, not everything ticked in it.
- "Stop now" is one dialog away from where the scores are watched.

## Amendment — a live tab's 3D panel is the run's own view (2026-11-06)

Rule 6 made the Live run layout's 3D panel the main view. Opening a run then
replaced whatever the main view showed, and two live tabs fought over the one
main view. The layout's 3D panel is now an own view (`view = "independent"`,
`link_frame = false`): it shows the run ticked in its tab, at its own frame.
Opening a run only opens its tab; the main view keeps what it shows (an empty
main view still takes the first dataset that arrives, as for any dataset).

Attribution follows this: an attribution card explains one 3D panel of its tab,
chosen in the card's caption ("Explains b · …"), the tab's first 3D panel by
default. It asks DetectAna about that panel's frame and colours that panel. The
choice is saved in the card's `options.explains` (the panel's place among the
tab's 3D panels, in layout order). A tab without an attribution card leaves
attribution on the main view, as before (ADR 0087).


## Amendment — own live views follow incoming frames (2026-11-09)

An own 3D view of a running live run follows its newest available frame by
default. Merely extending the slider range left the molecule frozen at frame
0, contrary to the live tutorial's promise. Following now reacts to both
dataset-length announcements and live-run updates, whichever arrives first.

Selecting an older frame through the slider, frame buttons, or score plot
holds it for inspection. Selecting the newest frame resumes following. Tab
Pause freezes the frame and its range without forgetting whether the view
was following; Resume catches up only for a view that was following. With
Same step enabled, the focused view drives the others rather than each run
choosing a different newest step. Frame-linked panels keep their existing
main-view link. A run opened after it has ended starts at frame 0 as before.

Following is transient state of each own view, kept across a layout rebuild
that keeps that view; it is not a tab-file setting or a change to server-owned
geometry. Frame changes still go through the ordinary SET_FRAME command.
