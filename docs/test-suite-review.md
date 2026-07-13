# FFAST Unit Test Suite Review

Scope: 78 test files, ~13,000 lines under `tests/`. No `conftest.py` exists anywhere in the repo — each file manages its own fixtures/env doubles.

## Overall assessment

**Partially useful, trending strong, with a long tail of low-value tests and one real CI-hygiene bug.**

The suite is strongest exactly where the codebase's ADRs describe non-trivial consolidations or bug fixes (metric execution planning, cache-key parsing/pruning, per-element grouping, coordinate transforms, display-override identity, session reconnect). In those areas tests assert real numeric/behavioral outcomes and are clearly regression-driven — several explicitly cite the historical bug they guard against.

The weak tail clusters into a recognizable pattern: pydantic-model "field echo" tests (construct a model, assert the field you just set equals what you set), pure smoke tests (`imports_cleanly`, `not None`, "no exception"), and a handful of tests coupled to private internals (cache-key string formats, private attribute defaults, mocked-call identity) that would survive a broken implementation or fail on a harmless rename. These inflate file/test counts without adding regression protection.

Two structural issues matter more than any individual weak test:
1. `tests/test_local_server_render.py` spawns a real `ffast-server` subprocess with no `@pytest.mark.integration` and no `skipif` guard — unlike every other subprocess-based test in the suite — so it silently rides along in the "fast/unit" run (`pytest -m "not integration"`) and will hard-fail (not skip) on any machine without the console script and example data on `PATH`.
2. `tests/ffast/renderers/vispy/test_vispy_adapter.py` and `test_local_scene.py` construct a real vispy `SceneCanvas` without `QT_QPA_PLATFORM=offscreen`, unlike four sibling Qt test files that set it explicitly — one headless-CI box away from a hard failure rather than a clean skip.

Neither is visible from coverage numbers; both are exactly the kind of thing this review is meant to surface.

---

## Strong tests (keep, and use as the template for new tests)

**Metrics / execution / cache** (`ffast/metrics`, `ffast/cache`)
- `tests/ffast/test_execution_context.py:113-133` — asserts the two *distinct* failure messages for "missing required input" vs. "present-but-None required input" — this is the exact divergence bug ADR 0035 fixed between `build_metric_inputs` and the pool executor. Real regression lock.
- `tests/ffast/test_execution_context.py:151-156,189-202` — plan picklability and cache-hit-skips-`run_fn`, verified with a recording fake, not a mock — checks actual driver behavior.
- `tests/ffast/test_pool.py:199-284,346-354` — real subprocess crash recovery, hard-timeout kill (asserting the *effective timeout value* appears in the message, catching min/max sign flips), and the compiled-transform pickling regression — all against a real `multiprocessing.Process` pool, each tied to a documented past incident.
- `tests/ffast/test_metric_inference.py:29-67` — compares full schema tuples between hand-declared and signature-inferred registration of the real `force_mae`; would catch drift in the inference machinery itself.
- `tests/ffast/test_cache_key.py:41-56,60-65` and `test_cache_key_prune.py:50-84` — right-anchored `__`-key parsing and real registry-delete pruning of >3-segment keys, direct regression coverage for the "delete-not-persisted" bug.
- `tests/ffast/test_builtin_force_metrics.py:60-65`, `test_builtin_atomic_metrics.py:71-81`, `test_builtin_accel_metrics.py:110-116` — per-element grouping tested with elements given in *reverse* order plus hand-computed expected values; would catch an unsorted-groupby or index-mapping bug.
- `tests/ffast/test_metric_generation_spine.py:160-167` — transitive prediction-dependency edge case explicitly called out in the resolver's own docstring.

