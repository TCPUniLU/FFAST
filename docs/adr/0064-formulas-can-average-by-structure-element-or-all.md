# Formulas can average, total and take extremes by structure, element or all, over chosen atoms

Status: Accepted 2026-10-23, built 2026-10-23 (steps 1-7). Amendment of 2026-10-24
(per structure and per element at once) accepted and built 2026-10-24. Supersedes ADR 0042 Decision 1 ("Element-wise
only") and the "Reduction scope" option it chose; the rest of ADR 0042 stands.

## The problem

A formula metric (ADR 0042) works on one value at a time: for each atom, or
each structure, it takes the inputs at that place and gives one number for that
same place. It cannot combine values. So the commonest numbers in force-field
evaluation cannot be written as formulas:

- force error **per structure** (the mean over each structure's atoms),
- force RMSE **over the whole dataset**,
- force error **per element** (H, C, O, ...),
- the force error of **some elements only** (hydrogen, or "all but H"),
- energy MAE, or the energy shift (the mean energy difference).

Filtering by element exists only at the dataset level today: Extract Subset
makes a new dataset of the chosen atoms. That gives one dataset per filter, and
a filter cannot be part of a formula (e.g. "colour only the H atoms by their
error").

Today these need Python. Of the 45 built-in metrics, only 8 have a formula
that computes the same (`ffast/metrics/builtin/equivalents.py`); the other 37
are copied by Duplicate as a formula that takes the Python metric as its input
(ADR 0057 amendment, 2026-10-23). A user can scale such a copy but cannot
change how it averages, e.g. weight by mass, average only large errors, or use
RMSE instead of MAE.

ADR 0042 kept reductions out on purpose: averaging inside formulas would have
been a second engine for structures of different sizes, next to the Transform
layer. That reason has weakened. Since ADR 0058 every dataset describes its
structures by offsets (where each structure's atoms start), and one helper,
`ffast/metrics/segments.py` (`segmentSums`, `segmentMeans`), does the
per-structure maths for every built-in. A formula reduction calls the same
helper, so there is still one engine.

## Prior art

How other tools let a non-programmer reduce, checked on 2026-10-23:

- **PLUMED** (closest neighbour; ADR 0042 already follows its `CUSTOM` action):
  each reduction is its own action (`MEAN`, `SUM`); always gives one number.
  [MEAN](https://www.plumed.org/doc-master/user-doc/html/MEAN/)
- **OVITO**: per-particle formulas only; a separate "Reduce property" step gives
  one number per frame. [Compute property](https://www.ovito.org/docs/current/reference/pipelines/modifiers/compute_property.html),
  [Reduce property](https://www.ovito.org/docs/current/reference/pipelines/modifiers/reduce_property.html)
- **ParaView Python Calculator**: `mean(a)`, `max(a)` give one number, which
  then spreads to every point. [Docs](https://docs.paraview.org/en/latest/UsersGuide/filteringData.html)
- **xarray**: `x.mean(dim="atom")`, `x.groupby("element").mean()`; the named
  axis disappears from the result. [Computation](https://docs.xarray.dev/en/stable/user-guide/computation.html)
- **Prometheus**: `avg by (element) (x)`; the labels kept are the result's shape.
  [Operators](https://prometheus.io/docs/prometheus/latest/querying/operators/)
- **NequIP** (a force-field trainer): fixed metric names (`forces_mae`,
  `per_type` per element), no formula language.
  [Metrics](https://nequip.readthedocs.io/en/latest/guide/configuration/metrics.html)

The common idea: **say what you reduce over, and the result's shape follows from
that word.** This ADR does that with one keyword, `by=`. Choosing which rows
count is a filter applied before the reduction: OVITO reduces "only selected
elements", SQL has `WHERE`, Excel `AVERAGEIF`. Here it is a second keyword,
`where=`.

## The decision

### 1. Six reductions, each needs `by=`

```
mean(x, by=...)   sum(x, by=...)   min(x, by=...)
max(x, by=...)    rms(x, by=...)   std(x, by=...)
```

`by=` is required and takes one of three words:

| `by=` | combines | per-atom input gives | per-structure input gives |
|---|---|---|---|
| `structure` | the atoms of each structure | per structure | error |
| `element` | the atoms of each element | per element | error |
| `all` | everything | one number | one number |

`rms(x, by=…)` is `sqrt(mean(x**2, by=…))`, given its own name because the
order (square, average, root) is easy to get wrong. `std(x, by=…)` divides by
the number of values N, as NumPy's `np.std` does by default, not by N − 1 (the
"sample" version); a group of one value has `std` 0.

`by=` is required, never assumed: a forgotten `by=` is an error, not a silent
average over everything. `min` and `max` combine many values; `minimum(a, b)` and
`maximum(a, b)` (unchanged) take the smaller or larger of two at each place. The
error for `min(a, b)` says so.

### 2. Reductions keep vector parts; two new functions drop them

A reduction combines atoms or structures and **keeps** the parts of a vector:
`sum(pred_F - ref_F, by=structure)` is the net force error of each structure, a
vector per structure (what `ffast.force_net_residual` computes). Two new
functions work on the parts at each place, like `norm` already does:

- `mean_parts(v)`: the mean of the parts (`(|Fx| + |Fy| + |Fz|) / 3`),
- `sum_parts(v)`: their sum.

This keeps the two usual meanings of "force error" apart and both easy to
write. Many trainers average the x, y and z parts; `ffast.force_mae` averages
vector lengths. They give different numbers:

```
mean(norm(pred_F - ref_F), by=all)              # mean length of the error vector
mean(mean_parts(abs(pred_F - ref_F)), by=all)   # mean of all x, y, z errors
```

### 3. Every atom counts once

`by=all` on a per-atom value weighs every atom equally, and `by=element` every
atom of that element equally. With structures of different sizes this is not
the mean of the per-structure means; to get that, write it:
`mean(mean(err, by=structure), by=all)`. Per element, the result lists the
elements in order of atomic number, as the built-ins do.

### 4. Reductions nest and mix with the rest

A reduction is an ordinary value: it can sit inside arithmetic and inside
another reduction. A one-number result spreads to any shape (ADR 0042 Decision
3), which makes "relative to the dataset" formulas possible:

```
abs(dE - mean(dE, by=all))                    # error after removing the mean shift
norm(dF) / max(norm(dF), by=all)              # per-atom error scaled to 0..1
```

A per-structure value does **not** spread onto atoms (ADR 0042 Decision 2
stands): `err - mean(err, by=structure)` is an error. See "Not now".

### 5. The shape check stays a check, with no data

The server's check (`infer_shape`, ADR 0057 rule 11) gains the table in
section 1. It still touches no data, so the editor shows the result shape as
you type:

```
mean(norm(pred_F - ref_F), by=structure)
  per structure → can be plotted

mean(norm(pred_F - ref_F))
  mean(...) needs by=structure, by=element or by=all

mean(ref_E, by=element)
  by=element needs a per-atom value; ref_E is per structure

mean(norm(pred_F - ref_F), by=all, where=element("Hx"))
  "Hx" is not an element symbol

mean(norm(pred_F - ref_F), by=structure, where=n_atoms > 20)
  where= must have the input's shape: per atom here, n_atoms > 20 is per structure

norm(dF) / max(norm(dF), by=all)          (picked for 3D colouring)
  by=all can't be used for colouring yet: colouring sees one frame
```

A per-atom formula that contains `by=all` or `by=element` anywhere is refused
for 3D colouring (the same message names `by=element`). Colouring runs a metric
on the frame on screen only (`color_values.py`), so inside a colouring these
words would mean "this frame", not "the dataset", and the same formula would
give one number in Try and another on screen. Per-atom filters without a
reduction, such as `where(element("H"), err, 0)`, colour as before.

### 6. Where the grouping comes from

A formula never names the offsets or the elements. When it uses
`by=structure`, the server gives it the dataset's offsets; when it uses
`by=element`, the atomic numbers (`reference.elements`). This is how `n_atoms`
already works (ADR 0042 Decision 4). `reference.elements` stays unusable as a
value (it is a label, not a quantity).

### 7. Filters: `element(...)` and `where=`

`element("H")` is 1 for each atom of that element and 0 for the others: a
per-atom value. It takes several symbols, `element("C", "N", "O")`; "all but
H" is `1 - element("H")`. The symbols are the only text a formula may hold, and
only inside `element(...)`. The atomic numbers come from the dataset, as for
`by=element` (section 6).

Used at each atom, it already filters:

```
where(element("H"), norm(pred_F - ref_F), 0)    # colour only the H atoms by their error
```

Every reduction takes an optional `where=`, a per-atom or per-structure 1/0
value of the same shape as its input; rows where it is 0 are left out:

```
mean(norm(pred_F - ref_F), by=structure, where=element("H"))    # H force error per structure
rms(norm(pred_F - ref_F), by=all, where=element("C", "O"))      # one number for C and O
mean(norm(pred_F - ref_F), by=all, where=norm(ref_F) > 1)       # only atoms with large forces
mean(abs(pred_E - ref_E), by=all, where=n_atoms > 20)           # only large structures
```

`where=` never changes the result's shape: `by=` alone decides it. It shares
its name with the per-atom `where(cond, a, b)` on purpose, as NumPy does
(`np.mean(x, where=mask)` beside `np.where`): both say which rows count.
`by=element` with `where=element("H")` lists every element; only H has a value,
the others have no value (section 8).

### 8. Bad values and "no value"

Two kinds of missing number are kept apart:

- **A bad value** (infinity, or not-a-number from e.g. dividing by zero) still
  fails the metric (ADR 0042 Decision 7). A reduction never skips one; one bad
  atom makes its structure's mean bad.
- **No value**: a group that has nothing to combine. A structure with no atom
  that passes `where=` (`where=element("N")` on a structure without nitrogen),
  an element left out by `where=`, or a structure with no atoms at all. This
  is not an error: that place has no value, and the metric still succeeds.

A later reduction leaves out no-value rows as if `where=` had dropped them:
`mean(mean(err, by=structure, where=element("N")), by=all)` is the mean over
the structures that have nitrogen. When everything is left out, the result
has no value; a one-number metric then shows "—".

Inside the evaluator, "no value" is tracked as its own mask, so a bad value is
never mistaken for one. In a metric's result it is stored as not-a-number
(NaN), since a formula metric's result can hold no other NaN. A NaN that
arrives from an input metric is read by where that input comes from:

- from a **formula** metric it is no value (it can be nothing else);
- from a **Python** metric it is a bad value and fails the metric, as today. A
  Python metric's NaN may be a real bug (e.g. 0/0), which must not pass as a
  gap. The cost: a built-in that gives NaN for a structure with no atoms
  (`segmentMeans`) fails a formula that uses it; such structures do not occur
  in real data.

So "no value" can only come from a formula's own `by=` or `where=`, and the two
kinds never mix. If a Python metric ever needs to give "no value", it can
declare so in `@metric` (see "Not now").

How each place shows it:

| Where | No value shows as |
|---|---|
| Timeline | a gap in the line (Plotly draws no point for NaN) |
| Table | `—` (`formatTableValue` already does this) |
| Scatter, density, KDE | the point is left out (the transforms drop NaN first) |
| 3D colouring | the atom is drawn grey, not mapped through the colour map |
| Try | counted on its own line: "12 structures have no value", next to the bad-value count and its frame links |

### 9. More built-ins get an exact formula

About 21 of the 37 built-ins that now copy as a formula over the metric would
get an exact formula and be listed in `equivalents.py`. The test that runs each
formula against its Python metric decides which ones; for example:

| Built-in | Formula |
|---|---|
| `ffast.force_mae_per_structure` | `mean(norm(pred_F - ref_F), by=structure)` |
| `ffast.force_rmse` (per structure) | `rms(norm(pred_F - ref_F), by=structure)` |
| `ffast.force_mae_global` | `mean(norm(pred_F - ref_F), by=all)` |
| `ffast.force_mae_per_element` | `mean(mean_parts(abs(pred_F - ref_F)), by=element)` |
| `ffast.force_net_residual` | `sum(pred_F - ref_F, by=structure)` |
| `ffast.energy_mae` | `mean(abs(pred_E - ref_E), by=all)` |
| `ffast.energy_shift` | `mean(pred_E - ref_E, by=all)` |

Still Python: densities and KDE curves, smoothed series, the radius of
gyration, geometry from picked atoms (distance, angle, dihedral), and metrics
with options (a formula has none).

## Considered options

- **Keyword `by=` (chosen)** vs **Prometheus style** `mean by structure (x)` vs
  **one function per case** `structure_mean(x)`. The keyword parses as a
  Python call, which the formula parser already reads, and makes good error
  messages easy. Prometheus style reads well but needs its own grammar. One
  function per case is the easiest to autocomplete, but it needs 18 names
  (6 reductions × 3 targets).
- **`by=` required (chosen)** vs defaulting to `all`. A default turns a
  forgotten word into a silently wrong number.
- **Keep parts (chosen)** vs reductions that also average the parts. Averaging
  the parts too would make `sum(dF, by=structure)` a single number and the net
  force vector impossible to write. `mean_parts` is explicit and also useful
  without any reduction.
- **Filter inside formulas with `where=` (chosen)** vs **Extract Subset only**
  vs **multiply by a 1/0 mask**. Extract Subset stays, but needs a dataset per
  filter and cannot feed a formula. A mask alone (`sum(err * element("H"),
  by=structure) / sum(element("H"), by=structure)`) works without `where=` but
  is long, easy to get wrong, and divides by zero on a structure without H.
- **An empty filtered group has no value (chosen)** vs failing the metric vs
  giving 0. Failing makes any element filter unusable on a mixed dataset; 0
  looks like a perfect prediction.
- **Refuse dataset-wide words in colouring (chosen)** vs computing them over
  the frame (the same formula then means two things) vs computing them over the
  whole dataset (needs predictions for every frame; see "Not now").
- **A Python metric's NaN is a bad value (chosen)** vs reading every input NaN
  as no value (hides bugs in Python metrics) vs a new `@metric` flag (exact,
  but new API before anyone needs it).
- **One engine (chosen)**: reductions call `segments.py`, as the built-ins do.
  Transforms (ADR 0021) keep KDE and smoothing, which give curves or shorter
  series, not one value per structure or element.

## Not now

- **Spreading a per-structure value onto its atoms** (`err - mean(err,
  by=structure)`, "atom error relative to its structure"). Useful, but it lets a
  shape mistake pass silently. If wanted, it gets its own explicit function
  (e.g. `per_atom(x)`) in a later ADR.
- **Dataset-wide colouring**: run a colouring formula that contains `by=all`
  or `by=element` over the whole dataset once, cache it, and colour the frame
  from its slice, so the formula means the same on screen as in Try. Deferred
  because it needs predictions for every frame: a prediction file has them,
  but a live model (ADR 0030) predicts only the frame on screen, and running
  it on every frame to colour one could take minutes.
- **"No value" from Python metrics**: a flag in `@metric` saying a metric may
  give NaN as "no value", so a formula reads it as a gap, not a failure.
- **Windows over frames** (`rolling_mean(x, 5)`). The smoothing transform
  already exists; its output is shorter than the series (`np.convolve`
  "valid"), which a formula's same-shape rule cannot express yet. Frame order
  also only means something for a trajectory.
- **Median and percentiles** (`median(x, by=…)`, `percentile(x, 95, by=…)`),
  common in force-field papers (e.g. the 95th-percentile force error). They
  fit the same `by=` and `where=`, but cannot reuse the sums in `segments.py`
  (they sort each group), so they are a separate piece of work.
- **Per structure and per element at once** (taken up by the amendment of
  2026-10-24) (`by=(structure, element)`,
  e.g. the H, C and O error of each structure). Computing it is small: the
  parser takes a pair of words, the shape check gains structures × elements,
  and each (structure, element) pair becomes one group for `segments.py`. The
  missing part is showing it: every panel kind takes one series, or one row of
  finished curves per element, and none takes a structures × elements table.
  It needs a timeline with one line per element, or a transform that runs per
  element row (the one built-in that does this,
  `ffast.force_mae_per_structure_per_element_kde`, builds the table inside and
  returns only the curves). Most cells can be gaps (a structure without H), so
  it builds on this ADR's "no value". One later ADR.
- **Grouping by other labels** (by a selection, by a file key). `by=` can take
  more words later without changing the syntax. (Filtering by them already
  works with `where=` once they are a per-atom value.)
- **Element symbols elsewhere** in a formula, or other text.
- **The Display and Extract filter syntax** (`C, -H`) inside formulas. The
  formula keeps its own `element(...)`, which can be combined with arithmetic.
- **Options in formulas** (`norm="l1"`), so metrics with options could become
  formulas.

## Consequences

- **A formula can no longer take the server down.** Each operation in a
  formula makes a full-size temporary array (10 million atoms × 3 parts × 8
  bytes ≈ 240 MB), so a long formula on a large dataset can need several GB.
  Today Try computes it inside the server process; on a cluster, going over
  the job's memory limit makes the scheduler kill the whole server. Step 1
  moves Try to the worker and caps a formula's length and nesting. A formula
  cannot run code or reach memory directly: it is parsed against a fixed list
  (no `eval`), and NumPy checks every index.
- **Some per-atom formulas cannot colour atoms.** A per-atom formula that
  contains `by=all` or `by=element` (e.g. `norm(dF) / max(norm(dF), by=all)`)
  can be tried, saved and plotted, but colouring refuses it (section 5). The
  editor shows this under the shape hint.
- **Saved formulas need a newer server.** A user metric file using `by=` (in
  `~/.ffast/metrics/`, a project `ffast.toml`, or a saved session) does not
  compile on a server older than this change. This is the one part that is hard
  to undo: removing the feature later breaks those files. It depends on a
  separate, small change: the server reports the fingerprint of its installed
  package when a client connects (the one the cluster setup already compares,
  `cluster/bootstrap.py` `needs_provision`), and the client warns when it
  differs from its own ("The server on the cluster runs older code. Restart
  the job to update it."). Today a new job with automatic setup reinstalls a
  changed package, but reconnecting to a running job, or a profile with its own
  `ffast_server_cmd` (e.g. the container of ADR 0059), runs old code unseen.
- **The cache names the new language.** `_WHITELIST_VERSION` in `expr.py` goes
  up, so no result computed under the old rules is read back.
- **The editor learns the new words.** `metric_logic.js` lists the new
  functions for suggestions; the words after `by=` and the symbols inside
  `element(...)` are not reported as "not an input".
- **"No value" reaches places that never saw it.** The colour map
  (`colormap.js` `mapColorBy`) has no rule for NaN today and must draw grey;
  the KDE and smoothing transforms must drop NaN before they run (today a NaN
  would spoil the whole curve); the scene's automatic colour range already
  uses finite values only. Python metrics can give NaN today too (an empty
  structure in `segmentMeans`), so these fixes help them as well.
- **A metric can now succeed with gaps.** A plot of the H error of a dataset
  where most structures lack H is mostly gaps. Try says how many structures
  have no value, so this is seen before saving.
- **The guide** (`docs/usage.md`, "Formula metrics") replaces "it never totals
  over atoms or frames" with the table in section 1 and the examples above.

## Steps

Each step is one commit and leaves the suite green.

1. **Safety limits** (before any new word, since they protect today's
   formulas too): a formula longer than 2,000 characters or nested deeper than
   50 levels is refused by the check, with a message that says which limit.
   Try computes the formula in the metric worker process, as saved metrics
   already do, not in a thread of the server process: a formula that uses up
   memory or runs too long then kills only the worker (which restarts), never
   the server and the windows connected to it. Tests: both limits and their
   messages; a Try whose worker dies reports a failure and the server still
   answers.
2. **Parser and shape check** (`ffast/metrics/expr.py`): accept the six
   reductions with `by=` and `where=`, `mean_parts`, `sum_parts`,
   `element("H", ...)` (text allowed only there, and only element symbols); the
   shape table; the error messages in section 5. Tests: every row of the table,
   every message.
3. **No value in every display** (before any formula can make one, so no
   commit lets a gap reach code that would draw it as a number; the colour map,
   for one, has no rule for NaN today, and a missing value drawn at one end of
   the scale would look like a perfect or a terrible prediction): grey atoms in
   `colormap.js`, NaN dropped before the KDE, smoothing and summary transforms.
   Tests: one test that feeds a result with gaps through **every** panel kind
   (`PLOT_BUILDERS`, `TABLE_BUILDERS` in `panels.js`), every registered
   transform and the colour map, and checks that each gap is left out, grey or
   `—`, never a number. It loops over those registries, so a panel kind or
   transform added later without a no-value rule fails it.
4. **Evaluation**: reductions call `segments.py`; the server supplies offsets
   and elements when `by=` or `element(...)` needs them; `where=`; the no-value
   mask and the bad-value rule (section 8); Try's "no value" count. Tests:
   equal and unequal structure sizes, per-element order, nesting, a structure
   with no atoms, a filter that leaves a structure empty, a bad value next to a
   no-value row, a NaN from a formula input read as no value, a NaN from a
   Python input failing the metric.
5. **Exact formulas**: add the built-ins that now have one to
   `equivalents.py`; the existing test runs each against its Python metric.
6. **Editor and colouring**: suggestions, `by=` words, `element(...)`
   symbols; colouring refuses a per-atom formula with `by=all` or
   `by=element`, and the editor shows why (section 5). Tests: the refusal
   message, and a filter-only formula still colours. Browser test: write
   `mean(norm(pred_F - ref_F), by=structure, where=element("H"))`, see "per
   structure", Try (with its no-value count), Save, Plot it.
7. **Docs**: `docs/usage.md`; ADR 0042 gets a status line pointing here.

Done when: the formulas in section 9 compute what their built-ins compute (the
test says so), and a user can write, try, save and plot a per-structure, a
per-element and an H-only formula in the browser, the last with gaps where a
structure has no H.

## Amendment 2026-10-23: bars per element, and warnings for ignored bad values

Decided after the build, when the review found two gaps.

**1. A per-element result is drawn as bars.** "Done when" asks for a
per-element formula to be plotted, but no panel kind took one value per
element: the per-element table is fixed to an MAE and an RMSE column, and the
per-element density draws curves. A new panel kind, **Bars per element**
(`bars`), takes one per-element metric (`value`):

- one group of bars per element, in order of atomic number, labelled by symbol,
  with the element picker the other per-element panels have;
- one bar per prediction in each group, in the series colours;
- an element with no value has no bar and a "no value" label, never a bar at 0
  (which would read as a perfect prediction);
- **Plot it** appears for a per-element result and adds a bars panel to the
  "My metrics" tab, as it adds a timeline for a per-structure one;
- the desktop has the same kind, so both clients keep the same panel kinds.

**2. A bad value in a row that `where=` leaves out does not fail the metric,
but is reported.** Section 7 ("rows where it is 0 are left out") and section 8
("a reduction never skips one") disagreed for such rows. They are left out:
`mean(err, by=structure, where=element("H"))` does not fail because a carbon
atom's force is broken, as `where(element("H"), err, 0)` never did. A
broken input must still not pass unseen, so each such value is counted and
shown:

- Try adds a line: "1 bad value in rows left out by where= was ignored
  (frame 41)";
- the saved metric's result carries the warning, and a panel drawing it shows
  ⚠ beside its title, with the sentence on hover; a formula that takes such a
  metric as its input carries the warning on.

Bad values in counted rows still fail the metric, and a bad value inside
`where=` itself makes its row bad. The element-wise `where(cond, a, b)` is
unchanged and gives no warning.

**Consequence.** `METRIC_RESULT` gains an optional `warnings` list. This is a
change to the message between server and client, and the one part that is hard
to undo; a desktop client older than this change rejects a result that
carries warnings (the code fingerprint warning of the connection says so).

With these, "Done when" holds as written: a per-element formula is plotted as
bars.

## Amendment 2026-10-24: per structure and per element at once

Status: Accepted 2026-10-24, built 2026-10-24 (steps 1-6). Takes "Per
structure and per element at once" out of "Not now".

**The problem.** "Per element" means two different things today. The built-in
`ffast.force_mae_per_element` colours atoms by the frame on screen, but its
bars average every frame. The same name gives two numbers, so the 3D view
labels it "(per element, this frame)". A formula with `by=element` cannot
colour at all (section 5). Neither tells you how the error of one element
changes from frame to frame.

**1. `by=` takes the pair `(structure, element)`.**

```
mean(mean_parts(abs(pred_F - ref_F)), by=(structure, element))
```

Each (structure, element) pair is one group: the H atoms of structure 1, the
C atoms of structure 1, the H atoms of structure 2, and so on. All six
reductions and `where=` take it. The input must be per atom; a per-structure
input is an error, as with `by=element`. Only this order is accepted;
`by=(element, structure)` is an error that names the right order, so there is
one way to write it.

**2. The result is a table: one row per structure, one column per element.**
The columns are every element in the dataset, in order of atomic number (as
`by=element` orders them). A structure without that element has no value in
that cell (section 8). In a variable-size dataset many cells can be empty.

The shape check names it "per structure and element". The shape is
`(N_frames, N_elements)`.

**3. What a table can be combined with.** Arithmetic with a single number or
with another table of the same shape works cell by cell. It does not mix with
a per-structure or a per-element value (ADR 0042 Decision 2: nothing spreads
silently). A table cannot be reduced again yet (see "Not now" below).

**4. It colours atoms, and means the same on screen as in Try.** Each row
belongs to one structure, so colouring the frame on screen with its own row is
the same number Try and the plot show. Colouring therefore accepts it: each
atom gets its element's value in that frame. Section 5's refusal stays for
`by=all` and for `by=element` on its own; the word `element` inside the pair
does not trigger it.

**5. A timeline draws it, one line per element.** The Timeline panel kind
accepts a table in its `y` role:

- one line per element, coloured by element, with the element picker the
  per-element panels have;
- with several predictions, each prediction's lines share one line style;
- an empty cell is a gap in its line, as everywhere (section 8);
- **Plot it** adds such a timeline to the "My metrics" tab;
- the desktop timeline gets the same rule, so both clients keep the same
  panel kinds.

**6. Try** counts structures and elements ("100 structures × 3 elements"),
reports empty cells as no value, and computes its minimum, mean, maximum and
histogram over the cells that have a value.

**Not now.**
- **Reducing a table again**, e.g. `mean(table, by=element)` for the mean of
  the per-structure H errors. It needs a rule for which axis each `by=` word
  removes. Wait until someone needs it.
- **Replacing the built-in per-element metrics** with this formula, so the
  "(this frame)" label could go. Wait until the formula has been used.
- **Tables in the other panel kinds** (density, table, bars).

**Consequences.**
- The cache version `_WHITELIST_VERSION` in `expr.py` goes up.
- A saved formula that uses the pair does not compile on an older server. The
  code fingerprint warning of the connection already says when the server is
  older.
- The colouring list and the catalog gain the shape `(N_frames, N_elements)`
  (`COLORABLE_SHAPES` in `start_settings.js`, `_COLORABLE_SHAPES` in
  `loupeAtoms.py`).

**Steps.** One commit each; the suite stays green.

1. **Parser and shape check** (`expr.py`): the pair after `by=`, the error for
   the other order, the table shape, the combination rules in point 3. Tests:
   each rule and each message.
2. **Evaluation**: each (structure, element) pair as one group for
   `segments.py`, columns for every element of the dataset, empty cells as no
   value, `where=`. Tests: equal and unequal structure sizes, an element
   missing from some structures, `where=`, a bad value in a counted cell.
3. **Try**: the counts and summary in point 6. Test: the summary of a table
   with empty cells.
4. **Colouring** (`color_values.py`, both clients' lists): the frame's row
   onto its atoms. Tests: the colour of an atom equals the cell Try gives for
   that structure and element; `by=all` and `by=element` are still refused.
5. **Timeline** (web and desktop): lines per element, the element picker,
   gaps, **Plot it**. Browser test: write the formula above, Try, Save, Plot
   it, see one line per element; colour atoms with it.
6. **Docs**: `docs/usage.md` gains the pair and its example.

Done when: a user can write the formula above, plot one line per element, and
colour atoms with it, and an atom's colour matches the value for its frame and
element on the timeline.
