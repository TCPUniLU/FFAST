Status: Accepted (implemented; GUI-verified 2026-07-13)

# Loupe sidebar: regroup panes, dissolve VIEW SETTINGS, disambiguate the two filters

ADR 0039 moved the pick tools out of the sidebar onto a toolbar, but the panes they left
behind are still a flat, unstructured list, and two of them are muddled:

- **VIEW SETTINGS** (`loupeViewSettings.py`) is a grab-bag of five unrelated concerns —
  Kabsch alignment, atom labels, a view-only atom filter, a highlight field, pick radius, and a
  3-atom alignment. It has no single theme; it is where settings go when they have no home.
- **Two different things are both called "filter".** VIEW SETTINGS "Filter indices"
  (`sceneFilterIndices` → `ffast.atom_filter`) *hides* atoms in the current view, while the
  ATOM FILTER pane "Indices" (`atomFilterIndices` → `createAtomFilteredDataset`) *extracts a new
  sub-dataset*. Same word, two index fields, two behaviours — users conflate them.
- **Alignment is split across two modules.** `loupeAtomAlign.py` owns the 3-atom pick tool plus a
  legacy hidden checkbox; `loupeViewSettings.py` owns the *visible* 3-atom UI; both write
  `alignAtomsIndices`. Kabsch alignment is a third, separate path.
- Coloring's dependent combos (Colormap, Prediction) are always shown even when Coloring is
  `Elements`, where they do nothing.

**Decision (proposed):** restructure the sidebar around concern, not module history.

- **Group the panes** under four collapsible headers: **Playback**, **Appearance**, **Analysis**,
  **View** (see `3d-view_redesign.md` for the full pane→group map).
- **Dissolve VIEW SETTINGS**, redistributing its items: atom labels → Atoms (Appearance);
  the view filter → a renamed **Hide atoms** pane (Appearance); Kabsch + 3-atom align → one
  **Alignment** pane (Analysis) that owns both modes end to end; pick radius → the contextual pick
  strip (ADR 0039); highlight field → an advanced entry beside the strip.
- **Rename the two filters** so the words carry the distinction: **Hide atoms**
  (`sceneFilterIndices`, view-only) vs **Extract subset** (`atomFilterIndices`, spawns a dataset;
  the pane formerly ATOM FILTER).
- **Contextual visibility for dependent combos:** hide Colormap + Prediction until Coloring ≠
  `Elements`, mirroring the existing `setHideCondition` pattern (`loupeBonds.py`).

## Why

- The flat list and the "misc" pane make settings hard to find; grouping by concern is how the
  Panel-driven main UI already organises itself.
- The filter naming collision is a genuine correctness-of-understanding bug — one filter is
  cosmetic and reversible, the other creates persistent data. The labels must not be interchangeable.
- One Alignment pane removes the cross-module split on `alignAtomsIndices` and puts the two
  alignment strategies where a user compares them.

## Consequences

- No server-side change: the same view commands and stages (`ffast.atom_filter`,
  `kabsch_alignment`, `atom_align`, `atom_color`) fire; only pane layout, labels, and which module
  builds each control move.
- `loupeViewSettings.py` goes away as a pane; its parameter registrations move to the modules that
  now own them (labels → loupeAtoms, hide-atoms → its own module, align → loupeAtomAlign). Care
  needed: several keys are `markAsPerDataset` and wired to actions (`applyAtomAlign`,
  `applySceneFilter`) — those wirings must move intact.
- Scoped deliberately narrow after ADR 0039; picking is untouched. `3d-view_redesign.md` is the
  working spec (layout, dropdown inventory, pane→group map) this ADR formalises.

## As implemented (deviations from the spec)

- **The ATOMS pane is renamed COLOR BY and moved to Analysis**, alongside FORCE VECTORS. Both are
  interpretive overlays (colour atoms by a metric like force error; overlay GT-vs-prediction force
  arrows), not structural appearance. Appearance is left as pure look/structure (DISPLAY, BONDS,
  UNIT CELL). Final groups: Playback [Index/Video]; Appearance [Display, Bonds, Unit cell];
  Analysis [Color by, Force vectors, Extract subset, Alignment]; View [Camera, Export].
- **Grouping** is done by reordering the panes after load and inserting a muted group header
  label (`ContentBar.arrangeGroups`, called from `newLoupe` via `Loupe.arrangeSidebarGroups`),
  not by nesting collapsibles — lower risk, same visual grouping. The group→pane map lives in
  `Loupe.SIDEBAR_GROUPS`; unlisted panes fall under an "OTHER" header rather than vanishing.
- **VIEW SETTINGS** is dissolved into two themed panes — **DISPLAY** (atom labels, Hide atoms,
  Highlight atoms, Pick radius) and **ALIGNMENT** (Kabsch + 3-atom, dependent fields hidden by
  `setHideCondition`) — rather than redistributing each item to a pre-existing pane. The parameter
  registrations + action wirings stay in `loupeViewSettings.py`, so no wiring moved.
- **Pick radius** stays in the DISPLAY pane instead of moving into the contextual pick strip
  (deferred — the strip has no options host yet).
- Contextual visibility uses the existing `SettingsWidgetBase.setHideCondition` +
  `SettingsPane.updateVisibilities` path; the raw Prediction row (not a SettingsPane control) is
  toggled by a parameter action on `atomColorType`.

Tests: `tests/ffast/test_loupe_sidebar_groups.py` (group map covers every pane once, group order,
tool→pane handshake targets, dissolved-schema labels).

Refs: [NN/G — Modes in User Interfaces](https://www.nngroup.com/articles/modes/)