**Scene / visualization pipeline**
- `tests/ffast/test_atom_coloring.py:297,303` — metric compute-parameter flow-through and per-element→per-atom broadcast, with real numeric inequality checks between element types.
- `tests/ffast/test_scene_builder.py:624` — bond remapping under atom filtering, verified geometrically (every segment endpoint must coincide with a kept atom position).
- `tests/ffast/test_scene_builder.py:654`, `test_builtin_transform_stages.py:48,68` — real Kabsch alignment math (rotation + translation), numeric convergence checked.
- `tests/ffast/visualization/test_stage_registry.py:210,221` and `test_pipeline.py:92,101` — DFS cycle/missing-dependency detection and stage-error-surfacing, with message-content assertions.
- `tests/ffast/test_atom_coloring.py:56` — regression-locks a real historical bug (string shape predicate vs. Dim tuple).

**Display overrides / config / transforms**
- `tests/ffast/test_display_overrides.py:39-53,89-100` — nested-dict pruning on clear (exact shape after clear) and mtime-keyed disk-cache correctness.
- `tests/ffast/test_panel_display_override.py:157-173,212-236` — content-based (not object-identity) override matching across a fresh widget instance, and a specific pyqtgraph `LegendItem.updateSize()` gotcha with coordinate assertions.
- `tests/ffast/test_panel_display_override_identity.py:75-80` — the one true identity-collision test (same metric, different tab → no bleed-through).
- `tests/ffast/test_colorbar_display_override.py:101-135,336-358` — exact drag-snap thresholds and per-metric override isolation across a live metric switch.
- `tests/ffast/test_transform_compiler.py:139-154` — numeric parity between the compiled pipeline and the hand-written legacy metric it replaced.
- `tests/test_menuLogic.py:41-52` — explicit regression test for a named historical bug ("Nona bug"), exact expected tuple.

**Networking / server / cluster**
- `tests/ffast/test_pending_requests.py:43-86` — request coalescing (one wire send, both awaiters get the result) and dead-future purge after timeout.
- `tests/ffast/session/test_server_session.py:147-165` — msgpack list→tuple restoration for `prediction_keys`, a real wire-format footgun.
- `tests/ffast/test_control_events.py:106-135` — proves pydantic validation doesn't erase presence-vs-None distinction in dispatched kwargs.
- `tests/ffast/test_cluster_bootstrap.py:83-106` — asserts exact command ordering and substrings in the generated SLURM job script.
- `tests/ffast/test_protocol_messages.py:182-208` — drives the real pack/unpack helpers bit-for-bit.
- `tests/ffast/test_reconnect_token_recovery.py:33-37` — covers a named production bug (int vs. str job IDs on reconnect).

**Misc / renderers**
- `tests/ffast/test_subdataset_predictions.py:49-112` — real parent-walk chain for metric generation, plus real concurrent-request coalescing.
- `tests/ffast/test_generation_queue_robustness.py:41-73`, `test_look_for_ghosts_robustness.py:37-63` — verified against the actual `CacheKey.try_parse` segment-count/dtype-discriminator guards; genuine edge cases, not smoke tests.
- `tests/ffast/test_loupe_view_commands.py:61-116` — replays real recorded commands through the real server `VisualizationView`, catching a documented stale-version regression.
- `tests/ffast/test_cli.py:73-100` — checks specific stderr text and exit codes, not just `SystemExit`.
- `tests/ffast/renderers/vispy/test_local_scene.py:239-289` — deliberately builds an env double *without* the `_env_facets` shortcut to prove a code path reads through the composed `env.data`/`env.datasets`/`env.models` API rather than a flat facade — good defense against a helper masking a real bug.

---

## Weak or low-value tests

**Pydantic field-echo (construct model, assert field equals what you passed in — tests pydantic, not your code):**
- `tests/ffast/visualization/test_visualization_models.py` (most of the file, e.g. `:20,49`)
- `tests/ffast/visualization/test_scene_models.py:33,78`
- `tests/ffast/visualization/test_view_commands.py:22,51`
- `tests/ffast/visualization/test_protocol.py:26-38` (three near-duplicate Literal round-trip tests — collapse to one parametrized test)
- `tests/ffast/test_metrics_models.py` (whole file) — mostly exercises pydantic's discriminated-union/`extra="forbid"` machinery
- `tests/ffast/test_module_loader.py:97-106` — field echoes only
- `tests/ffast/test_set_dataset_ref.py:10-13`

