Status: Proposed

# Pipeline Stage output contracts validated at the executor

The **Stage Catalog** resolves execution order, but nothing checks that a **Pipeline Stage**
produced what it declared: `ffast/visualization/pipeline.py` executes stages and stores raw results
with no presence/shape validation, and the **Render Scene** builder
(`ffast/visualization/scene_builder.py`) trusts hard-coded output-key strings
(`"stage.ffast.atom_positions.positions"` etc.) that duplicate what the stages declare. A
misdeclared or misbehaving stage surfaces as a bare `KeyError` at scene-build/draw time, far from
the offending stage, with nothing naming it.

**Decision (proposed):** each Pipeline Stage declares a typed output schema (key, dtype/shape
expectation), and the pipeline executor validates declared-vs-produced after each stage — a
violation is a **Metric Failure**-style isolated error naming the stage, not a downstream
`KeyError`. Stages publish their output-key constants, and the scene builder imports them instead
of re-stating the strings.

## Why

- The stage contract is already documented as pure and declared-inputs/declared-outputs
  (CONTEXT.md constraints); this makes the outputs half of that contract enforced instead of
  aspirational.
- The value grows with the second renderer: a web **Renderer Client** consuming the same pipeline
  will hit contract violations without a Qt window to debug through.

## Status note

Deliberately marked speculative: the built-in stage set is currently small and stable, and the web
renderer is not yet in development, so the enforcement mostly guards future stage authors. Revisit
when new stages are being written or the web renderer work starts; do not treat this as
blocking the Strong candidates (ADRs 0032–0034).
