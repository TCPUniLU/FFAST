Status: Accepted

# Stop presentation leaking across the Render Scene seam

ADR 0014 makes the Render Scene the one consumption path between the Headless
Core and a renderer: `build_scene` produces renderer-neutral primitives, and an
adapter translates them. The architecture review found the seam leaking in both
directions — the core baked bare RGBA literals into scenes, and a renderer
reached *back* through the seam into a private core helper and then ignored the
presentation the core had sent it.

Four leaks, each closed below. Two of the review's claims did not survive
checking and are recorded at the end.

## Arrow tessellation crossed the seam by private import

`ffast/renderers/vispy/adapter.py` did:

```python
from ffast.visualization.stages.builtin.force_stages import _arrow_mesh
```

ADR 0049 deleted the `ffast.force_arrows` stage, which is the only thing
`force_stages.py` registered. What was left was 133 lines of private helpers
inside the stage package with no stage in it — and `stages/builtin/__init__.py`
still imported the module for registration side effects it no longer had.

The review's remedy was "single-source arrow tessellation on one side of the
seam". There is exactly one consumer and there will not be a second: Vispy needs
a triangle mesh because it has no instanced-arrow visual, and the web renderer
builds arrows from `THREE.ArrowHelper`. So the mesh builder moved to
`ffast/renderers/vispy/arrow_mesh.py` as a public `arrow_mesh`, beside the
renderer that needs it. The seam is not crossed differently — it is not crossed.