**Weak/insufficient assertions (would pass even if the underlying logic were wrong):**
- `tests/ffast/test_metric_adapter.py:47-51` (`test_vmin_vmax_respected`) — feeds data whose own min/max equals the explicit vmin/vmax, so it can't distinguish "used the explicit values" from "fell back to `np.nanmin/nanmax`"; also only checks the (always-1.0) alpha channel, never RGB.
- `tests/ffast/test_metric_adapter.py:54-58` — only checks `colors[0]==colors[1]`, not the actual normalized value, so a wrong constant-case fallback would still pass.
- `tests/ffast/test_scene_builder.py:133` — only asserts `atoms is not None`; never checks the clamped index actually resolves to the last frame.
- `tests/ffast/visualization/test_builtin_color_stages.py:99` — comment in the test itself admits "just check shape and no crash."
- `tests/ffast/test_headless_closure.py:27-35` — valuable intent, but only checks import-time module names, says nothing about runtime GUI usage.
- `tests/ffast/renderers/vispy/test_vispy_adapter.py:104-115` — asserts private-attribute defaults (`_atom_markers is None`); breaks on a harmless rename, verifies nothing behavioral.
- `tests/ffast/test_loading_coordinator.py:169-177` — only checks identity of a method reference (`task_args[0] == coord.loadDataset`), not that anything actually loaded.
- `tests/ffast/test_session_token.py:46-49` — trivial dataclass-immutability check.
- `tests/ffast/test_data_cache.py:62-68` — `isinstance(k, str)` on a plain dict never touched otherwise.
- `tests/ffast/test_metrics_registry.py:46-56` — asserts `passthrough(3) == 6`; tests Python semantics, not the registry.
- `tests/ffast/test_builtin_energy_metrics.py:12-23`, `test_builtin_force_metrics.py:10-17` — identity check (`registered_fn is fn`); only catches total registration breakage.

**Redundant (subsumed by an adjacent, stronger test):**
- `tests/ffast/visualization/test_builtin_atom_stages.py:59` — shape-only check immediately followed by a known-values test that subsumes it.
- `tests/ffast/test_pool.py:99-102` — subsumed by `test_run_returns_correct_output`; adds subprocess overhead for no new assertion.
- `tests/ffast/test_tab_controls.py:56-57` — subsumed by the more specific `test_unknown_tab_control_raises` right after it.
- `tests/ffast/test_analysis_tabs_config.py:14-20` — weak relative to the more specific `test_basic_errors_tab_is_declarative` immediately following it.
- `tests/ffast/test_colorbar_display_override.py:77-81` — implicitly re-covered by every later test's first call.

**Brittle / coupled to implementation internals:**
- `tests/ffast/test_scene_builder.py:316-331` — asserts against the literal cache-key string format (`f"forces__{model}__{ds}"`) rather than `build_scene`'s public output; breaks on an unrelated cache-key refactor.

**Tests that verify fixture code, not application code:**
- `tests/ffast/test_connect_panel.py:255-278` (`test_error_kwarg_propagated`) — the "task" and "event push" under test are locally hand-written fixtures that never call into `cluster.connection`; it's a tautology test.
- `tests/ffast/renderers/web/test_web_server.py:79-193` — pure substring/slice checks on `ffast-viewer.js` source text (e.g. `"renderer: 'webgl'" in viewer_js_source`); confirms identifiers exist in source, never that the browser code behaves correctly. Brittle to harmless refactors, zero runtime verification.

**Partially dead coverage (fixture supports cases the tests never exercise):**
- `tests/ffast/test_connect_panel.py` — `FakeSlurmBackend` documents `fail_at ∈ {'submit','poll','node','ws'}` but only `'submit'` and (separately) `fail_ws=True` are actually invoked; `'poll'` and `'node'` failure paths are dead in the suite despite being explicitly built for.

---

## Missing test cases

