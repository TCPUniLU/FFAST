# The force scatter is a density, with the worst components as dots

Status: Accepted 2026-11-02 (built).

## The problem

The "Forces Scatter" panel of "Basic Errors" plotted every force component,
true against predicted. The page fetched two metrics, `ffast.force_reference`
and `ffast.force_prediction`, each holding every component.

- Local, 55,010 structures (5.5M atoms): each reply ~133 MB; the page froze
  for 15.5 min drawing them (handoff 4, 2026-10-31).
- MeluXina, 2026-11-02, `shard_03` (1,773,605 structures, ~100 atoms each,
  ~530M components): the server failed to send `force_reference` at all
  ("RPC decode error: bytes object is too large", over 4 GB).

Found while building this: the page's vendored Plotly is the *basic* build
(scatter, bar, pie only). A `scattergl` trace silently falls back to an SVG
scatter, so every point was a page element. Measured: 50,000 SVG dots take
0.42 s to draw and 0.32 s per zoom.

The user picks outliers out of this plot, so they must stay visible and
selectable.

## The decision

1. **A new panel kind, `parity`.** It draws two server metrics:
   - `ffast.force_parity_density`: every component with a value on both
     sides counted on a 300×300 grid over the shared range of both axes.
     Row 0 holds the bin centres, the rows below the counts
     [predicted bin][true bin]. ~720 KB.
   - `ffast.force_parity_outliers`: the 50,000 components furthest from the
     diagonal, worst first: true, predicted, structure, atom within it,
     component. ~2 MB.
   Both work through the components in chunks of 10M, so the temporary
   arrays stay small. At 53M components: 1.15 s and 0.58 s.
2. **The density is an image** under the axes (log₁₀ count through viridis,
   empty bins clear), its colour bar a scatter with no points. The basic
   Plotly build has no heatmap. Hovering the density shows nothing; hovering
   a dot names its structure, atom and component.
3. **Clicking a dot opens its structure**, as clicking a per-frame point
   does. The structure travels in the dot's data, not its index.
4. **Sub works on this panel.** The panel still names `ffast.force_reference`
   and `ffast.force_prediction` as its x and y. The page does not fetch them
   (`DRAWN_ROLES` in `analysis.js`); the server reads them to turn a box into
   structures, as it already did for a per-atom scatter (`subbing.py`).
5. **With several predictions**, each one's dots get its series colour and
   only the first one's density is drawn: two would cover each other.
6. **The desktop client draws the panel as the scatter it was** (dropping
   the two new roles in `UI/panels_toml.py`). The desktop is being retired.

## Consequences

- On a small dataset (under 50,000 components) every component is a dot,
  and the plot looks as it did before.
- The parity kind is built-in only: the tab editor's kind list stays in step
  with the desktop's kinds (a test checks that), and the desktop has none.
- Amended 2026-11-02: the page now ships the full Plotly build, so
  `scattergl` draws with WebGL (50,000 points: 135 ms to draw, 7 ms per zoom,
  was 416 ms and 328 ms). The density stays an image; a heatmap would add
  per-bin hover.

## Alternatives

- **A random or error-weighted sample**, drawn as today. The bulk's shape is
  only approximate, and no image is needed.
- **Zoom to detail**: real points once the zoomed window holds under ~200k.
  Every point reachable, but a server round trip per zoom.
- **A Plotly build with heatmap** (the "cartesian" one). Per-bin hover, but a
  new vendored file; the image needs nothing new.
