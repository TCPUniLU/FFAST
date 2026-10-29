# Errors are a base metric plus keep boxes, not a list of fixed variants

Status: Accepted 2026-10-24, built 2026-10-24 (steps 0-8; see "As built").
Amends ADR 0064 (adds `by=atom`, a signed mean
statistic in the boxes, and per-atom values combining with per-atom vectors).
Supersedes the `norm` parameter of `ffast.force_mae` / `ffast.accel_mae` and the
fixed force, acceleration and energy error metrics listed in section 7.

## The problem

Force errors come as about 25 fixed metrics: per atom, per frame, per element,
over the dataset; mean or RMS; vector length or per component; force or
acceleration. Each one is a separate entry in the Metrics pane with a long
name ("Force Error Magnitude, RMS (per frame)"), and the one per-atom metric has
a `norm` dropdown with the words `l1` / `l2`, which a user has to look up.

Since ADR 0064 a formula can say the same thing with one keyword:
`mean(mean_parts(abs(pred_F - ref_F)), by=structure)`. 38 of the built-ins
already have such a formula (`ffast/metrics/builtin/equivalents.py`), tested
against the Python version. So the fixed list is a set of hand-picked points in
a small space of choices: which axes to keep, how to combine x/y/z, which
statistic. A user who wants a point not on the list (force Max per frame,
per-axis error per element) has to write a formula.

## The decision

### 1. Keep boxes

A metric's grouping is chosen with four checkboxes. A ticked box means **keep
separate**, the same meaning as `by=`:

```
Keep:      [✓] atom  [✓] structure  [ ] element  [ ] x/y/z
Combine:   (•) Per component  ( ) Length          (only when x/y/z is unticked)
Statistic: [MAE ▾]   MAE | RMSE | Max | Mean (signed)
```

| atom | structure | element | result |
|---|---|---|---|
| ✓ | ✓ | (greyed) | each atom in each frame |
| ✓ | ✗ | (greyed) | each atom position averaged over frames; uniform datasets only (ADR 0058), greyed otherwise with "structures have different atoms" |
| ✗ | ✓ | ✗ | one value per frame |
| ✗ | ✓ | ✓ | frame × element table (`by=(structure, element)`) |
| ✗ | ✗ | ✓ | one value per element |
| ✗ | ✗ | ✗ | one value for the dataset |

**x/y/z** ticked keeps the three axes apart (e.g. the MAE of x, of y and of z).
Unticked, **Combine** says how they become one number: **Per component** treats
them as three separate numbers (the MACE/NequIP convention FFAST uses since
78459eb) and is the default; **Length** is √(x²+y²+z²). These replace `l1` /
`l2`. Length / 3 is not the per-component value: for an error (3, 0, 4) the
length is 5 and the component mean 2.33. Only for RMS is there a fixed factor
(length RMS = √3 × component RMSE).

**Statistic** defaults to MAE. It is greyed when nothing is averaged (each
output value comes from one number). **Mean (signed)** keeps the sign, so the
energy shift and a force bias along one axis can be read.

There is no element filter among the boxes; `where=element(...)` stays a
formula edit ("Show as formula").

### 2. The boxes write a formula

Every box setting is a formula over a base metric `D`:

| setting | formula |
|---|---|
| structure ✓, Per component, MAE | `mean(mean_parts(abs(D)), by=structure)` |
| structure ✓, Length, RMSE | `rms(norm(D), by=structure)` |
| element ✓, Per component, RMSE | `sqrt(mean(mean_parts(D ** 2), by=element))` |
| atom ✓ structure ✗, Length, MAE | `mean(norm(D), by=atom)` |
| x/y/z ✓, nothing else, Mean (signed) | `mean(D, by=all)` |

The box setting is stored next to the formula, so the metric reopens with its
boxes as made. There is one engine: the server runs the ADR 0064 reductions.

### 3. Reading an existing metric's boxes