**Metrics / execution (correctness-critical — highest impact if wrong):**
- `execution.py`'s `build_execution_plan` DFS silently truncates on a cycle instead of raising, unlike `MetricGraph.freeze`'s `TopologicalSorter` (which does detect cycles). No test drives an unfrozen, cyclic registry through `build_execution_plan`/`run_plan` to show what actually happens.
- No direct unit test of jaxtyping-driven shape inference (`signature.py`) asserting e.g. `Float[np.ndarray, "N_atoms xyz"]` infers `(dims.N_atoms, dims.xyz)`, or that an unknown axis name raises — only exercised indirectly via `registry.freeze()` on real builtins.
- Degenerate scientific inputs across `energy_metrics.py`/`force_metrics.py`/`accel_metrics.py`: zero total mass (division by zero in gyradius/accel_difference), single-atom or single-frame arrays, NaN propagation through MAE/RMSE.
- `WorkerProcessExecutor` + cache interaction: no test proves a cached dependency is never shipped to a worker process (the `InProcessExecutor` case is memory-verified but has no pool counterpart).
- `metric_adapter.py`'s `vmin == vmax → zeros_like` branch and `MetricFailure` passthrough are effectively untested (see weak-assertion notes above).
- `color_values.py`: the ASE-fallback *failure* path (no `getMasses`, ASE import fails) and the Dataset Fields (`reference.atoms.<key>`) coloring path have no test.

**Scene / visualization:**
- `scene_builder.py`'s atom-pipeline exception fallback (gray atoms, size 0.5 on stage failure) is untested.
- `_build_force_scene`'s `filter_enabled`/`atom_indices` remap branch (force-arrow subset under an active atom filter) is untested.
- `color_stages.py`'s `"force_error"` special colormap branch is untested.
- `atom_stages.py` atom-color dimming has no boundary test for `dimming > 1.0` or negative — the function never clips, so this is a plausible latent bug that a boundary test would likely surface.
- No test drives `pipeline.execute` with a genuinely missing external-namespace input to confirm the resulting `TypeError` is wrapped as `StageExecutionError`.

**Display overrides / config:**
- Identity-key order-independence: no test that the same metric ids in a *different order* collapse to the same `panel_key` (the `sorted(ids)` call is untested for reordering).
- Same tab + same kind but *different* metric ids not colliding is untested (only the tab-difference case is covered).
- Genuinely malformed TOML (`TOMLDecodeError`) and wrong-typed fields (`ValidationError` from a bad type, not just an unknown key) are untested.
- `compile_pipeline` raising `ValueError` on an empty `steps` list is untested.
- Conflicting compute-param names across pipeline steps silently let a later step overwrite an earlier one (`transforms.py:269`) — untested, and a plausible latent bug.
- KDE's degenerate-input branch (`<2` points / near-constant) is never directly hit.

**Networking / server / cluster:**
- `cluster/bootstrap.py`'s "already up to date" skip path and the `mkdir`/`scp` failure branches (raising `ClusterError`) are untested — only wheel-build failure is covered.
- `connect_to_cluster`'s `JobStatus.FAILED`/`COMPLETED`/poll-timeout branches and `get_node_address` failure are never driven end-to-end, despite the test fixture explicitly supporting `fail_at="poll"/"node"`.
- `save_session_record`'s upsert/dedup-by-`job_id` is untested (calling it twice for the same job could silently duplicate on a regression); `delete_session_record`/`load_session_records` have zero tests.
- **`ffast/session/server_session.py`'s most logic-dense handlers — `_on_request_metric`, `_on_request_subdataset_arrays`, `_on_request_prediction_arrays` — have no unit coverage at all.** They're only reached indirectly through the subprocess-based integration test (`test_array_transfer.py`). Given they own cache-key parsing, variable-array concatenation, and on-demand prediction generation, this is the single biggest coverage gap in the networking group.
- `_on_view_command`'s pydantic parse-failure path is untested in both `test_server_session.py` and `test_control_events.py`.
- `buffers.py`'s empty-array guard (`if not self._chunks: self._chunks = [b""]`) and an end-to-end zstd round-trip through `BufferService` (as opposed to `BufferTransfer` directly) are untested.

