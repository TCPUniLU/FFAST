# Picking is client-side ray-cast committing to a server selection

**Status:** Accepted / Implemented (`AtomScene.atom_ids` present; ray-cast picker in `VispySceneAdapter`)

ADR 0014 said picking "stays client-local" but did not say *how* a Renderer
Client picks once the scene adapter — not the legacy `VisualElement`s — owns
drawing. The legacy color-buffer pick (`getPickingRender` re-renders atoms with
per-index ID colors and reads back the framebuffer) draws through the very
elements the adapter replaces, so it stops working in adapter mode. This ADR
resolves the gap.

## Decision

Picking is resolved **client-side** and committed to the server as a
`SET_SELECTION` View Command. The **contract is normative for every Renderer
Client**; the pick **mechanism** is a per-renderer Renderer Capability.

Contract (all renderers):

- A pointer event resolves to the nearest atom and is mapped to a **stable
  scientific atom id** carried in `AtomScene.atom_ids`, then committed with
  `SET_SELECTION(name=target)`.
- The client holds the working index set **locally** (click toggles add/remove,
  rectangle adds a set) and commits the **full** set each time, because
  `SET_SELECTION` is replace-semantics server-side.
- Clicks target a **purpose-named** selection (default `"picked"`; features such
  as filter/measure set their own target name) — selections are isolated by
  purpose.
- **Hover is a client-local transient highlight** with its own visual; it is
  never sent to the server. A per-move round-trip would be unusable over the
  remote-mode network.

Vispy mechanism (this renderer's choice):

- CPU ray-cast against `AtomScene.positions` using the scene graph's existing
  canvas↔scene transform (`node_transform(...).imap`), off the render path.
- Click = nearest atom along the unprojected ray (occlusion-correct).
- Rectangle = project atoms to screen, test in-rect.

`AtomScene.atom_ids`: optional `list[int]`, the original/scientific structure
index of each displayed atom. `scene_builder` populates it with the kept
originals when an atom filter is active; absent means identity (`0..N-1`). This
keeps picked selections referencing scientific identity under filtering or
reordering.

## Considered alternatives

- **Adapter ID-color buffer pick** (port the legacy scheme into the adapter).
  Exact parity, including visible-only rectangle select. Rejected: it recouples
  picking to the adapter's draw internals, costs an extra render pass plus a
  `glReadPixels` GPU→CPU readback stall *per pick*, and forces every renderer
  (including the web one) to reimplement GPU picking. Ray-cast is off the render
  path, has no readback, and is decoupled from whatever draws the atoms.
- **Dedicated gloo/framebuffer GPU pick.** Most machinery, marginal benefit over
  color-buffer. Rejected.
- **Picking only when unfiltered**, or the client recomputing the server's
  filter keep-mask. Fragile and duplicates server logic. Rejected in favour of
  `atom_ids`.
- **Per-move `SET_SELECTION` for hover.** Unusable over the network. Rejected —
  hover stays a client-local transient.

## Consequences

- `AtomScene` gains `atom_ids`; `scene_builder` populates it. The client maps a
  picked displayed-index → `atom_ids[k]` before emitting `SET_SELECTION`.
- The legacy color-buffer picking in `UI/Loupe.py`
  (`getPickingRender` / `refreshPickingColors` / `colorToIndex`) is part of the
  dying render path. The Renderer Client gains a ray-cast picker plus a
  client-only transient-highlight visual (separate from server selection
  overlays).
- **Rectangle select changes semantics**: ray-cast selects atoms inside the drag
  rectangle including occluded ones; the legacy color-pick selected only visible
  atoms. Accepted as a minor, arguably-desirable change.
- Picking is decoupled from drawing, so it works identically regardless of which
  visual renders the atoms.
- The web renderer implements the same contract via its native raycaster; there
  is no shared GL picking code across renderers.
