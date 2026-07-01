# ADR 0005: Auto-compute plots on dataset+prediction selection

**Status:** Accepted / Implemented  
**Date:** 2026-05-28

## Context

Currently, after loading a dataset and its predictions, the user must manually click a **Load** button on every plot tab to trigger computation. This is friction: for small datasets the computation is fast and the user always wants to see all plots immediately.

The threshold between "small" and "large" already exists in the codebase: `UI/menuHandler.py:163` uses **3 GB** (file size) to decide whether to show the large-dataset loading dialog.

## Decision

**Trigger:** when the user selects a dataset+prediction pair in `DatasetModelSelector` (`UI/ContentTab.py`), auto-compute fires instead of waiting for a manual Load click.

**Behaviour by dataset size (reusing the 3 GB boundary):**

| Dataset size | Auto-computed | Manual (Load button still required) |
|---|---|---|
| < 3 GB (small/medium) | All plots and their tables | — |
| ≥ 3 GB (large) | Top-level energy error plot + force error plot (per-structure) + their tables | All other plots and tables |

**Table rule:** a table auto-populates when its corresponding plot has been computed (auto or manual). No separate Load needed for tables once the plot exists.

## Alternatives considered

- **Always manual** — current behaviour; high friction for small datasets.
- **Auto-compute on prediction load** — fires too early; user may not have selected which plots to view yet, and the pair is not yet known at load time.
- **Separate size threshold for compute vs. load** — adds configuration surface with no benefit; the 3 GB boundary already captures the "fast vs. slow" distinction.

## Consequences

- Small-dataset workflows require zero Load clicks after selecting a pair.
- Large-dataset workflows still protect the user from accidentally triggering expensive full-dataset computations, while immediately showing the most useful summary plots (energy MAE, force MAE per structure).
- `DatasetModelSelector` gains responsibility for firing the auto-compute signal; plot modules must handle being triggered externally, not just via their own Load button.
