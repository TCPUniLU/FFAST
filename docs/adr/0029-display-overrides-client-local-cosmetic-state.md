# Display Overrides: client-local cosmetic state for Panel labels/legends and the 3D colorbar

Users can edit axis label text, legend text/font/position (2D **Panel**s) and label/unit/font/position
(the 3D **Loupe** atom-coloring colorbar) at runtime, with the change persisted across restarts. These
are **Panel Display Override** and **Colorbar Display Override** — two separate CONTEXT.md terms
sharing one pattern and one on-disk file, not a single umbrella concept.

## Why not fold this into existing config concepts?

- **Not a Presentation Parameter.** A Presentation Parameter is Metric-declared and schema-validated
  (colormap, units, range) and generates its control through the normal Parameter Schema pipeline.
  Label text, font size, and legend/colorbar position aren't Metric output concerns at all — they're
  UI chrome with no Metric or schema involved. (The colorbar's `vmin`/`vmax` stay a Presentation
  Parameter concern and are explicitly excluded from Colorbar Display Override.)
- **Not Visualization Configuration.** Visualization Configuration is a curated, mergeable, shareable
  partial override a person hand-authors and reasons about. A Display Override is silently rewritten
  by the app every time a user drags a legend or retypes a label — auto-written GUI state, never
  hand-edited. Mixing the two would make Visualization Configuration noisy and unreviewable.

## Identity: content-based, not positional

A Panel Display Override is keyed by `(Analysis Tab name, Panel Kind, bound Metric IDs)` — the
Panel's own CONTEXT.md-defined identity — not `(row, col)`. A positional key breaks silently when a
TOML panel is inserted/reordered; a content-based key just fails to match (safe fallback to default)
until the same Panel reappears. A Colorbar Display Override is keyed by the Metric ID the client
already tracks when populating the atom-coloring combo (`loupeAtoms.py`'s label→Metric ID map) — the
wire-level `AtomColorBy` descriptor deliberately carries no identity (ADR 0016), so this reuses an
identity that exists client-side for an unrelated reason rather than inventing one.

## Storage

One new app-managed file (parallel in spirit to Session Records under `~/.ffast/`, not part of
Visualization Configuration's Configuration Merge), with two top-level keys: `panels` and `colorbar`.
One file, one read/write/debounce path, because both are the same shape of thing (client-local,
content-keyed, cosmetic-only) applied to two different widgets.

## 3D colorbar: replacing a working widget, not extending it

The existing 3D colorbar is a `vispy.scene.ColorBarWidget` placed in vispy's own grid layout
(`canvas.py`). Vispy grid widgets don't float, drag, or accept text-edit events, so drag-to-reposition
— explicitly in scope — cannot be added to the existing widget. Colorbar Display Override instead
replaces it with a hand-built Qt overlay widget floated over `canvas.native`, the same technique
already used for the atom-select toolbar (`atomSelectBar`). This trades a small, free, vispy-native
widget for a bespoke one that re-implements gradient/tick rendering, in exchange for native Qt
drag/double-click-edit/scroll-resize. Considered and rejected: keeping the vispy widget and dropping
position from scope — rejected because it would silently ship a feature missing the one property
(drag) that was explicitly requested, with no cheap middle ground available.
