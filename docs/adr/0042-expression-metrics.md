# Expression Metrics: element-wise algebra over refs, no Python (Tier 2.5)

FFAST has a three-rung ladder for exposing custom metrics, but the middle rung
is missing. A **Dataset Field** (ADR 0023) surfaces a file key as a passthrough
metric with no Python; a **Transform** (ADR 0021) reduces one metric via a named
catalog entry with no Python; anything else — including trivial arithmetic like
`predicted − reference` normalised per atom — drops straight to a hand-written
Python `@metric`, with its `Ref[]` markers, jaxtyping return shape, picklability
constraint, and `modules/`-discovery placement. ADR 0023 already flags this:
"derived math still requires a Python `@metric`." That is the wrong altitude for
the non-developer user the v2.0 release check targets, whose "custom metric" is
almost always one line of algebra over existing quantities.

This ADR adds an **Expression Metric**: a declarative `[[metrics.expr]]` entry
that binds named **Expression Variables** to **Metric Inputs** and evaluates an
**element-wise** arithmetic `expr` string over them, registering the result as an
ordinary Metric. No Python,
no plot/UI changes — a `compile_expr_metric` compiler emits a metric the server
computes and the client draws, exactly like the field and transform compilers,
running in the same pre-freeze compile pass on server, client, and headless
thread (idempotent).

```toml
[[metrics.expr]]
id    = "mylab.energy_per_atom"
label = "Energy error per atom"
unit  = "energy"
expr  = "abs(pred - ref) / n_atoms"

  [metrics.expr.vars]
  pred = "prediction.info.pred_E"      # raw ref, Dataset Field, OR a metric id
  ref  = "reference.info.REF_energy"
```

## Prior art

The closest neighbour is **PLUMED**, an MD-analysis package whose `CUSTOM` /
`MATHEVAL` action computes a function of existing collective variables:
arguments are bound via `ARG`, renamed via `VAR`, and the formula is a `FUNC`
string evaluated by the *Lepton* algebra library. The named-var binding here is
the same shape (`vars` table ↔ `ARG`/`VAR`, `expr` ↔ `FUNC`). The wider pattern
is convergent: Prometheus/Grafana pairs a PromQL expression editor with a visual
builder that emits the same query (motivating a later wizard on top of this, not
beside it), while two-tier tools that skipped the expression rung — MLflow's
built-ins vs `make_metric` Python — force Python for one-liners, the exact pain
this closes. Unlike Lepton, FFAST metrics are read-only (plot / atom colour), so
evaluation needs **no automatic differentiation** — pure numeric eval, no
derivative machinery.

## Decisions

1. **Element-wise only.** The whitelist is shape-preserving; no reducers. All
   shape-changing (KDE, smoothing, per-structure reduction) stays in the
   Transform layer (ADR 0021). One owner per job: expr does algebra, transforms
   do reductions, Python `@metric` does the complex tail. This is the load-
   bearing boundary — see *Considered Options*.
2. **Same-shape only.** Every non-scalar Expression Variable in one expr must
   resolve to the same **Metric Shape**; mixing different array shapes is rejected
   at config-load with an error naming the offending variables and their shapes.
   The `per-structure`, `per-atom`, and `vector-per-structure-per-atom` shapes each
   work internally. The output takes that shape.
3. **Scalars broadcast.** An Expression Variable whose shape is `dims.scalar`
   (e.g. a metric like `ffast.energy_shift`) broadcasts against any shape, exactly
   like a numeric literal. The same-shape rule constrains only non-scalar array
   variables, so the offset-correction pattern (`energy_difference − energy_shift`)
   is expressible.
4. **`n_atoms` reserved variable.** A `per-structure` atom-count array is
   auto-provided (`np.diff(offsets)` for variable datasets, count-per-structure for
   uniform) as the reserved **Expression Variable** `n_atoms`, so per-atom
   normalisation — the most common normaliser in this domain — works with no
   Python. A user `vars` entry named `n_atoms` is a **Configuration Failure**.
5. **Variables may bind a metric id.** An Expression Variable binds a raw ref, a
   **Dataset Field** (ADR 0023), **or** a registered **Metric ID** — the three
   forms of a **Metric Input**. Metric-id inputs are resolved by the Metric
   Execution Context DAG (ADR 0035) — the same machinery builtins use via
   `Ref["ffast.energy_difference"]` — so composition is near-free and cycles are
   caught by the existing **Metric Graph** validation. A metric-id variable
   contributes its declared Metric Shape to the same-shape check (or broadcasts if
   scalar).
6. **Moderate whitelist.** Functions: `abs sqrt exp log log10 clip where minimum
   maximum sign` (`minimum`/`maximum`/`clip`/`where` are element-wise, not
   reducers). Operators: `+ - * / **` and unary `-`. Constant: `pi`. Explicit
   set, trivially extended later; every entry verified shape-preserving.