**Misc / renderers:**
- The actual plugin/module dependency-order loading system (`loadModules` glob + Kahn-order sort) has **no test at all** — `test_module_loader.py` only covers an unrelated metric-config loader. No circular-dependency detection coverage anywhere.
- CLI subcommands `cmd_dataset_keys`, `cmd_stages_list`, `cmd_stages_inspect`, `cmd_stages_test`, `cmd_metrics_test` have zero coverage.
- Ghost-lookup: the "model already loaded" skip branch is never actually hit because the test double's `__contains__` always returns `False` — the branch exists in production code but the test suite structurally can't exercise it as written.
- `SubDataset.getChemicalFormula` on a zero-atom dataset, or where the parent chain itself raises, is untested.
- vispy adapter: zero-atom `AtomScene`, and `_map_color_by` with NaN/empty values (only `vmin==vmax` is covered).

---

## Flakiness / isolation red flags

1. **[High]** `tests/test_local_server_render.py` spawns a real `ffast-server` subprocess but is **not marked `integration`** and has **no `skipif` guard**, unlike every other subprocess-based test in the suite (`test_array_transfer.py`, `test_web_runtime.py`). It will hard-fail — not skip — on any machine lacking the console script and `examples/data/dataset.xyz`, and it slips straight through `pytest -m "not integration"`, defeating the suite's own fast/unit-vs-integration split. Fix by adding the marker and matching the `skipif` pattern used elsewhere.
2. **[High]** `tests/ffast/renderers/vispy/test_vispy_adapter.py` / `test_local_scene.py` build a real vispy `SceneCanvas` (confirmed backend: PySide6/Qt) without setting `QT_QPA_PLATFORM=offscreen`, unlike four sibling Qt test files that do set it explicitly. `pytest.importorskip("vispy")` only guards against vispy being absent, not against a missing display — this is one headless-Linux-CI box away from a hard failure.
3. **[Medium]** `tests/ffast/test_pool.py` runs ~15 real-subprocess tests with tight timing windows (`max_runtime_s=0.3, grace_period_s=0.1`); plausible flakiness under a loaded or throttled CI runner.
4. **[Low]** `tests/ffast/test_connect_panel.py` uses real `asyncio.sleep(0.01/0.05)` to force task interleaving — soft timing dependency, low risk but not strictly deterministic.
5. **[Low, informational]** Global registry state (metric registry, stage registry) is shared and populated by import-time side effects across many files (`test_stage_registry.py`, `test_atom_coloring.py`, `test_scene_builder.py`, builtin-metric test files, etc.). Harmless today because registration is append-only and duplicate IDs raise loudly, but it's a latent cross-file coupling — any future test that re-registers an id, or that reloads a module, will hard-fail unrelated tests via import order. Not currently a bug, worth knowing about.
6. `tests/ffast/renderers/web/test_web_runtime.py` needs real Playwright + Chromium + pixel-color assertions — inherently flaky by nature, but correctly marked `integration` and `skipif`-guarded, so this is acceptable as-is.

---

## Suggested improvements

