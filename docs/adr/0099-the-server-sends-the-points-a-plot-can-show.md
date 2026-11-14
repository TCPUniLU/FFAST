Status: Proposed 2026-11-13. Builds on the plot thinning in the browser
(`thin.js`, 9627d5cc) and on result tags (afe25c92).

# The server sends the points a plot can show

## The problem

Clicking "Energy shift" in Basic Errors on shard_03 (1.77M structures, server
on MeluXina) took 4.4 s (timing log, 2026-11-13):

- the server computed all 14 metrics in ~0.5 s;
- it then sent 45 MB, three 13.5 MB arrays (one number per structure) among
  them: ~2.8 s;
- the page was busy ~2 s, mostly Plotly drawing 1.77M points per plot.

Three changes since then:

- websocket compression is off (deflate saved 5% and cost 849 ms per 13.5 MB);
- the page draws only the points a plot can show (`thin.js`);
- the page keeps results and the server answers "unchanged" (result tags).

Measured over the SSH tunnel to MeluXina, a 13.5 MB array still takes
~630 ms (~21 MB/s). So on the cluster, every result the page does not hold yet
costs ~0.6 s per 13.5 MB array: the first look at a tab, a new prediction, an
energy shift, smoothing, a moved subset. The page then draws ~3,000 of its
1.77M values. Almost everything sent is thrown away.

## Decision

A plot asks the server for **the points it can show**, not for whole metric
results, when a result is large. This is a **thinned view**: the plot's kind
(line or dots), the x (and y) range shown, and the plot's size in pixels. The
server keeps the full results, as now, and thins them with the same rules as
`thin.js`:

- a line keeps, in each pixel column, its first, lowest, highest and last
  point (so every peak and dip, and every gap, is still drawn);
- dots keep one point per 2 px cell (so a lone outlier stays).

The reply is the kept points' values and **their frame numbers**.

1. **Small results come whole.** Below ~100k values per series the server
   sends the whole result, as now, and the page thins it (`thin.js`): no wait
   on zoom. The server decides by the result's size; the page sends the same
   request either way.
2. **A zoom asks again.** After a zoom or pan settles, the plot asks for the
   new range. Until the reply comes, the stretched overview stays on screen,
   so the plot never goes blank. Measured over the MeluXina tunnel with 1.77M
   values (synthetic, 2026-11-13):

   | View | Server thinning | Sent | Click → points in the page |
   |---|---|---|---|
   | Line, whole range | 11–13 ms | 2,799 points, 33 KB | 22–33 ms |
   | Line, 10 % of it | 1–2 ms | 2,782 points, 33 KB | 11–16 ms |
   | Line, 1,000 frames | ~0 ms | 1,000 points, 12 KB | 8–12 ms |
   | Dots, whole range | 38 ms | 415 points, 8 KB | 46–48 ms |
   | Dots, zoomed | 14–20 ms | 483 points, 10 KB | 23–30 ms |

   Round trip on the tunnel: 6–12 ms. A wide real scatter can fill up to
   ~50,000 cells (~1 MB, ~50 ms more).
3. **A click names its frame.** Each kept point comes with its frame number.
   A timeline's x already is the frame number; a scatter's dots carry it. A
   click opens the frame with no further request.
4. **Peak labels stay in the page.** The thinned line keeps each pixel
   column's highest point, so the five worst frames are among the points
   sent, with their own frame numbers. The page picks them as now
   (`peaks.js`); the server has no second copy of that rule.
5. **"Download data (CSV)" fetches everything, then.** Only that click asks
   for the whole results (the existing metric request), with the panel busy
   meanwhile: one wait of ~0.6 s per 13.5 MB on the tunnel.
6. **What stays as it is:** tables and densities (already small), subsets
   that follow a plot's zoom (the server already works from the range shown),
   atom colouring, live-run plots (their own path), compute parameters such as
   energy shift and smoothing (part of what is asked, as now).

## Considered options

- **Thin only in the page** (`thin.js`, built). Fixes drawing, not sending:
  the first look at a tab on the cluster still moves 45 MB.
- **Send smaller numbers.** 4-byte floats halve the size but keep only
  ~0.01 eV at −50,000 eV, too coarse for energy errors. Byte shuffling plus
  compression would shrink random-looking digits by an unknown, likely small,
  amount and costs CPU on both ends.
- **The server draws a picture** (a density image of all points). Cheapest to
  send for dots, but loses hover values, clicks on points and the plot's own
  look; Forces Scatter already does this for 4 GB of force components
  (ADR 0078) and keeps its worst points as dots for that reason. It stays the
  answer for force components, not for one value per structure.
- **A click asks the server which frame is nearest.** Saves 4 bytes per point
  sent, costs a round trip per click.

## What this gives up

- A zoom on a large plot waits for the server (~10–50 ms on the cluster).
  Before, every zoom was local.
- The page no longer holds every value of a large result, so anything new
  that needs them (a statistic over the whole curve, say) must ask the server.
- The thinning rules live in two places: the page (small results) and the
  server (large ones). A test runs both on the same data and expects the same
  points.