`force_stages.py` is deleted, and `stages/builtin/__init__.py` now says in prose
why `force_stages` is absent while force arrows still render (they are built by
`scene_builder._build_forces` from the `ffast.force_arrows` *parameter*
namespace, which stopped being a registered stage at ADR 0049 — a distinction
candidate #04 will have to handle).

Pure numpy, so the tests need no GL context; the eight cases moved with it to
`tests/ffast/renderers/vispy/test_arrow_mesh.py`.

## The adapter re-typed the colour the core had already sent

`build_scene` filled `ForceScene.colors` with `[[0.9, 0.4, 0.1, 0.8]] * n`. The
adapter then created its mesh with `color=(0.9, 0.4, 0.1, 0.8)` — the same
literal, hand-copied — and **discarded the scene's colours entirely**. Both
sides agreed only by coincidence, and a per-arrow colour was unreachable however
the server set it: `ForceScene.colors` was dead data on the Vispy path.

`arrow_mesh` now also returns `arrow_index`, mapping each vertex back to the
arrow it belongs to **in the caller's numbering**. That indirection is load-
bearing: zero-length arrows are not tessellated, so colouring by tessellated
position would paint every arrow after a dropped one with its neighbour's
colour. The adapter builds `vertex_colors = colors[arrow_index]`.

A colour array shorter than the arrow list is a malformed scene. The first
attempt had the adapter detect that and fall back to `FORCE_ARROW_COLOR` — which
put a presentation default back inside a renderer, the exact thing this ADR
removes, and left the web client needing its own copy of the same fallback. The
invariant belongs to the scene, so `ForceScene` now validates
`len(colors) == len(starts)`. Both renderers index `colors` unconditionally and
neither imports a presentation default; a malformed scene fails at the model, not
silently at whichever renderer drew it.

## Presentation defaults had no home

The scene *should* carry colours — a `LabelScene` with no colours makes every
renderer invent one, the divergence ADR 0016 avoids by resolving `vmin`/`vmax`
server-side. The leak was that the values were bare literals at their use sites.

`ffast/visualization/presentation.py` now holds `NEUTRAL_ATOM_COLOR`,
`NEUTRAL_ATOM_SIZE`, `LABEL_COLOR`, `SELECTION_OVERLAY_COLOR` and
`FORCE_ARROW_COLOR`, each with the reason it is what it is (the label black, for
instance, is parity with the legacy `loupeIndices` text on the light Loupe
background). `scene_builder` is the only importer.

The review also named the force *scaling* constants alongside the RGBA — "bakes
literal RGBA **and force-scaling constants**". Those were `params.get(key,
literal)` defaults plus two bare divisors (`/ 5` when normalising against the
frame's largest force, `/ 500` when not). They are now `FORCE_LENGTH_FACTOR`,
`FORCE_NORMALISED`, `FORCE_NORMALISED_DIVISOR` and `FORCE_RAW_DIVISOR`, which
also documents what the divisors are for — the `/ 5` exists so the historical
slider default of 10 yields arrows about two length units long.

These are defaults, not settings. Making them client-settable needs the
Setting → Parameter map (candidate #04); until then a renderer must read them
from the scene and never from a literal of its own.

## The web client had two colour tables and they disagreed

`colormap.js` held `COLORMAP_STOPS`, which `mapColorBy` uses to colour atoms.
`panes/colorby.js` held `GRADIENT_CSS`, a hand-written hex table for the
colourbar. Same seven colormaps, two independent transcriptions — and they were
not the same colours: the bar carried the true matplotlib hexes while the atoms
were drawn from the file's own "compact public approximations". The colourbar and
the molecule disagreed about what a value looked like.

`gradientCss(name)` now derives the bar from the stops the atoms get, and
`GRADIENT_CSS` is deleted. Hex, not `rgb(...)`, so a stop never contains the
`, ` that separates stops. The test asserts the bar's stops equal `mapColorBy`
sampled at the same fractions, so the two cannot drift apart again.

This is the *intra*-web duplication only. The vispy-vs-web colormap duplication
is ADR 0016-sanctioned ("no shared mapping code across renderers") and is left
alone. The review also called out "the byte-sync test" as unsanctioned waste
alongside the double LUT — no such test exists, and grepping finds no test
referencing `COLORMAP_STOPS` at all. Nothing to remove; recorded so the item is
not looked for again.

## A defect this work found and did not fix

`static/renderer.js` scales force arrows by a renderer-local
`const scale = 0.5` before drawing them, while the Vispy path draws
`starts + vectors` at face value. **The same scene therefore renders force arrows
at half length in the browser compared to the Loupe.** That is exactly the
presentation ownership this ADR is about, and the fix is a one-line deletion — but
it changes what the browser looks like, and neither renderer's arrow lengths have
been visually compared. Left for the GUI pass rather than changed blind.

## Review claims that did not survive checking

**"Force-arrow logic lives in three places" — right, and about more than colour.**
The colour was in three places (core, Vispy adapter, web fallback) and is now in
one. An earlier draft of this ADR claimed the *scaling* lived only in
`_build_forces` and that "the two renderers draw what they are given"; the
`const scale = 0.5` above disproves it. The `_SHAFT_RADIUS`/`_HEAD_RADIUS`
tessellation constants are genuinely Vispy-specific with no Three.js counterpart.

**"Five hand-rolled index remaps" — the count is off, but not to one.** An earlier
draft of this ADR declined the item by claiming there is a single mapping applied
five times. That is not accurate. `build_scene` derives *three* index objects from
the one `keep` mask: `old_to_new` (compact ← scientific), `atom_ids =
np.where(keep)[0]` (its inverse), and the force branch's `compact` list in a third
space. The `0 <= i < len(...)` bounds guard is hand-written three times against
three different bounds — `len(keep)`, `len(old_to_new)`, `len(force_positions)`.

Still declined, but on cost rather than on the facts: the mask has one owner and
one derivation point, the three guards are each two lines, and folding them into
an index object means restructuring the whole of `build_scene`'s assembly — an
indexing concern, not a presentation leak, and so not this candidate's subject.
It belongs with candidate #04, which has to reason about the same namespaces.

## Verification

1204 pass. No renderer imports a non-public name from the core any more (the sole
such import was `_arrow_mesh`), and no renderer imports
`ffast/visualization/presentation.py` either — `scene_builder` is its only
importer, so a presentation default cannot be applied on the far side of the seam.

New tests: 11 for `arrow_mesh` (7 moved from the deleted stage-test file, 4 new
for `arrow_index`), 6 for the adapter's force colouring, 4 for `gradientCss`. The
colouring tests read the Vispy mesh's vertex colours back, so "the scene's colour
reaches the screen" is asserted rather than assumed — the property the old code
silently violated.

Not GUI-verified: force arrows in the Loupe and in the browser (see the
half-length defect above), selection-overlay highlighting, and the colour-by
colourbar — whose palette changes, deliberately, to match the atoms.