| # | Recommendation | Priority | Why |
|---|---|---|---|
| 1 | Mark `tests/test_local_server_render.py` with `@pytest.mark.integration` and add the same `skipif`-on-missing-asset guard used in `test_array_transfer.py`. | **High** | Currently breaks the fast/unit test run's whole premise on any machine without the server script + example data on PATH. Pure hygiene fix, five minutes of work. |
| 2 | Set `QT_QPA_PLATFORM=offscreen` before constructing `SceneCanvas` in the two vispy renderer test files (or add a shared fixture/conftest that sets it repo-wide for all Qt-touching tests). | **High** | One CI environment away from a hard failure; trivial to fix, currently masked by dev machines having a display. |
| 3 | Add direct unit coverage for `ffast/session/server_session.py`'s `_on_request_metric` / `_on_request_subdataset_arrays` / `_on_request_prediction_arrays` — these are the highest logic-density, currently-untested handlers, only reachable today via a slow integration test. | **High** | Biggest single coverage gap found; a regression here is currently only caught end-to-end, late, and slowly. |
| 4 | Add a cycle-detection test for `build_execution_plan`/`run_plan` on an unfrozen registry, documenting (and if needed fixing) the current silent-truncation behavior vs. `MetricGraph.freeze`'s explicit raise. | **High** | Real behavioral divergence between two supposedly equivalent paths in the metric execution stack; exactly the class of bug ADR 0035 was meant to eliminate. |
| 5 | Add a boundary test for `atom_stages.py` dimming with `>1.0`/negative values, and either add clipping or document the current unclipped behavior as intentional. | **Medium** | Plausible latent bug (out-of-range RGB), cheap to test, cheap to fix if real. |
| 6 | Rewrite `test_metric_adapter.py`'s `test_vmin_vmax_respected` to use data whose min/max differ from the explicit vmin/vmax, and assert actual RGB values, not just alpha. | **Medium** | As written it cannot fail even if vmin/vmax handling is broken — a false-confidence test, not a true-negative gap. |
| 7 | Delete or consolidate the pydantic field-echo tests (`test_visualization_models.py`, `test_scene_models.py`, most of `test_metrics_models.py`, `test_view_commands.py`, `test_protocol.py`'s three near-duplicates). Replace with one or two tests per model that check something pydantic itself doesn't guarantee (e.g. discriminator routing edge cases, `extra="forbid"` on a genuinely malformed payload). | **Medium** | These inflate the file/test count without adding regression protection; not harmful, but worth pruning so the "meaningful" tests aren't diluted for future maintainers scanning the suite. |
| 8 | Add module dependency-order / circular-dependency tests for the plugin loader (`loadModules`), and CLI coverage for `cmd_dataset_keys`/`cmd_stages_*`/`cmd_metrics_test`. | **Medium** | Currently zero coverage on a system explicitly designed around dependency ordering (Kahn's algorithm) — exactly the kind of logic that breaks silently when someone adds a new plugin. |
| 9 | Add malformed-TOML and wrong-typed-field tests for the config loader, and an empty-`steps` test for `compile_pipeline`. | **Low** | Real gaps, but lower blast-radius (config errors surface immediately and loudly at startup in practice). |
| 10 | Fix or exercise the "model already loaded" ghost-lookup skip branch — currently the test double structurally can't reach it (`__contains__` always `False`). | **Low** | Coverage-hygiene issue rather than evidence of an actual bug; flag it so a future refactor doesn't silently regress an already-untested branch. |
| 11 | Collapse clearly redundant test pairs (`test_pool.py` run_multiple_calls vs. run_returns_correct_output; `test_tab_controls.py` energy_shift_registered vs. unknown_tab_control_raises; `test_builtin_atom_stages.py` shape-only vs. known-values). | **Low** | Pure suite-hygiene; saves a little CI time, no behavior change. |

### Categorization, as requested

- **Increase real confidence in correctness:** the "Strong tests" list above (execution-context divergence tests, pool crash/timeout tests, Kabsch alignment, cache-key pruning, per-element grouping, display-override identity/pruning, session reconnect, pending-request coalescing).
- **Increase coverage numbers only:** pydantic field-echo tests, `imports_cleanly`/`not None` smoke tests, identity-of-registration checks (`registered_fn is fn`), trivial dataclass-frozen checks.
- **Brittle / too coupled to implementation:** the literal cache-key-string assertion in `test_scene_builder.py:316-331`; the private-attribute-default checks in `test_vispy_adapter.py:104-115`; the JS-source substring checks in `test_web_server.py`.
- **Should be rewritten:** `test_metric_adapter.py`'s vmin/vmax test (weak by construction, easy fix); `test_connect_panel.py`'s `test_error_kwarg_propagated` (currently tests its own fixture, not app code).
- **Should be removed or merged:** the near-duplicate `test_protocol.py` Literal round-trip tests; the three redundant pairs in item 11 above.
