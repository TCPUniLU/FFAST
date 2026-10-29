# An element subset chooses its atoms by element, in every frame

Status: Accepted 2026-10-25 (grilling of `to_fix.md`, item 1). Built 2026-10-28.

## The problem

On a dataset whose structures differ, Extract Subset with "C" does not give
the carbons. The server turns "C" into atom numbers using frame 0 only
(`_on_create_subset` in `ffast/session/server_session.py`), and an atom subset
keeps those numbers in every frame (`DatasetView`, ADR 0058). In frame 50,
atoms 0, 3 and 7 are whatever atoms that structure has there.

The rest of FFAST already reads "C" the other way. Per-element metrics, the
Atomic Errors tab, `by=element` and `where=element(...)` test each atom's own
element, frame by frame (ADR 0064). Extract was the one place that did not.

## The decision

A new state of `DatasetView`, the **element subset**. It keeps the element
tokens ("C", "-H"), not atom numbers. An atom is kept when its own element
matches, the same test `where=element(...)` makes. Its fingerprint is the
parent and the elements kept, so "C" and "C -H" on a carbon-and-hydrogen
dataset are one subset.

- Typing only elements makes an element subset, on any dataset. On a dataset
  whose structures are all alike, it holds the same atoms as today's atom
  subset would.
- On a dataset whose structures differ, atom numbers (alone or mixed with
  elements) are refused: an atom number names a different atom from one
  structure to the next.
- **A frame with none of the elements stays, empty.** The subset's frame *i* is
  its parent's frame *i*. Metric reductions already give no value (NaN) for a
  frame with no atoms (`ffast/metrics/segments.py`), and distributions drop
  NaN, so plots show a gap. Export skips empty frames and says how many.
- The status line says how many frames hold the elements ("C in 812 of 1000
  frames").

## Considered options

- **Drop frames without the elements.** No empty structure would reach any
  code. Rejected: frame numbers would stop matching the parent's, and the view
  would need a frame map as well as an atom rule. Keeping the frames needs only
  the atom rule, and the guards against empty frames already exist.
- **One stored list of atom numbers per frame.** Rejected: its size grows with
  the dataset and the fingerprint would hash all of it, while the element list
  it would be made from is already there.
- **Refuse elements on datasets whose structures differ.** Rejected: that is
  the case the user needs.

## What it gives up

- Atom *k* of the subset is not the same atom from frame to frame, so anything
  that follows one atom across frames (`by=atom`) does not work on it. The same
  is already true of its parent.
- Code that sums over a frame's atoms without a guard (a user's own metric
  plugin) meets empty frames. Built-in metrics are guarded.
- The subset's energy is still the whole structure's energy, as for any atom
  subset.
- A new fingerprint formula. Sessions are not migrated (ADR 0008), so changing
  it later only costs re-making saved element subsets.
