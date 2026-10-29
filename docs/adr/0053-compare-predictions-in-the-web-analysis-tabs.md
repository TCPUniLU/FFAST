Status: Accepted

# Compare predictions in the web analysis tabs

Reported from a GUI pass: "Browser version should be able to compare basic errors
and other 2d plot based metric for different prediction. Right now I was able to
plot only one at the time."

Correct, and it was a web-client gap rather than a missing capability. The
desktop has compared predictions since ADR 0021: each Analysis Tab owns a
`DatasetModelSelector` holding **lists** of models and datasets
(`UI/ContentTab.py`), and every Panel in the tab draws one series per
(model × dataset) pair (`UI/panels.py`, `PanelKind.draw` — "one watched-data
entry"). ADR 0045 Phase 3 built the browser twin against a single prediction.

## Where the single-prediction assumption lived

Three places, none of them the protocol:

1. The object rail is single-select — `_selectModel(fp)` *replaces*
   `_currentModelFp`.
2. `_syncAnalysisContext` passed that one `modelFp`, and `_fetchAndRenderPanel`
   requested each metric once against it.
3. Every `draw*` in `panels.js` built exactly one trace with `seriesColor(0)`.

The transport was already right. `MetricClient.request()` takes `modelFp` per
call and keys each request by `(metric, params, model, dataset)`, so N
predictions is N requests, and two series sharing a reference-only metric land on
one cache slot instead of computing it twice.

The vocabulary was already right too. CONTEXT.md's **Series** entry reads: "A
Panel draws one Series per pair its **Analysis Tab** selector covers; the legend
labels Series, **Subbing** turns a Series' viewport range into a
**SubDataset**, and a Series carries the stable identity `(dataset, model)`."
Every clause of that describes what this ADR builds. The domain model specified
multi-series panels; one of the two clients had not implemented it. No glossary
change was needed — which is the useful signal here, since a gap the glossary
already covers is an implementation debt, not a design question.

## The comparison scope is per tab, not global

The rail keeps driving the 3D view — it selects the single prediction whose force
arrows render — so overloading it would tie "compare four models in Basic
Errors" to "change what the 3D view shows". Each analysis tab instead gets its
own dataset/prediction selector, as the desktop does.

A tab **follows** the rail until you click its selector, then it is **pinned**;
the first click pins the rail's current choice and applies the toggle to it, so
clicking a second prediction adds it rather than silently dropping the one on
screen. Following buttons are outlined, pinned ones filled, so a tab with its own
scope is distinguishable at a glance.

`pairSeries` is a pure function: selected datasets × selected predictions,
dataset-major, skipping pairs where the prediction was not computed for that
dataset. Naming follows the desktop's own compaction rule for the same problem
(`GroupedTableKind.table_left_header`) — the prediction alone names a series
while one dataset is in play, and the dataset joins the name once several are, so
four models against one dataset do not print its name four times in the legend.

## A panel draws a list of series

`renderPanel(el, spec, series, ctx)` now takes the series list. Each kind decides
what the colour channel means, and the two grouped kinds match the desktop's
`atom_mode` flag exactly: more than one element selected → colour means element
and the series goes in the label; one element → colour means series.

Two kinds gained real behaviour rather than just more traces:

- **`table`** is now the prediction × dataset grid the desktop's `TableKind`
  draws. It was a one-cell table whose own comment conceded the shortcut ("the
  desktop grids models×datasets; the daily-driver single-object case is one
  cell"). A pair that was not computed shows `—` rather than shifting the grid.
- **`grouped_table`** gained the desktop's two modes. Several elements selected →
  rows are elements. Exactly one → rows are the series and the element moves into
  the column header, which is how per-element prediction comparison reads.
  Multi-element mode shows the **first** series only, as the desktop does
  (`table_value` reads `datasets[0], models[0]`) — element × series is not a
  shape either client has.

Smaller decisions: the density fill is a single-series affordance (overlapping
filled curves read as mud); the legend turns on only once a panel draws more than
one named thing, so a single-prediction panel looks exactly as it did; the
scatter diagonal spans every series' combined range and stays last so
`dataCurveCount` still excludes exactly the non-data trace.

A series whose every metric came back empty is **skipped**, not drawn as a gap —
one prediction that cannot compute a panel must not cost the panel its other
predictions.

## Subbing had to learn which series was selected

A box-select used to sub `_ctx.datasetFp` / `_ctx.modelFp`, which is now
ambiguous. `subInfo.curveSeries` maps each trace back to its **original** series
index (original, so a skipped empty series does not shift the mapping), and the
first selected point decides whose subset it is rather than mixing two datasets
into one SubDataset. A range-based selection on a lines-only kind names no curve
and subs the first series.

## Pure builders

Each kind split into a builder — `spec + series + ctx` → traces and layout, or
table HTML — and a thin draw that hands the result to Plotly or the DOM. Trace
colours, labels, ordering and table structure are now asserted directly:
19 tests through the existing zero-build harness, no canvas, no Plotly global, no
server. This is the same move ADR 0050 made for `RemoteBrowser`/`SessionOps`, and
it is what let the two table modes be verified at all.

## Verification

1214 pass, including the live-runtime analysis tests that drive a real server and
browser — `test_web_analysis_scatter_renders` and
`test_web_analysis_box_select_declares_subset`, the latter covering the subbing
path this ADR changed.

Not GUI-verified: the selector itself (following vs pinned styling), a
multi-prediction legend, and the two table kinds' new shapes.