Opening any metric shows its boxes already ticked:

1. A formula is classified by walking its parsed tree, the same walk
   `infer_shape` (`ffast/metrics/expr.py`) does for shapes, extended to note
   the outer reduction, its `by=`, and `norm` / `mean_parts`.
2. A built-in with a formula twin is classified through the twin.
3. Another Python metric declares its setting in `@metric(recipe=...)`, or its
   boxes come from its output shape alone, with Combine and Statistic shown as
   "unknown".
4. A formula that fits the boxes only partly (e.g. `abs(dE - mean(dE, by=all))`)
   gets its keep boxes from its shape and is marked **custom**. Changing them
   wraps it (`max(that, by=structure)`) rather than rewriting it.

Boxes for detail a metric no longer has are greyed, with a hint such as "this
metric is already averaged per frame": from a per-frame metric one can only go
coarser.

### 4. Metrics pane: approve to create

The boxes sit in the Metrics pane under the selected metric. Changing them
updates **Try** live (result, shape, unit) and the "Show as formula" text.
Nothing is registered until **Create metric**, which makes a new user metric
(ADR 0057) with an editable auto name, e.g. "Force Difference · MAE · per
frame · per component". The metric the boxes started from is never changed.

### 5. 3D Color by: apply at once

The 3D panel's Color by has the same boxes, but a change recolours at once with
no approval, and the setting is saved with the 3D view, not as a metric. A
**Save as metric** link turns it into one. Only settings that give one value
per atom are allowed: atom ✓ (with or without structure), or element ✓ with
atom ✗ (each atom painted with its element's value). Settings that give one
value per frame or one number are greyed. The colour range uses the whole
dataset (ADR 0064 amendment of 2026-10-24).

### 6. Five base metrics

| base | shape | replaces |
|---|---|---|
| Force Difference `ffast.force_difference` | per atom × xyz | every force MAE / RMSE / magnitude metric |
| Acceleration Difference `ffast.accel_difference` | per atom × xyz | every acceleration metric |
| Energy Difference `ffast.energy_difference` | per frame | energy MAE, RMSE, shift |
| Energy Difference (shifted) `ffast.energy_difference_shifted` (new) | per frame | energy MAE / RMSE shifted |
| Net Force Residual `ffast.force_net_residual` | per frame × xyz | net force MAE / RMSE |

Each base has a formula twin, so it classifies like any formula. The
pass-through metrics (reference/predicted forces and energies) stay; the
boxes apply to them too.

### 7. The fixed variants are deleted

Deleted ids: `force_mae`, `force_rmse`, `force_mae_per_structure`,
`force_mae_global`, `force_rmse_global`, `force_component_mae`,
`force_component_rmse`, `force_component_mae_per_structure`,
`force_component_rmse_per_structure`, `force_mae_per_element`,
`force_rmse_per_element`, `force_net_mae_per_structure`, `force_net_mae`,
`force_net_rmse`, `accel_mae`, `accel_rmse`, `accel_mae_global`,
`accel_rmse_global`, `accel_component_mae`, `accel_component_rmse`,
`accel_mae_per_atom`, `accel_mae_per_element`, `energy_shift`, `energy_mae`,
`energy_rmse`, `energy_mae_shifted`, `energy_rmse_shifted` (all `ffast.`).

A session, formula or tab that names one is **refused** with a message naming
the replacement, e.g. "`ffast.force_rmse` no longer exists; use Force
Difference with structure ✓, Length, RMSE". There is no translation table:
FFAST has no users with saved sessions yet. The built-in tab files are
rewritten in the same change.

### 8. Formula language additions

- `by=atom`: groups the same atom position across frames. Refused (checked, no
  data needed) on a dataset whose structures differ in atoms.
- A per-atom value combines with a per-atom × xyz value: each atom's value
  applies to all its parts. Needed for `(pred_F - ref_F) / m`. Today
  `_combine` (`expr.py`) requires equal shapes.

`by=(structure, element)` comes from branch `pairs` (ADR 0064 amendment of
2026-10-24), which must be merged first.

### 9. Web only

The desktop (Qt) app has no metric editor and gets no boxes. It gets the fixes
needed to keep its Basic Errors plots, Atomic Errors plots and Coloring list
working on the new metrics, and shows any metric made in the browser (its
Coloring list already refreshes on new user metrics, `UI/loupe/window.py`).

## Considered options

- **Boxes mean "keep" (chosen)** vs "average over". The same calculation either
  way; "keep" reads like `by=`, so one word means one thing in the boxes and in
  formulas.
- **Atom and structure independent (chosen)** vs atom greying structure vs a
  separate "average over frames" box. Independent boxes give per-atom-position
  averages without a new box and keep "ticked = kept".
- **Read the setting from the formula tree (chosen)** vs from the text vs from
  the output shape only. The tree walk exists already and guesses nothing; text
  matching breaks on style; the shape alone loses the statistic.
- **Delete the fixed ids (chosen)** vs keep them folded under their base vs
  leave the list. Deleting gives the short list; it is the one non-reversible
  choice here, accepted because nobody depends on the ids yet.
- **Refuse old ids (chosen)** vs translate them on load vs a migrate command.
  Same reason.
- **Boxes write a formula (chosen)** vs boxes as parameters of each base metric.
  Parameters would only work on metrics that declare them, so custom formulas
  could not have boxes.
- **Apply at once in Color by (chosen)** vs approve there too. Colouring is for
  looking; approval would fill the list with throwaway metrics.
- **Mean (signed) (chosen)** vs keeping Energy Shift as the one fixed metric left.

## Not now

- An element filter among the boxes (chips that add `where=`).
- Boxes on the desktop.
- Statistics beyond the four (std, min, sum): a formula edit away.

## Consequences

- The Metrics pane lists base metrics, pass-throughs, the non-error metrics
  (geometry, densities, smoothing) and user metrics, instead of ~25 force and
  energy variants.
- Built-ins run as formulas rather than numpy functions. Speed is measured
  before and after on a large dataset (step 1) rather than assumed.
- The smoothing and density transforms (`force_rmse_smoothed`,
  `force_mae_per_structure_density`, ...) take a deleted id as their input today;
  they move to the box formulas.
- Old sessions and formulas naming a deleted id stop loading, with a message.

## Decided points

Both settled 2026-10-24 with the recommended option.

- **Ids of box metrics** are derived from base + setting:
  `ffast.force_difference[structure;component;rmse]`, so the same setting is
  always the same metric and tab files can name a setting directly. Any such id
  works where a metric id does, without being created first. Create metric on a
  setting that already exists opens it instead of making a copy.
- **`energy_mae_p`, `energy_rmse_p`, `energy_prediction_shifted`** are deleted;
  the Basic Errors tables and energy scatter switch between Energy Difference
  and Energy Difference (shifted) with the tab's energy-shift toggle.

## Steps

Each step is one commit and leaves the suite green.

0. Merge branch `pairs`; resolve `color_values.py`.
1. **Measure**: time the 25 Python metrics and their formula twins on the
   largest example dataset; record the numbers here.
2. **Formula language**: `by=atom`, per-atom × per-atom-vector combination
   (section 8). Tests: uniform and non-uniform datasets, the refusal message.
3. **Box model** (`ffast/metrics/boxes.py`): setting ↔ formula, and the tree
   classifier (section 3). Tests: every row of the table in section 2 both ways;
   each built-in twin classifies to its setting; a custom formula is marked
   custom.
4. **Bases and deletion**: add `energy_difference_shifted`; delete the ids in
   section 7 and their twins; refuse old ids with the replacement message;
   rewrite `ffast/config/builtin_tabs/` and the transforms that read deleted
   ids. Tests: the refusal message names a valid setting for each old id.
5. **Metrics pane** boxes, live Try, Show as formula, Create metric. Browser
   test: Force Difference → structure ✓, RMSE → Try → Create → Plot it.
6. **Color by** boxes, apply at once, saved with the view, Save as metric.
   Browser test: atom ✓ structure ✗ on a uniform dataset keeps colours fixed
   across frames.
7. **Desktop fixes**: Basic Errors, Atomic Errors, Coloring list on the new
   metrics (`modules/loupe/loupeForceError.py`, `UI/clientFeatures.py`).
8. **Docs**: `docs/usage.md`, `CONTEXT.md`, ADR 0064 status line, README row.

### Step 1 measurements (2026-10-24)

`temp/bench_variable.xyz`: 5000 frames, 172,513 atoms, structures of
different sizes; prediction = reference forces + noise. Best of 5 runs through
`InProcessExecutor`, fresh executor each run (no cache), each metric computing
its whole dependency chain from the raw arrays. Times in ms.

| metric | Python | formula |
|---|---|---|
| force MAE / RMSE over the dataset (per component) | 10 | 6 |
| force MAE / RMSE per frame (per component) | 11 | 6 |
| force error length per atom / per frame / global | 12-14 | 6-7 |
| force MAE / RMSE per element | 18 | 22 |
| net force MAE / RMSE | 11 | 7 |
| acceleration (per atom, per frame, global, component) | 17-21 | 7 |
| acceleration per element | 28 | 23 |
| energy MAE / RMSE / shift / shifted | 0.8-1.2 | 0.1-0.2 |

Formulas are as fast or faster everywhere except force per element, 20 %
slower (the grouping sorts the atoms by element). Not worth a special case.

Done when: the Metrics pane shows the five bases, any force/energy error used
today can be made with the boxes and gives the same numbers, and Color by
recolours on a box change.

## As built (2026-10-24)

Where the build differs from or adds to the text above:

- **`max_parts`** joined the formula language (section 8): the Max statistic
  per component is `max(max_parts(abs(D)), by=...)`.
- **Box id form**: `base[keep;combine;statistic]`, the kept boxes joined with
  `+` in the order atom, structure, element, x/y/z, `dataset` when none is
  kept, and Combine or Statistic left out when the boxes grey it:
  `ffast.energy_difference[dataset;mean]`, `ffast.force_difference[atom+structure;length]`.
  Any box id is registered the first time something asks for it
  (`MetricRegistry.ensure`), so tab files, sessions and colourings use it
  without Create. Create metric saves it as a user metric file under that id
  with its name; its formula must stay the setting's.
- **Registered is not listed.** Tabs, colourings and opening a setting register
  box ids, but the metrics list and the formula inputs show a box id only once
  it is saved as a user metric; until then Create offers to save it (not
  **Open it**), even when a built-in tab uses it. Deleting a saved box id drops
  only its file and name: the setting stays registered, so its users keep
  working, and delete is not refused while something uses it.
- **Color by also offers structure ✓ + element ✓** (one value per element in
  each frame): it gives one value per atom, as section 5 requires, so every
  atom of an element shares a colour in a frame and the colour follows the
  frame. Element ✓ alone takes the value over the whole dataset.
- **Every metric can be a base.** A box id over a user formula wraps it
  (`user.odd[dataset;max]`); that is how a custom formula's boxes work.
- **The energy-shift toggle** of Basic Errors kept its `shifted` parameter but
  drives the existing `shift` transform: the tables are Energy Difference with
  transforms `shift` then `mean_abs` / `rmse`, the scatter Predicted Energy with
  `shift`. The three toggle metrics are deleted as decided.
- **Python metrics without a twin** open with their boxes from their shape;
  `@metric(recipe=...)` (section 3, point 3) was not needed: every built-in
  that reduces was deleted.
- **Desktop Coloring list** (no boxes, section 9) offers fixed settings of the
  force and acceleration bases (`modules/loupe/loupeForceError.py`).
