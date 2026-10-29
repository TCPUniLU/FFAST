Status: Proposed

# Displacement as geometry — trajectory overlay

FFAST can show per-atom displacement only as a **scalar colour**. The
`ffast.displacement_stats` stage (`color_stages.py`) collapses the whole
`(T, N, 3)` trajectory to one RMS number per atom, and ADR 0016 surfaces it
through the `displacement` colour source. The analyst sees *how much* an atom
moved, never *where it roams*, in *what direction*, or *how correlated* the
motion is — exactly the spatial distribution a force-field validator wants.

This ADR adds **displacement-as-geometry**: draw many trajectory frames at once
as a translucent "ghost" cloud beside the solid current frame, so stable atoms
pile into saturated dots and mobile atoms spread into faint clouds. A single
pinned ghost frame falls out as a special case, giving A/B comparison of two
structures.

Three facts from the current pipeline shape the design (traced 2026-07-14):

- **The scene is single-structure.** `build_scene` fetches one frame
  (`ds.getCoordinates(idx)`, `scene_builder.py:71`) into one `atoms`/`bonds`.
- **"Reference vs prediction" is not two geometries.** The model predicts
  forces/energies on the *same* positions; the only distinct geometries FFAST
  holds are different **frames**. The overlay therefore compares frames, never
  predictions.
- **`getCoordinates(indices=…)` already batches** on both the local loader
  (`loader.py:433`) and the remote proxy (`remote_dataset.py:257`) — a window of
  frames returns in one call, local or remote.

## Decision

Add a **trajectory-overlay layer**: a second, translucent atom layer that leaves
the current frame's interactive layer untouched.

- **`RenderScene` gains `overlay_atoms: AtomScene | None`** (reusing the existing
  `AtomScene` type). The current-frame `atoms`/`bonds` are unchanged — still
  pickable and metric-colourable. The overlay draws beside them and is never
  picked.
- **`AtomScene` gains `alphas: list[float] | None`** — per-point opacity, applied
  by both renderers as a post-colour multiply (composes with element colours,
  time-ramp, and the ADR 0016 `color_by` colormap alike). `None` = fully opaque
  (behaviour unchanged). This is the per-atom transparency primitive; the overlay
  uses it for a recency fade.
- **Frame set:** a window `[start, end]` (default = whole trajectory) plus a
  target count `K`, sampled at an even stride within the window; one batched
  `getCoordinates(indices=…)` fetch.
- **Alignment:** each overlaid frame *and* the current-frame layer are Kabsch-
  aligned to a **user-picked reference frame** R (default = the current displayed
  frame). Both layers share R, so the solid anchor always sits inside its cloud.
- **Colouring**, independent of the current frame's colouring, one overlay mode:
  - **density** — element colours; overlaps saturate, spread fades (default).
  - **by-time** — frame index through a colormap ramp (recency as hue).
  - **metric** — the ADR 0016 path, per frame: compute the per-atom metric for
    *each* overlaid frame (`resolve_atom_color_values` is already frame-scoped)
    into a `(K·N,)` `color_by.values` — a **spatially-resolved error field**.
    Range is **presentation-fixed**: `vmin`/`vmax` come from `ffast.atom_color`
    (inherited from single-frame metric colouring), so there is no auto-range pass
    and no colorbar drift.
- **Applicability (v1): non-periodic, fixed-composition datasets only.** The
  feature is **unavailable** (not offered) when `isVariable` — no per-atom
  correspondence across frames, so alignment, recency, and per-atom metric are all
  undefined — or when a lattice is present, because rigid Kabsch is misleading for
  a diffusing periodic cell. Periodic support waits on a **future PBC-aware
  alignment**, gated behind it.
- **A/B is a special case, not a second feature.** A single pinned ghost frame is
  window `[R, R]`, `K = 1`; scrubbing the current frame against it is A/B
  comparison. No separate overlay machinery is deferred.
- **UI:** a new standalone pane in the **Analysis** group (ADR 0040), modelled on
  `loupeDisplacement.py`: enable, window start/end, `K`, align toggle +
  reference-frame picker, overlay colour mode, opacity. The existing scalar
  `displacement` colour source stays in **Color by**.

Server-built in `build_scene`, armed via a new `"trajectory_overlay"`
`enabled_features` flag plus a `state.parameters["ffast.trajectory_overlay"]`
dict; both already invalidate the atom/bond/overlay components through
`_STATE_TO_SCENE`.

### How v1 builds, in `build_scene`

1. **Gate.** If `isVariable` or a lattice is present, emit no overlay (the pane
   is not offered for these datasets).
2. **Sample** `K` frame indices at an even stride across `[start, end]`.
3. **Fetch** them in one `getCoordinates(indices=…)` call.
4. **Align** every sampled frame and the current-frame positions to reference R
   via the existing `kabsch_alignment` stage (heavy-atoms-only per its default).
5. **Assemble `overlay_atoms`** (an `AtomScene`): concatenated `(K·N, 3)`
   positions, tiled `sizes`, `atom_ids = None`, per-point `alphas` from a linear
   recency ramp (oldest faintest → most-recent most opaque), and colours per the
   overlay mode (element / baked time-ramp `colors` / `color_by` metric values).
   Points only — no ghost bonds.
