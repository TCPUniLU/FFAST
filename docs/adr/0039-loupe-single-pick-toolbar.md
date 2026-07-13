Status: Accepted (implemented; GUI-verified 2026-07-13)

# Loupe pick tools: one toolbar, one owner, contextual strip

The 3D View (Loupe) sidebar exposes five atom-picking tools, each behind its own **Select**
button buried in a separate collapsible pane: Atom Filter (`loupeAtomFilter.py`), Bonds
(`loupeBonds.py`), Align (`loupeAtomAlign.py`), Atoms Info (`loupeInfoSelect.py`), and Force
Vectors (`loupeForceVectors.py`). All five drive the *same* single-valued slot
`InteractiveCanvas.activeAtomSelectTool` (`UI/loupe/canvas.py:342`), so exactly one is ever
armed at runtime — but nothing tells the user *which* one. To pick atoms they must find the
right pane, expand it, and hit its Select; to know what a canvas click will do they must
remember which button they last pressed. This is the classic **mode-slip** trap: tools hidden in
a hierarchy, active state invisible.

**Decision (proposed):** consolidate all pick tools into one flat **Pick Toolbar** on the canvas
(the only place tools live) and surface the active tool through redundant signals, following the
established modal-tool model (NN/G "Modes"; Blender/Photoshop active-tool systems).

- **Tool vs operator vs setting.** Only *modes* (the five pickers) become toolbar tools.
  One-shot **operators** (Create filtered dataset, Bonds dynamic-fill, Force clear, Save image)
  stay as plain buttons in their panes. **Settings** (coloring, colormap, unit cell, FPS, …)
  stay as pane fields. The per-pane Select buttons are deleted.
- **Redundant active-state (≥2 signals).** Extend `setActiveAtomSelectTool` to, alongside its
  existing single-slot flip: (1) depress/highlight the owning toolbar button, (2) change the
  canvas cursor, (3) label the existing `atomSelectBar` strip (`canvas.py:232`) with the tool
  name + live pick count. The strip also hosts that tool's inline options (pick radius, 3-atom
  indices) — Blender's "tool settings in the header."
- **Tool ↔ pane handshake.** Arming a tool auto-expands its owning pane (target field visible);
  releasing the tool (strip ✕, or collapsing the pane) resets the slot to `None`.

## Why

- The arbitration already exists — `activeAtomSelectTool` is a single slot with show/hide of the
  strip. The gap is purely discoverability and active-state feedback, which is a wiring change,
  not a redesign.
- Five hand-placed Select buttons across five panes is five spots to hunt through and the reason
  the current owner is ambiguous; one toolbar makes tools "accessible as fast as possible"
  (the tool-palette guidance) and gives them a single visual home.
- Separating one-shot operators from persistent tools removes the conceptual muddle of a "Select"
  mode sitting next to a "Create" action in the same pane.

## Consequences

- The scientific behaviour of each tool is untouched — same tool classes, same
  `SET_SELECTION` / `SET_PARAMETER` view commands (ADR 0014/0015). Only where they are launched
  and how their active state reads changes.
- Enables the broader sidebar regroup (10 flat panes → Playback / Appearance / Analysis / View)
  and contextual visibility, but those are independent follow-ups; this ADR is scoped to the
  pick-tool consolidation.
- Atoms Info stops being a "tool with a pane"; its read-out (position/distance/angle/dihedral)
  moves to a canvas status line, leaving Info as a pure pick tool.

## Surfaced during GUI verification (implemented here)

Consolidating the tools exposed that a picked selection had no visual feedback, which in turn
masked two selection bugs. Folded into this change:

- **Selected-atom highlight.** The adapter now renders the active tool's `selectedPoints` as a
  client-local green halo (a sphere drawn behind the atoms with depth-test off, so only the rim
  shows and the atom's own colour stays visible). Without it, every pick tool felt broken.
- **Bonds picking fixes.** `fixedBondIndices` defaults to `None`, so the first pick crashed on
  `set(None)`; and picking against an empty fixed set collapsed the view to the single new bond.
  Fixed: guard the `None`, and seed the fixed set from the currently-shown (dynamic) bonds so
  picking an existing bond erases just that one.
- **Toolbar affordances.** Buttons share the bar width and carry hover/active QSS highlight.

Tests: `tests/ffast/test_loupe_pick_toolbar.py` (registry contract, id↔displayed mapping, bond
seed/erase/None-default).

## Out of scope (future ADR)

The sidebar regroup, VIEW-SETTINGS dissolve, the two-"filter" rename, and dropdown contextual
visibility are captured in `3d-view_redesign.md` — a separate change, not done here.

Refs: [NN/G — Modes in User Interfaces](https://www.nngroup.com/articles/modes/) ·
[Blender — Tool System](https://docs.blender.org/manual/en/2.93/interface/tool_system.html)
