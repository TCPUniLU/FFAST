# VispySceneAdapter replaces the legacy Loupe render path

**Status:** Accepted / Implemented (Milestone 2 complete; legacy render path coexists until Milestone 5 client wiring)

`ffast/renderers/vispy/adapter.py` (`VispySceneAdapter`) is the target Qt/Vispy
renderer: it consumes renderer-neutral `RenderScene` data (`AtomScene`,
`BondScene`, `ForceScene`, `LabelScene`, `UnitCellScene`, `SelectionOverlay`) and
owns all Vispy drawing. It replaces the legacy **Render Path** — the per-feature
`CanvasProperty`/`VisualElement` drawing in `modules/loupe*.py` that read global
UI state and drew Vispy objects directly. The scientific/geometry logic those
modules owned has already moved into `ffast/visualization/stages/builtin/` and
the modules now delegate to it (strangler-fig migration).

The **Renderer Client** (`UI/Loupe.py`) is *not* deleted. It is slimmed to pure
rendering and interaction: the Vispy canvas, color-based picking, camera,
rubber-band selection, playback controls, and image export survive and become
the host for `VispySceneAdapter`. Only the per-feature drawing loop
(`addVisualElement`/`visualRefresh` → `element.draw()`) and client-side geometry
(`applyTransformation`/`setCurrentR`) die.

The client consumes scenes **only** through the RPC protocol that the server
already speaks (`OPEN_VIEW`/`VIEW_COMMAND` ↔ `SCENE_SNAPSHOT`/`SCENE_PATCH`) —
the same path for local and remote. Local desktop mode runs the managed local
server rather than calling `build_scene` in-process.

## Considered alternatives

- **Keep Loupe drawing objects as the permanent adapter** (Milestone 2's original
  wording). Rejected: maintaining two Vispy drawing paths — old
  `CanvasProperty`/`VisualElement` objects reading global UI state plus the new
  scene-consuming adapter — is the divergence the server-owned architecture
  exists to eliminate.
- **In-process `build_scene` shortcut for local mode** (RPC only for remote).
  Rejected: it is a *third* drawing path and reintroduces the same divergence.
  Local mode uses the managed local server so there is one consumption path.

## Boundary criterion

A drawing concern is a **server-side Render Primitive** if it is derived from
scientific data / Visualization State and must look identical across all renderer
backends (Vispy *and* the future web renderer). It is **client-local** if it is
viewport chrome or interaction feedback that depends only on camera + screen,
carries no scientific meaning, and each renderer may draw its own way.

Applying this to every `modules/loupe*.py`:

| Module | Disposition |
|---|---|
| loupeAtoms | `atoms` primitive + metric atom-color (science ported) |
| loupeBonds | `bonds` primitive |
| loupeForceField | `forces` primitive |
| loupeForceError | `forces` + value color |
| loupeUnitCell | `unit_cell` primitive |
| loupeIndices | `labels` primitive (atom-index text) — index-label stage still to build |
| loupeDisplacement | atom-color stage (consolidation to finish) |
| loupeAtomFilter | filter **stage** (not a primitive) |
| loupeKabschAlign | View Transformation **stage** |
| loupeAtomAlign | View Transformation **stage** (3-atom frame align) |
| loupeInfoSelect | selection → `SET_SELECTION`; `alignAtomsIndices` → `SET_PARAMETER`; distance/angle/dihedral → server **Metrics** (`ffast.distance`/`angle`/`dihedral`) shown in client toolbar |
| loupeGyradius | radius-of-gyration **Metric + plot** — not a scene primitive |
| loupeCamera | client-local |
| loupeExport | client-local |
| loupeAxes | client-local (screen-corner orientation gizmo, not a primitive) |

Note: an earlier backlog said "model an axes primitive for loupeAxes." That was
wrong — the existing axes is a camera-coupled screen gizmo, same category as
loupeCamera, so it is client-local. A world-space crystallographic axes (a/b/c
cell vectors) would be a legitimate primitive, but that is a separate future
feature.

## Deletion gate

The legacy Render Path may be deleted **only when (1)–(3) hold**:

1. **Scene-primitive parity** — adapter + pipeline reproduce the current viewer
   for `atoms`, `bonds`, `forces`, `labels` (incl. atom indices), `unit_cell`,
   and `selections`.
2. **All transforms are stages** — Kabsch, atom-frame align, displacement applied
   server-side; the client applies none.
3. **All filters are stages** — `loupeAtomFilter` consolidated.

Two further items are tracked separately and do **not** block render-path
deletion:

4. **Non-render modules relocated** — `loupeGyradius` lives as a Metric+plot.
   This gates emptying `modules/` wholesale, not deleting the render path.
5. **Client-local set rehomed** — camera, export, axes, picking, rubber-band, and
   playback live in the slimmed Renderer Client.

## Consequences

- **Client-side geometry transforms die.** The client draws scenes that stages
  have already transformed; `applyTransformation`/`currentTransformations`/
  `setCurrentR` are removed.
- **Picking stays client-local**, emitting `SET_SELECTION`. *How* the client
  picks once the adapter (not the legacy `VisualElement`s) owns drawing is
  decided in [ADR 0015](0015-client-side-ray-cast-picking.md): a client-side
  ray-cast against the scene geometry, mapped to a stable atom id
  (`AtomScene.atom_ids`). It is not a server primitive.
- **Measurements become server-owned.** `loupeInfoSelect`'s distance/angle/
  dihedral are server Metrics keyed on a Scientific Selection; the toolbar HUD is
  client chrome but displays server values, not client-computed ones. Updates are
  per-selection round-trips (1–4 atoms), not per-hover, so latency is acceptable.
- **The adapter is live the moment the client subscribes.** The server already
  emits `SCENE_SNAPSHOT`/`SCENE_PATCH`; only client consumption is missing.
  `VispySceneAdapter` is work-in-progress with respect to that client wiring, not
  dead code, and is covered by `tests/ffast/renderers/vispy/test_vispy_adapter.py`.
- Until the client is wired in (Milestone 5), both paths coexist; the old loupe
  modules remain the live drawing path.

## Note (as-built, 2026-07-01)

The Render Path retirement completed without deleting the loupe modules: each had
its drawing code stripped and was rewritten as a `ClientFeature` descriptor (the Qt
control UI for a server-side stage). `loupeProperties.py` is **kept** as the home of
`ClientFeature` / `DatasetFeature` / `AtomSelectionBase` / `CanvasProperty` /
`VisualElement` — the last still backs the rubber-band selection rectangle, so the
file is not deletable (porting the rubber band to a plain Vispy visual is optional
future work, not a blocker). See also ADR 0017 (client-feature descriptor replaces load hooks).