6. The current-frame `atoms`/`bonds` build as today (aligned to R), fully opaque
   (`alphas = None`).
7. The overlay honours the same atom-filter keep-mask as the current frame.
8. **Guardrail.** Cap `K·N`; when a request exceeds it, clamp and surface a
   visible notice — never silent truncation or refusal.

## Why

- The cloud is the **un-reduced twin of `displacement_stats`** — same
  `(T, N, 3)` input, but it preserves the spatial distribution the RMS scalar
  destroys. Colour answers "how much"; the cloud answers "where".
- **Metric colouring is the payoff, not a casualty.** Because metric resolution
  is already frame-scoped, the cloud can be coloured by per-frame force error —
  a spatially-resolved error field impossible to get from the scalar or a plain
  density cloud.
- **One layer, two features.** The dedicated overlay keeps the current frame a
  clean, pickable, metric-colourable primitive and expresses both the ensemble
  cloud and single-ghost A/B without building the protocol twice.

## Consequences

- **Protocol delta is two optional fields:** `RenderScene.overlay_atoms:
  AtomScene | None` and `AtomScene.alphas: list[float] | None`. Both default to
  absent/`None`, so existing scenes are unchanged. Both renderers gain a second
  atom set and a post-colour `alphas` multiply.
- **Picking is preserved; only the overlay is unpickable.** The current frame's
  `atoms` keeps `atom_ids` and picking; the overlay sets `atom_ids = None`. No
  regression to the normal single-frame view.
- **Per-point alpha in metric mode costs renderer work.** vispy: overwrite the
  RGBA alpha channel after `_map_color_by` (`adapter.py:217`). Web: three.js
  `InstancedMesh` has no native per-instance opacity, so it needs a per-instance
  alpha attribute + an `onBeforeCompile` material tweak — and the metric-coloured
  cloud on the web additionally waits on the pre-existing `ffast-viewer.js`
  `color_by` gap (metric colouring already degrades to element colours in the
  browser). Density and by-time clouds work on the web path via `alphas` alone.
- **Build cost = K metric evals** in metric mode (each needs that frame's
  prediction forces), on top of one batched coordinate fetch + K Kabsch solves.
  The overlay is a static snapshot armed once, not a playback-time recompute, so
  the cost is paid per arm, not per frame step.
- **Coherence invariant:** both layers align to the same reference R. Changing R
  (or the anchor) re-aligns both. If the playback frame lies in the window it is
  drawn twice (opaque anchor + one ghost); harmless — the opaque copy sits on top.
- **Gated datasets get nothing in v1.** Variable-composition is out permanently
  (no correspondence); periodic waits for the PBC-aware alignment. Availability
  predicate: `not isVariable and getLattice() is None`.
- **By-time colouring bakes RGBA into `colors`** (presentation, not a metric to be
  ranged); metric colouring goes through `color_by`. Consistent with ADR 0016.

## Considered alternatives

- **Concatenate ghosts into the single `atoms` field** (no overlay layer).
  Rejected: it forces `atom_ids = None` on the whole scene (picking off), buries
  the current frame in the cloud, and makes A/B a separate future component. The
  dedicated layer keeps the current frame pristine and unifies both features.
- **Scalar layer opacity instead of per-point `alphas`.** Rejected: a single
  opacity cannot express a recency fade, which is the cue that gives the cloud a
  time direction. (The extra cost is per-instance alpha on the web path, accepted.)
- **Auto-range the metric colorbar over the shown frames, or the whole
  trajectory.** Rejected: shown-frame range drifts as the window/K change;
  whole-trajectory range costs T evals. Presentation-fixed is cheap, stable, and
  comparable across re-arms.
- **Render the cloud unaligned for periodic (raw diffusion) now.** Rejected: a
  rigidly-unaligned periodic cloud is as misleading as a rigidly-aligned one;
  gating until the PBC-aware alignment exists avoids shipping a confusing view.
- **Faster playback animation.** Rejected: temporal motion can't be compared
  region-to-region at a glance and never reveals the distribution.
- **A separate v2 single-ghost feature.** Rejected: window `[R, R]`, `K = 1`
  already is it.

Tests: a `build_scene` test that, with `trajectory_overlay` armed on a
non-periodic fixed-composition dataset, `overlay_atoms.positions` has `K·N`
points, `alphas` ramps oldest→most-recent, `atom_ids is None`, the current
`atoms` layer is unchanged and opaque, aligning to R reduces cloud spread versus
the unaligned frames, and the window/K sampling is honoured; in metric mode
`overlay_atoms.color_by.values` has length `K·N` with `vmin`/`vmax` taken from
`ffast.atom_color`. A gating test that the feature is absent for `isVariable` and
lattice-bearing datasets. A renderer-adapter test that `alphas` multiplies into
the final RGBA under each colour mode (element / by-time / `color_by`).

Refs: ADR 0016 (atom colour values + client maps), ADR 0014 (server-side
transforms/filter; primitive-vs-client-local boundary), ADR 0040 (Loupe sidebar
groups), `ffast.displacement_stats` (`color_stages.py`), `kabsch_alignment`
(`transform_stages.py`), `loupeDisplacement.py`, `loupeVideo.py`.
