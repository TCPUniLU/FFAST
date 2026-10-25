# Metric inputs across whole-dataset and frame scopes

**Status:** Implemented in the working tree (2026-10-25) · **Target agent:** GPT-6 luna, medium reasoning  
**Decision:** ADR 0046 amendment of 2026-10-25  
**Scope:** The second candidate in the 25 October architecture review. This is one focused change, not the Prediction or Render scene candidate.

## Outcome

A metric input ref has one meaning wherever the metric runs. A panel can request
all frames; atom colouring may request only the displayed frame when a full
result is unavailable. Both paths recognize the same ref, report unavailable
inputs clearly, and preserve the existing shape and colourability rules.

Example: a user formula `norm(R)` with `R = reference.positions` works in a
panel today. In the frame-colouring fallback, the frame adapter accepts only
`frame.positions`, so it cannot run that same formula. After this change,
`reference.positions` returns the displayed frame's **raw, untransformed**
coordinates there. `frame.positions` remains the separate visualization-stage
name used by atom and transform stages.

## Current code to inspect first

| Concern | Current location |
|---|---|
| Valid metric refs and field-ref pattern | `ffast/metrics/inputs.py` |
| Expression raw-ref shapes | `ffast/metrics/expr.py`, `_RAW_REF_SHAPE` |
| Metrics pane labels and offered refs | `ffast/metrics/input_catalog.py`, `_RAW` |
| Full-dataset values and prediction fields | `ffast/metrics/input_resolver.py`, `InputResolver.resolve` |
| Current-frame values and fallback | `ffast/visualization/color_values.py`, `_resolve_symbolic_ref` and `resolve_atom_color_values` |
| Prediction field storage | `ffast/core/loading_coordinator.py`, `_extractPredictionFields`; `ffast/core/data_service.py`, `predictionFields` |
| Frame's raw coordinates | `ffast/visualization/scene_builder.py`, `build_scene` |

Read ADRs 0023, 0046, 0058, 0064 and 0066 before editing. The October review's
`docs/research/architecture-review-2026-10-14/03-metric-inputs-table.md` describes
the original problem, but its line numbers and warning claim have aged; use
current code as evidence.

## Required behavior

1. **One definition of formula raw inputs.** Put their canonical ref, metric
   shape, display label and suggested variable name together in the metrics
   package. Make expression validation and the Metrics pane read it. Dataset
   fields keep their key-in-ref pattern and derive shape from `info` versus
   `atoms`; do not enumerate file keys globally. Retain the wider ref set used
   by Python metrics, including refs that Expression metrics deliberately
   reject.
2. **Two real adapters.** Keep full-dataset and current-frame fetching as
   separate implementations behind `InputSource`. The frame adapter accepts
   canonical metric refs, especially `reference.positions`, with per-atom
   values matching the displayed frame and offsets `[0, atom_count]`. Do not
   rename visualization-stage refs or apply display transforms to metric inputs.
3. **Cover current-frame inputs that can colour atoms.** Audit each continuous
   per-atom raw ref and `reference.atoms.<key>` / `prediction.atoms.<key>`.
   Implement frame fetching where the data exists. For prediction atom fields,
   use the server's stored prediction fields and map the displayed dataset
   view to the correct parent frame and atom mask; verify this with a subset
   test. Do not read a prediction file again or start whole-dataset inference
   merely to colour one frame.
4. **Preserve valid limits.** Per-frame energies and `*.info.<key>` are not
   per-atom colours by themselves. Formula Check may accept them for panels.
   Existing `metric_colour_problem`, shape checks and whole-result-first logic
   continue to govern colouring. An unavailable ref or a non-colourable result
   follows ADR 0066's user-facing warning path with a reason; element colours
   remain the visual fallback.
5. **Keep existing identity and execution behavior.** No changes to metric IDs,
   formulas, cache keys, worker-pool transport, result shapes, or the public
   `MetricExecutor.run(id, source, parameters)` seam.

## Implementation order

1. Write a focused failing test for `norm(reference.positions)` when
   `get_metric` returns `None` and atom colouring must run on one frame. Assert
   the actual per-atom values and that display transforms do not change them.
2. Consolidate raw input metadata. Move the Metrics pane and Expression shape
   checks to it without changing their lists or validation outcomes. Add a
   parity test for the supported raw refs and dynamic field-ref shapes.
3. Align the frame adapter with canonical refs. Add a small table of expected
   frame behavior for each colourable ref. Extend prediction atom-field access
   only after tracing parent/view indexing in the current storage form.
4. Verify a missing value and a non-colourable formula both show an actionable
   reason through the existing warning path. Keep the warning once-per-change
   behavior from ADR 0066.
5. Run focused tests, then the repository's full required test gate. Review the
   diff against this brief and the ADR amendment.

## Acceptance checks

- `reference.positions` produces the displayed frame's raw coordinates in the
  frame adapter and all frames in the full-dataset adapter; `norm(R)` colours
  atoms when frame fallback is needed.
- The catalog, Expression validation and both adapters agree on canonical
  names and shapes for supported inputs. Adding a raw input requires one
  definition and scope-specific fetching, with a parity test that catches an
  omitted adapter.
- Available reference and prediction atom fields resolve to the correct atom
  rows for a normal dataset and a `DatasetView`; missing fields yield a clear
  warning and element-colour fallback.
- Per-frame or otherwise non-colourable results are not advertised as per-atom
  colours. Formula Check still accepts valid panel formulas.
- Existing whole-result colouring, per-element broadcasting, missing-prediction
  behavior, and worker-process execution keep passing.

Start with `tests/ffast/test_atom_coloring.py`,
`tests/ffast/visualization/test_metric_atom_coloring.py`,
`tests/ffast/test_input_catalog.py`, and `tests/ffast/test_dataset_fields.py`.
Run them with `~/.venvs/ffast_env/bin/pytest -q <test files>`; that venv has
`pytest-xdist`, so the configured `-n 6` works (add `-n 0` to debug).

## Guardrails

- Do not turn every metric into an atom-colourable metric. Output shape and
  formula reductions still decide that.
- Do not add a new execution engine or merge the two `InputSource` adapters.
- Do not change the visualization-stage meaning of `frame.positions`.
- Do not edit unrelated, pre-existing worktree changes.
