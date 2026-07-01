# Dataset Fields: key-in-the-ref, reference lazy / prediction eager

To let user metrics consume arbitrary numeric keys carried by loaded files
(`atoms.info` per-frame scalars, `atoms.arrays` per-atom scalars — **Dataset
Fields**), a Metric references a key directly in its input ref:
`reference.info.<key>`, `reference.atoms.<key>`, `prediction.info.<key>`,
`prediction.atoms.<key>`. Ref validation, previously membership in the closed
`ALL_VALID_REFS` frozenset, becomes "member of the frozenset **or** matches the
field-ref pattern" (single site, `ffast/metrics/graph.py`). An absent or
malformed key resolves to `None` (the existing graceful path), never throws.

## Considered Options

- **Key-as-parameter** (rejected): one ref `reference.atom_field` plus a
  `field_key` Compute Parameter, so one metric serves any key with a UI
  dropdown. Rejected because the authoring model is user-written metrics that
  already know their key name, and it would require threading parameters through
  `InputResolver.resolve`, which today receives only `(ref, model, dataset)`.
- **Key-in-the-ref** (chosen): the key name lives in the ref string. No resolver
  signature change; cache identity already keys on the ref string, so different
  keys are different metrics automatically. Cost: a metric is bound to one key;
  switching keys is a new metric, not a dropdown.

## Consequences

- **Storage is asymmetric by side.** Reference fields read **lazily** from the
  dataset loader's retained `atomsList` (`getAtomField`/`getFrameField`).
  Prediction fields must be **eagerly extracted at load** because the prediction
  load path discards its source ASE objects after pulling energy/forces; only
  the keys declared by registered field metrics (known once config is frozen)
  are extracted, to bound memory. Same `{side}.{info,atoms}.<key>` surface, two
  mechanics underneath.
- Fields resolve server-side (metrics and scene-building are server-owned), so
  the Prediction-Only Array Channel (ADR 0004) is **not** extended.
- A Dataset Field can be exposed as a plottable/colorable Metric purely
  declaratively via a TOML `[[metrics.fields]]` passthrough (per ADR 0021 / 0007);
  derived math still requires a Python `@metric`.
