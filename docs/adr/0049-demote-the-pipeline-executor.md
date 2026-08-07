Status: Accepted

# Demote the pipeline executor; keep the Stage Catalog

The **Visualization Pipeline** had two separable halves, and only one of them earned its cost.

The **executor** (`ffast/visualization/pipeline.py` plus `StageRegistry.resolve_order`) presented a
large interface: six external input namespaces, `stage.<id>.<output>` string addressing, dependency
derivation by parsing those strings, a topological sort with cycle and unknown-dependency error
modes, an output-arity contract, and a parameter overlay. The **catalog** (the `@stage(...)`
declarations, `StageRegistry`, `StageSchema`) is a declarative description of each stage: its inputs,
outputs, parameters with defaults, and inline test cases.

**Decision:** delete the executor. `scene_builder` calls stage functions directly, in the order
written. Keep the catalog.

## Why the executor went

Three measurements, taken before deciding:

1. **It ordered one edge.** Loading the real registry and inspecting dependencies: of the five stages
   that actually crossed `execute()`, exactly one had a stage-to-stage dependency
   (`ffast.atom_labels` needs `ffast.atom_positions`). Every other stage read only external
   `frame.*` / `view.*` inputs. The topological sort, the cycle detector and the address-string
   parsing existed to decide that one line runs before another line.

2. **Most stages did not cross it.** 14 stages were registered; 5 went through `execute()`. Six were
   reachable from nothing in production at all (`bond_indices`, `bond_positions`, `selection_mask`,
   `value_colors`, `force_arrows`, `frame`). Three more were live but called by direct import
   (`atom_filter`, `kabsch_alignment`, `displacement_stats`), and `atom_align` was written in stage
   shape but never registered. The scene was built *around* the pipeline, not *through* it.

3. **The bypass was structural, not neglect.** Bonds must decide mid-computation between an explicit
   Fixed bond set and `ds.getBondIndices(idx)`; force arrows must resolve a per-stage
   `prediction_ref`, then the view's global ref, then ground truth, and then fetch. That is
   conditional I/O in the middle of the graph, and the executor's context model is pre-resolved pure
   values in, pure values out. Routing them through it would have required either lazy/callable
   inputs or eager pre-fetching — and eager pre-fetching would fetch predictions every frame with
   force arrows switched off, which on a remote session is a server round-trip. Measured executor
   overhead is 8-71 µs per frame; that regression would have been three orders of magnitude larger.
   Deepening was therefore not the cheap direction it appeared to be.

Removing it costs nothing measurable and returns a little: at 20 000 atoms the framework accounted
for 0.071 ms of a frame in which `positions.tolist()` alone costs 1.45 ms and msgpack-packing the
scene arrays costs 2.96 ms. Performance was never an argument in either direction — it is recorded
here only so the question is not re-asked.

## Why the catalog stayed

An earlier draft of this decision deleted the catalog too, on the grounds that it existed to serve
the executor. That was wrong, and worth recording as the error it was: the catalog has three
consumers that have nothing to do with execution.

- `ffast-cli stages list`
- `ffast-cli stages inspect <id>` — dumps the declared schema as JSON
- `ffast-cli stages test [<id>]` — runs the `tests=[...]` cases declared inline in the stage files

All three are documented in `README.md` and covered by `tests/ffast/test_cli.py`. Deleting the
catalog would have silently removed three documented commands and the declarative test cases. The
lesson generalises: measuring one half of a mechanism and attributing the result to the whole is how
a deletion becomes a regression.

The catalog also keeps a job the executor used to do. `StageRegistry.resolve_parameters(id,
overrides)` returns declared defaults overlaid with the view's stored values, ignoring keys the stage
does not declare — what the executor's `_resolve_params` did, minus the execution. Callers invoke
stage functions directly but resolve parameters through the catalog, so a default lives in exactly
one place. This is not incidental tidiness: `ffast.force_arrows` declared `length_factor=1.0,
normalised=False` while `scene_builder` shipped `10, True`, because that stage's maths was
reimplemented at the call site instead of resolved from its declaration. Nothing caught it, since
that stage never reached the executor — and "fixing" `scene_builder` to call the registered stage
would have made every force arrow ten times shorter. The duplicate declaration is deleted; the
renderer is now the single home for that calculation.

## What changed

- Deleted `ffast/visualization/pipeline.py` (`execute`, `StageExecutionError`) and
  `StageRegistry.resolve_order`, and `StageSchema.dependencies` which only `resolve_order` read.
- `scene_builder.build_scene` calls `atom_positions`, `atom_sizes`, `atom_colors`, `atom_labels` and
  `unit_cell_edges` directly. The labels-needs-positions dependency is line order. The
  `_ATOM_POSITIONS`/`_ATOM_SIZES`/`_ATOM_COLORS` address constants and `_label_outputs_present` are
  gone — direct calls return values, so no address strings remain to drift (the concern ADR 0038
  raised, resolved by removal rather than by imported constants).
- Deleted the six production-dead stages and the tests covering them: 25 pytest cases plus 6 inline
  declarative cases. `_arrow_mesh` is kept — `ffast/renderers/vispy/adapter.py` imports it, and that
  cross-seam private import is candidate #07's business, not this one. (Closed by ADR 0052: it moved
  to `ffast/renderers/vispy/arrow_mesh.py` and `force_stages.py` — which registered nothing after
  this ADR — is deleted.)
- Added `StageRegistry.resolve_parameters`, with tests.
- `test_builtin_stages_registered` now asserts the registered set *exactly*. It previously used
  `issubset`, which is why six dead stages sat in the catalog unnoticed; equality makes a
  re-introduced dead stage fail.
- ADR 0038 → **Rejected**: its decision was validation sited at the executor.

Registered stages: 14 → 8, all with a live caller.

## Consequences

The catalog no longer describes an execution mechanism, so CONTEXT.md's **Stage Catalog** entry ("the
server-side registry of available Pipeline Stages and their dependencies … the server resolves a
valid execution order") is now wrong in its second half and is amended: it is a declarative registry
of stages and their parameters, and execution order is the caller's.

What this forecloses: a plugin cannot contribute a scene-building step. It could not before either —
`scene_builder` named its five targets in a hard-coded list and nothing let a plugin add one — so
this removes an appearance of extensibility rather than extensibility itself. If plugin-authored
scene stages become a real requirement, an executor comes back, and it should be designed around
conditional I/O from the start, since that is what excluded bonds and forces from the one that just
went.
