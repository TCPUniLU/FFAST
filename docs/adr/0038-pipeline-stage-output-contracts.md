Status: Rejected (superseded by ADR 0049)

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

## Rejection (2026-08-06, ADR 0049)

Rejected because its subject no longer exists. This ADR's decision was *"the pipeline executor
validates declared-vs-produced after each stage"* — validation sited at the executor. ADR 0049
demoted that executor: `ffast/visualization/pipeline.py` is deleted, `scene_builder` calls stage
functions directly, and there is no longer a single point through which stage execution passes.

The measurement that decided ADR 0049 also undercuts this ADR's premise independently. This ADR
assumed stages generally flow through the executor, so executor-side validation would cover them.
They did not: of 14 registered stages only 5 crossed `execute()`, and the ones most likely to
misbehave were precisely the ones that bypassed it — `bonds` and `forces` cannot cross the executor
at all, because they need conditional I/O mid-pipeline that the pure-values context model cannot
express. Validation at the executor would have enforced the output contract on the five simplest
stages and none of the risky ones.

The concrete problem this ADR cited is real and was fixed differently. The hard-coded output-key
strings (`"stage.ffast.atom_positions.positions"`) are gone entirely rather than replaced by imported
constants — direct calls return values, so there are no address strings left to drift. The
misdeclaration failure mode it worried about also turned out to have a live instance, which no amount
of executor validation would have caught: `ffast.force_arrows` declared `length_factor=1.0,
normalised=False` while the renderer shipped `10, True`, undetected precisely because that stage never
reached the executor. ADR 0049 removed the duplicate declaration.

What survives as a real concern: stage *parameters* still need one home, and they have one —
`StageRegistry.resolve_parameters` (ADR 0049). Typed output validation is not reopened here. Should
stage authorship pick up again, the case would need remaking against whatever execution shape exists
then, not this one.
