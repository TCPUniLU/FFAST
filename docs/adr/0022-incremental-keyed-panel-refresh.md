# ADR 0022: 2D Panels refresh incrementally by diffing Series, not clear+rebuild

**Status:** Accepted

## Context

A 2D plot refresh was stateless: `visualRefresh` did `clear()` (destroy every
`QGraphicsItem`) then `_addPlots()` (recreate every **Series** from scratch). With
server-owned metrics (ADR 0019/0021), each computed **Metric Result** streams back as its
own `DATA_UPDATED`, so toggling the energy shift or loading a prediction fires a burst of
events; every dependent Panel then tore down and rebuilt *all* its Series — including the
ones whose data was unchanged — on the GUI thread. Measured GUI event-loop stalls of
120–533 ms (heartbeat probe) made scrolling the Basic Errors tab lag. Earlier mitigations
(refresh debounce, a bounded server compute pool to cap GIL contention) reduced but did not
remove the stalls; the residual cost was the synchronous per-Panel rebuild itself.

## Decision

Plot refresh is incremental. `plot()` keys each **Series** by content identity
`(dataset_fp, model_fp, sub-index)` (static overlays get a fixed synthetic key) and, against
the previously drawn item with that key, **skips** when the Series is unchanged, calls
pyqtgraph `setData(x, y)` in place when it changed, and creates a new item only when the key
is new. Series whose key is not revisited in a refresh are removed at the end — so add /
remove / reorder all fall out of the keyed reconcile with no separate "structural rebuild"
path. `clear()` is no longer called on the normal refresh path.

The change signal is a content hash of the drawn `(x, y)` arrays with an O(1) fast-path: if
the backing **Metric Result**'s `checksum` is unchanged the hash is skipped. This is correct
for config Panels (transform params are folded into the metric server-side) *and* legacy
plots that transform client-side (e.g. the smoothing slider), because the final drawn arrays
are what's hashed. `autoRange()` runs at most once per coalesced batch and only when ≥1
Series actually changed, so a streamed update no longer yanks a manually-zoomed view.

Applies to all `BasicPlotWidget`s through the shared `plot()` funnel. Legacy plots still run
their inline KDE/convolve each refresh (client-side, unavoidable) but skip the item
recreation + autoRange; Panels — whose draw is just `.values.ravel()` — get the full benefit.

## Consequences

- A Series now has identity across redraws (added to the glossary). The legend and Subbing
  already operate per-Series; this names it.
- Colour/symbol assignment must be stored per Series key so it stays stable when an item is
  reused rather than recreated.
- Pairs with the refresh debounce (one rebuild per coalesced batch) and the bounded metric
  compute pool; together they remove the measured GUI-thread stalls.
