# ADR 0003 — Dedicated selection tool for force vector atom filter

**Status:** Accepted

## Context

The force vector overlay (`loupeForceField`) needs an option to display arrows only for a user-chosen subset of atoms, to keep rendering tractable on large systems. The existing codebase already has `AtomFilterSelect` (multi-select, rectangle drag) and `AtomInfoSelect` (1–4 atoms for measurement). Only one `AtomSelectionBase` tool can be active at a time.

Three options were considered:

- **A** — Reuse whatever selection tool is currently active. Simple, but awkward: selecting 2 atoms for a bond measurement would hide all other force vectors.
- **B** — Reuse `AtomFilterSelect` specifically. Couples force vector visibility to the dataset atom filter — two unrelated concerns sharing state.
- **C** — A dedicated `ForceVectorSelect` tool with its own stored atom set.

## Decision

**Option C — dedicated `ForceVectorSelect` tool.**

Force vector visibility is a persistent display preference, not a transient measurement. It must survive switching to info-select or bond-select modes. Reusing `AtomFilterSelect` would silently alter which atoms are included in derived datasets when the user intends only to change arrow visibility.

## Consequences

- `forceVectorsAtomIndices` stored in Loupe settings, marked per-dataset (consistent with `alignAtomsIndices`, `atomFilterIndices`).
- The Force Vectors pane exposes: a **"Select atoms"** button (enter/exit `ForceVectorSelect` mode), a **"Filter to selection"** checkbox (apply the stored set), and a **"Clear"** button (reset to empty).
- Empty selection + filter enabled → zero arrows rendered (not a fallback to all atoms).
- Rectangle drag (Ctrl+drag) supported for large-system usability.
- No permanent highlight when `ForceVectorSelect` is not the active tool — the arrows themselves are sufficient feedback.
- **Variable-size datasets:** when filter is ON and any stored index ≥ current frame atom count, render zero arrows for that frame. The stored selection is preserved and resumes on compatible frames. When filter is OFF (show all), forces always render regardless of atom count changes between frames.