7. **Non-finite output raises a Metric Failure.** After eval,
   `if not np.all(np.isfinite(out))` the metric raises a **Metric Failure** — no
   `errstate` masking. This is uniform with hand-authored metrics and honours the
   Key Constraint (CONTEXT.md) that a Metric never returns silent `inf`/`nan`. The
   consequence is deliberate and accepted: a `/0` or `log(-x)` on *any* structure
   makes the whole Expression Metric — and its dependents — unavailable until the
   data or expr is fixed, rather than masking bad structures as gaps. Consistency
   with the existing non-finite constraint was chosen over per-value masking (this
   reverses an earlier draft's "let nan flow"; see *Domain reconciliation*).
8. **Cache identity via `implementation_source`.** The metric compute cache is
   in-process only (`MetricCache._store`) and already keys on
   `implementation_hash` plus fingerprints of the resolved input arrays, so a
   `vars` ref swap busts the cache automatically. An `expr` edit under the same
   `id` busts it because `_ExprFn.implementation_source()` returns
   `expr + sorted(var→ref) + whitelist-version`. The `id` is **not** hash-suffixed
   (unlike transforms), so it stays referenceable verbatim from Panel configs.
9. **Config surface.** `ExprMetricConfig` (`id`, `label`, `unit`, `expr`, `vars`)
   joins `FieldMetricConfig` in `ffast/config/models.py`; `MetricsConfig` gains
   `expr: list[ExprMetricConfig]`. `id` must be namespaced (contain a dot). A
   `compile_expr_metric(s)` sits beside `compile_field_metric(s)`.

## Considered Options

- **Reduction scope: element-wise only (chosen)** vs whole-array reduction vs
  full axis reductions (rejected). Allowing reductions inside expr would create
  two parallel reduction engines — expr and the Transform layer — with
  overlapping capability, divergent cache-identity schemes, and duplicated
  ragged/`offsets` handling for variable-size systems. Element-wise-only keeps a
  single owner per job.
- **Named-var binding + expr string (chosen)** vs **dotted refs inline in the
  expr** (rejected). A ref path (`prediction.info.pred_E`) is not a Python
  identifier, so inlining it forces the parser to scrape refs from the string,
  colliding with function-call syntax and complicating validation. The `vars`
  indirection keeps the expr a clean identifier expression (and mirrors PLUMED).
- **Shape mixing: same-shape only (chosen)** vs numpy broadcasting (rejected).
  Genuine mixed-shape math (mass-weighting a force error) needs explicit axis
  alignment the user cannot express in a flat expr string, and on variable-size
  datasets the flat `(N_tot, 3)` force layout has no frame axis to broadcast
  against without `offsets` — the ragged handling deliberately out of v1 scope.
- **Restricted-AST eval (chosen)** vs `eval()` with stripped `__builtins__` vs an
  external expression lib (all rejected). Parse with `ast.parse(mode="eval")`,
  walk, and permit only `BinOp`, `UnaryOp`, numeric `Constant`, `Name` (bound
  vars + `n_atoms`), and `Call` to the whitelist; reject attribute access,
  subscripts, and comprehensions at compile with a precise message. Pure Python +
  numpy, zero new dependencies. `eval()` sandboxing is a standing dunder/escape
  footgun; numexpr/asteval/sympy/Lepton add a dependency for a one-expression
  need (and Lepton is C++, PLUMED-internal).

## Consequences

- **Picklable by the same discipline as siblings.** The expr string and the
  var→ref map ride the metric schema (data, not a closure) via `parameters=`; a
  module-level `_ExprFn` (precedent: `_TransformFn`, [transforms.py:187]) carries
  them, compiles the AST once, caches it by string, and evaluates over the
  resolved arrays. The registry stays picklable for the worker pool.
- **Errors surface at config-load, not at plot time.** Unknown identifier,
  disallowed call, bad ref, shape mismatch, or `n_atoms` name collision fail while
  the config is frozen — matching the ADR-0023 §2 goal that users can "safely make
  mistakes and understand what went wrong." `ffast-cli metrics validate` extends
  to expr metrics.
- **Non-finite output is the one compute-time error.** Everything else is caught
  at config-load; a `/0` or `log(-x)` can only be known against real data, so it
  surfaces later as a **Metric Failure** that names the metric and disables its
  dependents (per Decision 7). Fixing it means fixing the data or the expr.
- **Units are passthrough.** `unit` is a user-declared free string (as in
  `fields.py`); no dimensional analysis. Arithmetic that changes units is the
  user's responsibility.
- **3D atom colouring for free.** A per-atom expr resolves to `(N_atoms,)` and is
  colourable through the existing configured-metric colour path — plan goal #4,
  no extra work.
- **The Python `@metric` (Tier 3) stays.** Genuinely complex metrics — the
  per-element KDE reductions, anything with control flow or external libraries —
  remain Python. Expression Metrics close the arithmetic majority, not the tail.
- A later metric **wizard** (Grafana-style visual builder) can emit
  `[[metrics.expr]]` config rather than introduce a parallel mechanism.

## Domain reconciliation

Checked against the project glossary (CONTEXT.md) during a domain-modeling pass:

- **Non-finite policy (Decision 7).** An earlier draft let `nan`/`inf` flow. That
  contradicted the standing Key Constraint that "a Metric implementation guards
  non-finite results explicitly … by raising rather than returning silent
  `inf`/`nan`." Reconciled *toward the constraint*: Expression Metrics raise a
  **Metric Failure**, uniformly with hand-authored metrics. The constraint text is
  left unchanged. The exploratory-UX cost (one bad structure blanks the whole
  metric) was weighed and accepted.
- **New glossary terms.** `Expression Metric` (distinct sibling of `Transform
  Metric` — element-wise vs reducing) and `Expression Variable` (the local name an
  expr binds to a **Metric Input**; `n_atoms` is the reserved one) were added to
  CONTEXT.md.
- **Shape vocabulary.** This ADR uses the canonical **Metric Shape** names
  (`per-structure`, `per-atom`, `vector-per-structure-per-atom`), not the ad-hoc
  "per-frame"/"force family" of the first draft. The pre-existing *frame ≈
  structure* synonymy (Frame Field vs per-structure) is recorded under CONTEXT.md
  *Flagged Ambiguities*.

[transforms.py:187]: ../../ffast/metrics/transforms.py
