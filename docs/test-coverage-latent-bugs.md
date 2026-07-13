# Latent Bugs Surfaced by Test-Coverage Work — ALL RESOLVED

Companion to `docs/test-suite-review.md`. While closing the review's "missing test
cases" (2026-07-07, branch `2ffast`), six production bugs were surfaced. **All six have
since been fixed** in the working tree: each function now guards/clips as described
below, and every pinning test was flipped from asserting the buggy behavior to
asserting the corrected behavior.

Full fast suite after the fixes: **1006 passed, 6 deselected**
(`pytest -m "not integration"`).

Priority key: **High** = wrong/opaque result on realistic input; **Medium** = wrong
result only on unusual input; **Low** = defensive hardening. Each entry is kept as a
record; **Status** notes the fix.

---

## 1 · `build_execution_plan` did not detect dependency cycles — High

Was: `build_execution_plan`'s DFS used only a `seen` dedup set, so a cyclic metric
registry yielded a plan whose steps bound to each other with `failure=None`, and
`run_plan` then crashed with an opaque `AttributeError: 'NoneType' object has no
attribute 'values'`. `MetricGraph.freeze` detected the *same* cyclic graph cleanly.

- **Status: FIXED** — the DFS now also tracks the current recursion stack (a
  `visiting` set) and raises a clear `ValueError("Dependency cycle detected …")`,
  matching `MetricGraph.freeze`.
- **Pinning tests:** `tests/ffast/test_execution_context.py` —
  `test_build_execution_plan_raises_on_cycle`, `test_self_cycle_raises`,
  `test_metric_graph_freeze_does_detect_the_same_cycle` (contrast),
  `test_valid_diamond_dag_produces_correct_topological_order` (contrast).
- **Files:** `ffast/metrics/execution.py`, `ffast/metrics/graph.py`

## 2 · `accel_difference` divides by per-atom mass with no zero guard — Medium

`ffast/metrics/builtin/accel_metrics.py` — `accel_difference` divides the force error
by per-atom mass. A zero-mass atom yields `[inf, inf, nan]` silently (finite/0 → ±inf,
0/0 → nan); no error is raised, so the bad value propagates into coloring/plots as if
valid.

- **Status: FIXED** — `accel_difference` now raises
  `ValueError("… zero-mass atom(s) make acceleration undefined")` when any mass is 0.
- **Pinning test:** `tests/ffast/test_builtin_accel_metrics.py` (zero-mass case,
  asserts the raise).
- **Files:** `ffast/metrics/builtin/accel_metrics.py`

## 3 · `gyradius` divides by total atomic-number weight with no zero guard — Medium

`ffast/metrics/builtin/structure_metrics.py` — `gyradius` normalizes the
center-of-mass by the total atomic-number weight `sum(elements)`. All-zero elements
make the COM division `0/0` and the metric returns `nan` silently, no raise.

- **Status: FIXED** — `gyradius` now raises
  `ValueError("gyradius: total atomic-number weight is zero")` on both the
  trajectory and single-structure paths.
- **Pinning test:** `tests/ffast/metrics/test_structure_metrics.py` —
  `test_zero_total_weight_raises`.
- **Files:** `ffast/metrics/builtin/structure_metrics.py`

## 4 · Transform compiler silently shadows conflicting compute-param names — Medium

`ffast/metrics/transforms.py` (~line 269) — `compile_pipeline` does
`union_params.update(transform.compute_params)` per step. When two pipeline steps
declare the same compute-param name, the *later* step's schema (default, label, type)
silently overwrites the earlier one on the final compiled metric, with no warning.
Built-in transforms don't currently collide, so this is latent.

- **Status: FIXED** — `compile_pipeline` now detects a cross-step compute-param name
  collision and raises `ValueError("compile: duplicate compute-param name(s) …")`.
- **Pinning test:** `tests/ffast/test_transform_compiler.py` —
  `test_conflicting_compute_param_raises`.
- **Files:** `ffast/metrics/transforms.py`

## 5 · `parse_host_port` mangles IPv6 and accepts negative ports — Medium

`UI/menuLogic.py` (~lines 44-50) — `parse_host_port` uses `rsplit(":", 1)`, which
splits on the *last* colon. IPv6 addresses (`2001:db8::1`, `::1`) are silently
mis-parsed (`"2001:db8::1"` → host `"2001:db8:"`, port `1`) rather than rejected; they
require `[host]:port` bracket syntax. Separately, a negative port passes through
unvalidated (`"127.0.0.1:-8765"` → port `-8765`). An empty port string does correctly
raise `ValueError` (`int("")`).

- **Status: FIXED** — `parse_host_port` now accepts `[host]:port` bracket syntax for
  IPv6, rejects a bare (unbracketed) multi-colon address, and validates the port is in
  `1..65535`; each raises `ValueError`.
- **Pinning tests:** `tests/test_menuLogic.py` — `test_negative_port_is_rejected`,
  `test_non_integer_port_raises`, `test_empty_port_string_raises`, plus IPv6 bracket
  cases.
- **Files:** `UI/menuLogic.py`

## 6 · `atom_colors` does not clip out-of-range `dimming` — Low

`ffast/visualization/stages/builtin/atom_stages.py:68` — `atom_colors` computes
`rgba[:, :3] = atomColors[z] / 255.0 * dimming` with no clip. The parameter schema
declares `min=0.0, max=1.0`, but that bound is enforced only at the UI/param-input
layer, not in the stage function. Calling the function directly with `dimming=2.0`
doubles a white atom's channels to `2.0`; `dimming=-1.0` yields negative RGB.

- **Status: FIXED** — `atom_colors` now wraps the RGB computation in
  `np.clip(atomColors[z] / 255.0 * dimming, 0.0, 1.0)`, matching the schema's
  `min=0/max=1` contract.
- **Pinning tests:** `tests/ffast/visualization/test_builtin_atom_stages.py` —
  `test_atom_colors_dimming_above_one_is_clipped`,
  `test_atom_colors_negative_dimming_is_clipped`.
- **Files:** `ffast/visualization/stages/builtin/atom_stages.py`

---

## Coverage added in this pass

43 test files changed, +1637/−501 lines, two new files
(`tests/ffast/test_load_modules.py`, `tests/ffast/test_session_records.py`).

| Area | Coverage added |
|------|----------------|
| Metrics core | cycle handling (→ bug 1), jaxtyping shape inference, degenerate scientific inputs (zero-mass → bug 2, zero-weight → bug 3, single-atom/frame, NaN propagation), WorkerProcessExecutor+cache skip, `MetricFailure` passthrough |
| Config/transform | override-key order-independence + same-tab/kind collision, malformed/wrong-typed TOML, `compile_pipeline` empty-steps + conflicting params (→ bug 4) + `id=` override, KDE degenerate input, `parse_host_port` degenerate (→ bug 5) |
| Server/session | `_on_request_metric` / `_on_request_subdataset_arrays` / `_on_request_prediction_arrays` / `_on_view_command` parse-fail; session-records upsert/dedup/delete/load; inbound_router empty-args guard |
| Cluster | bootstrap skip + mkdir/scp failure branches; `connect_to_cluster` `fail_at` poll (JobStatus.FAILED) / node (address lookup) |
| Buffers | empty-array `[b""]` guard; end-to-end zstd through `BufferService` |
| Visualization | color_values ASE-mass-fallback failure + Dataset-Field refs; scene_builder atom-pipeline exception fallback + force-scene filter remap; color_stages `force_error` gradient; atom_stages dimming boundaries (→ bug 6); pipeline missing-input → `StageExecutionError`; vispy zero-atom scene + NaN/empty `_map_color_by` |
| Misc | `loadModules` dependency-order + circular detection; CLI `dataset keys` / `stages list·inspect·test` / `metrics test`; ghost-lookup None-fp / already-loaded skip / multi-key dedup; SubDataset chem zero-atom / parent-raises |
