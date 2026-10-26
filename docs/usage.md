# Using FFAST

Reference for day-to-day use. If you just want to see it work, the quick start in
the [README](../README.md) is shorter.

- [The interface](#the-interface)
- [Loading data](#loading-data)
- [Example data](#example-data)
- [Analysis tabs](#analysis-tabs)
- [The 3D view](#the-3d-view)
- [Subsets](#subsets)
- [Sessions](#sessions)
- [Running on a cluster](#running-on-a-cluster)
- [The command line](#the-command-line)
- [Custom metrics and tabs](#custom-metrics-and-tabs)
- [Scripting without a UI](#scripting-without-a-ui)
- [Configuration files](#configuration-files)
- [Notes on the two clients](#notes-on-the-two-clients)

## The interface

Two clients, same server, same protocol. The desktop client is the complete one;
use it unless you have a reason not to.

### The desktop client (`ffast-qt`)

```bash
pip install -e ".[gui]"
ffast-qt [--workdir PATH]     # or just `ffast`, which prefers this client
```

It starts a managed local `ffast-server` in the background and connects to it,
so a local session runs the same code path as a remote one. Menu-driven:

| Shortcut | Action |
|----------|--------|
| `Ctrl+d` | Load dataset |
| `Ctrl+m` | Load model |
| `Ctrl+p` | Load prediction |
| `Ctrl+0` | Load zero model |
| `Ctrl+l` | Load session |
| `Ctrl+s` | Save session |
| `Ctrl+n` | New 3D view window |
| `Ctrl+Shift+C` | Connect to cluster |
| `Ctrl+Shift+L` | Connect to a local server |
| `Ctrl+Shift+D` | Load remote dataset |
| `Ctrl+Shift+P` | Load remote prediction |

Loaded datasets and models appear in the left sidebar; analysis tabs run across
the top; the 3D view opens in its own window with the settings panes on its
right. Debug output goes to `debug.log` in the working directory.

### The browser client (`ffast-web`)

```bash
ffast-web                # loopback server plus a browser tab
ffast-web --app          # chromeless window (Chrome, Edge or Chromium)
ffast-web --no-browser   # print the URL and stop
ffast-web --ws-port 8765 --web-port 9000    # pin ports instead of taking free ones
ffast-web --trace        # record a trace, to find the source of a bug
```

With `--trace` the server writes one file per run,
`~/.ffast/logs/trace-<date-time>.log`, and keeps the last 10. It lists, in
order, what you clicked, changed or pressed, each command the page sent, how
long the server took to run it (or the error and traceback when it failed),
each reply, and every error or warning in the page. Reproduce the problem, then
read the file from the end, or send it with the bug report. Large data is
shortened to its size, so the file stays small. `ffast-server --trace DIR`
writes to `DIR` instead.

The page is served on port 8764, so the browser remembers its layout (open
settings section, sidebar width, recent servers) from one launch to the next;
the browser keeps these per address, port included. If 8764 is taken, for
example by a second `ffast-web`, that window gets a free port and starts with
the default layout.

The bare `ffast` command lands here too when PySide6 is not installed, but it
takes no arguments of its own — use `ffast-web` when you need these flags.

Everything binds `127.0.0.1`, so a local session is not reachable from the
network. It needs no native GL or Qt libraries, which is the point of it: it
installs and runs where the desktop client won't.

One page rather than a menu bar:

- **Top bar.** Server address, optional session token, a read-only toggle,
  Connect / Disconnect, and Save / Load Session. Launching with `ffast-web`
  fills the address in and connects for you.
- **Left rail.** Datasets and Predictions, each with a `+` to load one. Datasets
  also get a download button that exports the current selection as extxyz.
- **Own views.** A 3D panel can show its own data next to the main view:
  pick a dataset or subset in the drop-down in its top-left corner, and it
  becomes an **own view** at once; pick "Main view" to link it back. Either
  saves the tab, as Edit mode's ⚙ → "Own view" does. The last panel showing
  the main view stays on it, and its drop-down says so. Click a panel to focus
  it; the top of the settings sidebar then picks an own view's prediction and
  whether its frame and camera follow the main view. Following, one Play or orbit moves both panels; unticked,
  you can compare two frames of one trajectory. When the main view's frame
  has no counterpart in the panel's data, the panel greys out and says so.
  "Use current 3D settings as start" saves the panel's look with the tab.
- **Tabs.** Each tab is a grid of panels: the built-in "3D" tab (one 3D panel
  filling it), the analysis tabs, your project's tabs and your own. A tab may
  put a 3D panel beside plots. The ⋯ at the end of the tab bar exports the tab
  on screen as TOML for a project's `ffast.toml`, resets an edited built-in or
  project tab to the original, hides a tab, and shows hidden ones again.
  Your own tabs are saved on the server's machine, one TOML file per tab in
  `~/.ffast/tabs/` (`ffast-server --tabs-dir` chooses another folder).
  The desktop client shows only built-in and project tabs. In them a 3D
  panel appears as a grey "shown in the browser only" box, and a tab made
  only of 3D panels is left out, since the desktop has its own 3D window.
- **Right sidebar** (in the 3D view). Collapsible panes, grouped: Display, Bonds
  and Unit Cell under Appearance; Colour By, Force Vectors, Extract Subset and
  Alignment under Analysis; Camera and Export under View.
- **Warnings and errors** appear as pop-ups in the bottom-right corner:
  yellow for a warning (the work was done, but something is left out or
  doubtful), red for an error (something you asked for did not happen). A
  pop-up closes by its × or by itself after 10 seconds, and stays while the
  pointer is on it. The same message again adds "×2" to the open pop-up. A
  panel's warning or failure pops up once, not on every redraw. The pop-up
  is extra: the ⚠ beside a panel's title, a failed panel's "Could not compute
  ‹metric›: ‹reason›", a warning under a formula and an error on the status
  line stay where they are. An error that answers a button you pressed in a
  pane (such as "x is not an input") shows only next to that button.

Selecting a dataset is what makes everything else light up; analysis tabs also
need at least one prediction selected.

It is still catching up with the desktop, and is the easiest part of FFAST to
contribute to — see [CONTRIBUTING.md](../CONTRIBUTING.md).

The rest of this document describes behaviour both clients share. Where they
differ, the desktop is described first.

## Loading data

### Datasets

Two families of format:

**sGDML `.npz`**, with keys `R` (positions, shape `(N, n_atoms, 3)`), `E`
(energies, `(N,)`), `F` (forces, `(N, n_atoms, 3)`) and `z` (atomic numbers,
`(n_atoms,)`). Lattice vectors are read if present.

**Anything ASE can read**: `.extxyz`, `.xyz`, `.traj`, `.db` and the rest.
Energies come from `atoms.info['energy']` or `get_potential_energy()`, forces
from `atoms.arrays['forces']` or `get_forces()`. Datasets whose configurations
have different atom counts are detected and handled; you do not have to say so.

After loading, the rail shows the configuration count, atom count (a range for
variable-sized data), chemical formula, and the fingerprint. The fingerprint is
an MD5 of the contents and is what the cache keys on, so the same file loaded
twice costs nothing the second time.

### Predictions

The normal path is a file of pre-computed energies and forces:

- **`.npz`** with `E` shaped `(N,)` and `F` shaped `(N, n_atoms, 3)`.
- **ASE-readable** files carrying energies and forces the same way a dataset
  does.

Load one (File > Load Prediction, or `+` in the browser's Predictions list) and
pick the dataset it belongs to.
FFAST checks the fingerprint, so attaching predictions to the wrong dataset fails
loudly rather than producing quiet nonsense.

There is also a zero model, which predicts zero for everything. It sounds silly
and is genuinely useful: it turns every error plot into a plot of the dataset's
own magnitudes, which is how you spot a corrupted trajectory or a unit mismatch
in about five seconds.

Loading a trained model file and running inference from the UI exists
(`ffast-qt`, with inference server-side per ADR 0030) but is not the path most
people use. Generate predictions where you trained the model and load the file.

## Example data

`examples/data/dataset.xyz` and `prediction.xyz` are what ship today: 100
configurations of mixed organic molecules, 4 to 50 atoms each, so atom count
varies between configurations. Enough to see every tab populated.

A wider corpus is being prepared: fixed- and
variable-sized systems, molecular, periodic and subsystem, so a change can be
checked against the cases that behave differently. It is not published yet —
these are samples of datasets other people published, and their provenance and
licence terms have to be confirmed before this repository can redistribute them.
[examples/data/README.md](../examples/data/README.md) tracks what is still
outstanding.

`examples/MACE/` and `examples/Nequip/` are saved sessions rather than raw data;
open them with Load Session.

## Analysis tabs

Four ship built in. All four are TOML files in `ffast/config/builtin_tabs/`, with
no special status compared to ones you write.

**Basic Errors.** Energy and force MAE/RMSE along the trajectory, KDE
distributions of the errors, true-versus-predicted scatter for both, and summary
tables. The "subtract mean energy offset" toggle removes the constant energy bias
(mean of predicted minus true) everywhere at once; affected plots retitle
themselves "(shifted)" so you cannot forget it is on.

**Atomic Errors.** Force error distributions and tables per element, with an
element picker. This is where "the model is fine except on hydrogens" shows up.

**Subsystem Errors.** Net per-structure force error, distribution plus tables.

**Gyration.** Radius of gyration (weighted by atomic number) over the trajectory
and its distribution, overlaid against energy and force error, with a shared
smoothing control. Useful for asking whether the model gets worse when the
molecule is extended.

Each tab has its own dataset and prediction selector, and you can select several
predictions at once to compare them in one panel.

### Units, and editing labels by hand

FFAST does not invent units. Energy and force units depend on whatever produced
your data, so an axis shows `[energyUnit]` or `[forceUnit]` when nothing has told
it otherwise. That is a prompt, not a rendering fault: it names the setting you
can fill in.

Two ways to fill it in, in the desktop client:

- **Per plot.** Double-click an axis label or a legend entry and type. Right-click
  gives a menu with font size and legend position. Good for one figure.
- **Everywhere at once.** Set `energyUnit` and `forceUnit` in
  `config/default.json` (both ship as `null`). Every panel that declares a unit
  slot picks them up.

Hand edits are cosmetic and client-local. They are stored in
`~/.ffast/display_overrides.json`, keyed by content — the tab name, the panel
kind and the metrics it binds — so they survive a panel being rebuilt or your
TOML being reordered, and they never touch what gets computed or cached
(ADR 0029). Renaming an axis does not rename a metric.

Label editing is a desktop-client feature; the browser client does not have it
yet.

## The 3D view

Left-drag rotates, right-drag pans, scroll zooms. The strip under the viewport
steps through frames, plays them at a set frame rate and optionally skips
frames. The desktop opens each view as its own window; the browser pops one out
into a new tab.

Clicking uses whichever pick tool is armed in the toolbar:

| Tool | What it does |
|------|--------------|
| Info | Click 1 to 4 atoms: position, then distance, then angle, then dihedral |
| Bonds | Pick two atoms to add or remove a bond |
| Align | Pick three atoms to define a reference frame |
| Force | Select atoms (including by rubber-band) to restrict force arrows to |
| Extract | Select atoms to carve out a subset |

The sidebar panes:

- **Display** — atom size, index labels, index and element filters (`0 1 2`, `C`,
  or `-H` to exclude), highlighting, pick radius.
- **Bonds** — show or hide, width, dynamic detection with an adjustable cutoff
  leniency, or fixed bonds.
- **Colour By** — elements, displacement, or any per-atom or per-element metric
  such as force error, with a choice of colormap and a colourbar. The metric is
  computed over the whole dataset, as the plots compute it, so the colourbar's
  range covers every frame and a colour means the same error in each frame.
  Type a minimum or maximum to fix the range yourself. With a prediction that
  covers only the frame on screen, the colouring uses that frame and the
  colourbar says "this frame". Metric parameters appear under the picker when
  the chosen metric has any. **Prediction** says whose numbers: for
  **Forces**, Ground Truth colours by the dataset's own forces and a model by
  its predicted forces, and Combine picks the length ("Forces · Magnitude") or
  per component; an error such as Force Difference needs a model, so it offers
  no Ground Truth; a metric of the dataset alone hides Prediction.
  The colourbar on the 3D view is four pieces: the bar, its name, and the
  highest and lowest value. Drag any of them; they snap into line with each
  other, with a guide. Shift-click to select several; right-click a piece to
  turn it 90°, group the selection (a group moves and turns together), switch
  the whole bar between upright and lying ("Rotate all 90°"), or reset it.
  The mouse wheel over the bar makes it longer or shorter. One arrangement
  serves every colouring, is kept in this browser, and an exported PNG shows
  the pieces where they are on screen.
- **Force Vectors** — arrow length, normalised or true length, restricted to a
  selection or not.
- **Camera** — position and target, field of view, centre-of-mass tracking, saved
  positions, orientation axes.
- **Alignment** — Kabsch alignment of every frame onto frame 0 (optionally heavy
  atoms only), or alignment on a three-atom reference frame.
- **Extract Subset** — build a new dataset from selected atoms.
- **Export** — PNG of the current view, optionally with a transparent
  background.

Unit cells are drawn when the data has lattice vectors.

## Subsets

The workflow FFAST is built around: find something odd in a plot, look at it in
3D.

1. In an error timeline, zoom to the region you care about.
2. Tick **Sub** on the plot.
3. A new dataset named after the plot appears in the rail, containing exactly
   the configurations in view. The tab's other plots and tables redraw with
   just those configurations, and the 3D view shows them, coloured and drawn
   as the full dataset was. All of it follows further zooming; the plot you
   zoom keeps showing the full data. One plot per tab has **Sub** at a time.
4. Untick **Sub** to hide the subset; the plots and the 3D view go back to
   the full dataset, the 3D view on the same structure.

The same works on a scatter plot (zoom onto the outliers) and on an error
distribution (zoom onto a range of errors: the subset is the configurations
whose error falls in it). On a remote session this is also the mechanism that
keeps data on the cluster: only the subset you asked for crosses the network.

The Extract Subset pane does the same thing along the other axis, selecting atoms
rather than configurations, which is how you get "just the active site" out of a
large system.

## Sessions

Save Session writes a directory containing `info.json` (which datasets and models
were loaded) and `cache/*.npz` (everything computed so far). Load Session
restores it.

Two caveats. The original dataset files must still be where they were, because
sessions store paths rather than copies. And if a model file has gone missing,
FFAST reconstructs a ghost model from the cached predictions: it shows up in the
rail with a hash for a name and works for everything except computing something
new.

## Running on a cluster

FFAST can put the whole compute half on a SLURM node while the UI stays on your
machine. Define a profile in `config/clusters.json`:

```json
{
  "profiles": [
    {
      "name": "MyCluster GPU",
      "host": "login.cluster.edu",
      "username": "myuser",
      "identity_file": "~/.ssh/id_ed25519",
      "ffast_server_cmd": "module load Python/3.11 && source ~/env/ffast_env/bin/activate && ffast-server",
      "partition": "gpu",
      "account": "my_account",
      "qos": "normal",
      "job_name": "ffast",
      "cores": 1,
      "cpus_per_task": 16,
      "ntasks_per_node": 1,
      "gpus_per_task": 1,
      "gpu_count": 0,
      "memory_mb": 8192,
      "time_limit": "00:30:00",
      "snapshot_interval_minutes": 5
    }
  ]
}
```

Key authentication only; there is no password prompt. `ffast_server_cmd` must end
by launching `ffast-server` on the node. `snapshot_interval_minutes` controls how
often the server checkpoints its state so a dropped connection is survivable; 0
turns it off.

Connecting submits the job, waits for it to start, opens an SSH tunnel to the
node and connects. If a job for that profile is already running you are offered
it instead of a second one. If FFAST is not installed on the cluster, it installs
itself: it builds a wheel locally, pushes it over SSH, creates a venv and pip
installs, leaving a checksum marker so it only does this when something changed.

Remote data is loaded by path on the cluster filesystem. For ASE files the server
reads the first frame and lets you choose which keys are energy and forces, and
offers a stride so you can subsample a trajectory that is too long to be useful
whole.

To drive a server you started yourself:

```bash
ffast-server --host 0.0.0.0 --port 8765 --web-port 9000
# then open http://<server-host>:9000/?port=8765
```

## The command line

`ffast-cli` does everything the analysis machinery can do without a UI.

```bash
ffast-cli config validate ffast.toml

ffast-cli metrics list                       # registered metric IDs
ffast-cli metrics inspect <metric_id>        # inputs, parameters, shape, unit
ffast-cli metrics test [<metric_id>]         # run the metrics' own test cases
ffast-cli metrics validate                   # freeze the graph; report bad refs, shapes, cycles

ffast-cli metrics run <metric_id> --dataset data.xyz \
    [--prediction pred.xyz] [--pred-energy-key energy] [--pred-force-key forces] \
    [--param KEY=VALUE ...] [--json]

ffast-cli stages list | inspect <id> | test [<id>]     # 3D scene stages
ffast-cli dataset keys data.xyz                        # which keys are usable as fields
```

All of these honour a project config, discovered automatically or passed with
`--config`, so your own metrics are available to them too.

`metrics validate` is the one worth putting in a script. It compiles the whole
metric graph and fails on a bad reference, a shape mismatch or a cycle, before
you find out by staring at an empty plot.

## Custom metrics and tabs

Three levels, and the first two need no Python at all. A project `ffast.toml`
overlays the defaults: an empty file changes nothing, named entries add or
override. It is found by searching upward from your data directory, or passed
explicitly. Unknown keys are an error rather than being ignored, which catches
typos.

### Dataset fields

Any numeric key in an extxyz file becomes a plottable metric:

```toml
[[metrics.fields]]
id    = "demo.total_charge"
ref   = "reference.info.total_charge"   # info.<key> is per-frame
label = "Total charge"
unit  = "dimensionless"
```

`ref` is `{reference,prediction}.{info,atoms}.<key>`. `info` keys give one value
per frame, `atoms` keys give one per atom. `ffast-cli dataset keys <file>` lists
what a given file offers. There is a working `ffast.toml` at the repo root.

### Formula metrics

A formula metric is arithmetic over named inputs, with no Python:

```toml
[[metrics.expr]]
id    = "mylab.force_error"
label = "Force error per atom"
unit  = "force"
expr  = "norm(pred_F - ref_F)"

  [metrics.expr.vars]
  pred_F = "prediction.forces"
  ref_F  = "reference.forces"
```

Each name in `expr` is bound in `vars` to an input: a metric id, a dataset
field, or a raw reference such as `reference.forces`. `n_atoms` is always
available (atoms per structure). Arithmetic works value by value; to combine
values, use a reduction (below).

What a formula may use:

- Arithmetic `+ - * / **`, unary `-`, the constant `pi`.
- Functions `abs sqrt exp log log10 sign clip where minimum maximum`.
- Comparisons `> < >= <= ==`, mostly inside `where(...)`:
  `where(abs(err) > 1, err, 0)`. A comparison gives 1 or 0.
- `norm(v)`: the length of a vector, taken over all its parts. For forces,
  `norm(pred_F - ref_F)` is the force error per atom.
- Named parts of a vector: `x(F)`, `y(F)`, `z(F)`.
- Positions: `F[2]` is the third part of a vector. Write whole numbers only,
  starting at 0. Use a position when no name covers the part you want.

- Reductions, which combine many values into one per group:
  `mean`, `sum`, `min`, `max`, `rms` (square, average, root) and `std` (divides
  by the number of values, like NumPy). Each needs `by=`, which says what to
  combine and so the shape of the result:

  | `by=` | combines | per-atom input gives | per-structure input gives |
  |---|---|---|---|
  | `structure` | the atoms of each structure | per structure | an error |
  | `element` | the atoms of each element | per element | an error |
  | `all` | everything | one number | one number |
  | `(structure, element)` | the atoms of each element in each structure | per structure and element | an error |
  | `atom` | the same atom in every structure | per atom of one structure | an error |

  `by=atom` needs every structure to have the same atoms in the same order;
  on any other dataset the metric fails and says so. In the browser, the
  check under the formula already warns when the dataset you have open is
  such a dataset; you can still save the formula for another one.

  A forgotten `by=` is an error, never a silent average over everything.
  `by=all` counts every atom once: the mean of the per-structure means is
  something else, written `mean(mean(err, by=structure), by=all)`. Per element,
  the elements are listed by atomic number. `min(a, b)` is an error: the smaller
  of two values at each place is `minimum(a, b)`.
- A reduction keeps the parts of a vector: `sum(pred_F - ref_F, by=structure)`
  is the net force error of each structure, a vector. `mean_parts(v)`,
  `sum_parts(v)` and `max_parts(v)` average, add or take the largest of the
  parts at each place, so the two usual force
  errors are `mean(norm(pred_F - ref_F), by=all)` (mean length of the error) and
  `mean(mean_parts(abs(pred_F - ref_F)), by=all)` (mean of all x, y, z errors).
- `by=(structure, element)` gives a table: one row per structure, one column per
  element of the dataset, in order of atomic number. A structure without that
  element has no value in that cell. It shows how the error of each element
  changes from frame to frame:

  ```
  mean(mean_parts(abs(pred_F - ref_F)), by=(structure, element))
  ```

  Write the pair in this order (`by=(element, structure)` is an error). A table
  combines with one number or with another table, but not with a per-structure
  or per-element value, and it cannot be reduced again yet.
- `element("H")` is 1 for each hydrogen atom and 0 for the others; it takes
  several symbols, `element("C", "N", "O")`, and "all but H" is
  `1 - element("H")`. These symbols are the only text a formula may hold.
- `where=` on a reduction picks the rows that count, and never changes the
  result's shape:

  ```
  mean(norm(pred_F - ref_F), by=structure, where=element("H"))   # H force error per structure
  rms(norm(pred_F - ref_F), by=all, where=element("C", "O"))     # one number for C and O
  mean(abs(pred_E - ref_E), by=all, where=n_atoms > 20)          # only large structures
  ```

Reductions nest and mix with arithmetic. One number spreads to any shape, so
`norm(dF) / max(norm(dF), by=all)` scales each atom's error to 0..1, and
`abs(dE - mean(dE, by=all))` removes the mean energy shift. A per-structure
value does not spread onto atoms: `err - mean(err, by=structure)` is an error.
A per-atom value does apply to each part of a per-atom vector, so
`(pred_F - ref_F) / m` divides each atom's x, y and z by its mass.

Any bad number (infinity, not-a-number) makes the whole metric fail on that
dataset, for example a division by zero. "No value" is different: a group with
nothing to combine, such as a structure without hydrogen under
`where=element("H")`. The metric still succeeds; that place shows as a gap in a
timeline, `—` in a table, a grey atom, and is left out of scatter plots and
densities. A later reduction leaves it out too. A bad value in a row that
`where=` leaves out does not fail the metric, since the formula never uses
it, but it is not passed over in silence: Try says "1 bad value in rows left
out by where= was ignored (frame 41)", and a panel drawing the metric shows ⚠
beside its title with the same sentence on hover. A formula that grows past
2,000 characters or 50 levels of nesting is refused.

**The position trap.** A position counts parts in the layout FFAST holds, not
the layout of your file. A vector has three parts, so `F[2]` is z. A stress
given in the six-number Voigt layout (xx, yy, zz, yz, xz, xy) has `S[1]` = yy,
while the same stress as a 3×3 table has `S[0,1]` = xy. The same position can
mean different things for inputs of different layout. Prefer the names
(`x(F)`) whenever one exists.

### Your own metrics in the browser

The **Metrics** button in the top bar opens the Metrics drawer on the right, on
any tab. It lists every metric by where it comes from (built-in, project, user)
and lets you write your own as formulas, while the app runs. The line under its
title ("on aspirin · MACE") is the dataset and prediction it lists inputs for and
tries formulas on. It starts from what the left list (or the focused 3D panel)
shows and follows it; you can choose another one there without changing the tab.

1. **New metric.** The id starts with `user.`; you may type any dotted id, such
   as `mylab.e_per_atom`. A taken id is refused.
2. **Write the formula.** Click an input in the list (Reference, Prediction, File
   keys, Metrics) to put its short name in the formula and add it to the
   variables. Typing offers the same names. A name with no input is shown in red
   ("`ref_E` is not an input"), with a **Fix** button when exactly one input fits.
   The line under the formula says what the result is: per structure (can be
   plotted), per atom (can colour atoms), per element (bars), per structure and
   element (a line per element, and can colour atoms) or one number. A per-atom
   formula that uses `by=all` or `by=element` colours atoms from the whole
   dataset, so `norm(pred_F - ref_F) / max(norm(pred_F - ref_F), by=all)` gives
   1 to the largest error of the dataset. With a prediction that covers only the
   frame on screen, such a formula shows element colours.
3. **Try** computes the formula on every frame of the selected dataset and
   prediction without saving it. It shows the smallest, average and largest
   value, a histogram, how many places have no value ("12 structures have no
   value"; for a table, "100 structures × 3 elements" and its empty cells), and
   the first frames with bad values (infinity, not-a-number);
   click a frame number to go to it. **Cancel** stops it. A bad
   value fails the whole metric once it is saved, so try first.
4. **Save** keeps the metric on the server, in `~/.ffast/metrics/`, one TOML file
   each in the same format as `[[metrics.expr]]`. Every window connected to that
   server sees it, and open plots and a colouring by that metric recompute. If
   another window changed it meanwhile, the pane says "Changed in another window"
   and offers **Reload** (drop your edits) or **Overwrite**.

Only user metrics can be edited. Any other metric offers **Copy and edit**, which
starts a user copy under a new id. A formula metric copies its formula. A
Python metric shows its code under **Python code** and copies as a formula that
computes exactly the same where one can (`ffast.force_difference` copies as
`pred_F - ref_F`). A Python metric with no such formula (smoothing, densities,
geometry), such as `ffast.force_mae_per_structure_smoothed`, copies as a
formula that takes the metric itself as its input, for you to change, e.g. to
`1000 * force_mae_per_structure_smoothed`.

#### Errors: a base metric and its keep boxes

Force, acceleration and energy errors are not a long list of fixed metrics. There
are five **base metrics**: Force Difference and Acceleration Difference (per atom,
x, y and z), Energy Difference and Energy Difference (shifted) (per frame), and
Net Force Residual (per frame, x, y and z). Select one, or any metric, and the
**keep boxes** under its name say how to reduce it:

```
Keep:      [✓] atom  [✓] structure  [ ] element  [ ] x/y/z
Combine:   (•) Per component  ( ) Length
Statistic: [MAE ▾]   MAE | RMSE | Max | Mean (signed)
```

A ticked box means *keep separate*, as `by=` does in a formula. Atom and
structure ticked: each atom of each frame. Structure only: one value per frame.
Structure and element: a frame × element table. Element only: one value per
element. Nothing ticked: one number for the dataset. Atom without structure:
each atom averaged over the frames, which needs every structure to have the same
atoms in the same order (greyed otherwise). Element is greyed while atom is
ticked. **x/y/z** ticked keeps the three axes apart; unticked, **Combine** says
how they become one number: **Per component** treats them as three numbers (the
convention MACE and NequIP report), **Length** takes √(x²+y²+z²). For an error
(3, 0, 4) the length is 5 and the per-component mean 2.33. **Statistic** is greyed
when nothing is averaged; **Mean (signed)** keeps the sign, so the energy shift
is Energy Difference, nothing ticked, Mean (signed).

A change of boxes tries the new setting at once on the selected dataset and shows
the formula it writes (**Show as formula**): structure ✓, Length, RMSE is
`rms(norm(D), by=structure)` with D the base. Nothing is saved until **Create
metric**, which makes a user metric under the setting's id,
`ffast.force_difference[structure;length;rmse]`, with a name you can change. The
same setting is always the same id: a tab file or a session can name it directly
without creating it. The metrics list shows a setting only once someone created
it; when you have, the pane offers **Open it** instead. Deleting it drops only
the name: a tab that uses the setting keeps working. A formula the
boxes did not write, such as `abs(dE - mean(dE, by=all))`, opens as *custom*:
its boxes come from its shape, and changing them wraps it,
`max(that, by=all)`. From a metric already averaged per frame you can only go
coarser.

The 3D view's **Colour By** has the same boxes under the Coloring list, for the
settings that give one value per atom. There a change recolours at once and is
kept with the view, not as a metric; **Save as metric** keeps it. Atom ✓ without
structure gives each atom one colour on every frame.

The fixed metrics of earlier versions (`ffast.force_mae`,
`ffast.force_component_rmse_per_structure`, `ffast.energy_mae`, …) are gone. A
session, formula or tab that names one is refused with the setting to use
instead: "ffast.force_rmse no longer exists; use Force Difference with
structure ✓, Length, RMSE (ffast.force_difference[structure;length;rmse])".
**Delete** is refused while anything uses the metric: the pane lists the tabs,
other metrics and the colouring that do. **Export** shows the TOML to paste into a
project's `ffast.toml`.

For a per-atom metric, **Colour atoms by this** sets the colouring of the 3D view
the settings sidebar acts on. For a per-structure metric, **Plot it** adds a
timeline, for a per-element one a bars panel, and for a per-structure-and-element
one a timeline with one line per element (and the element picker), to the "My
metrics" tab (made for you, with the main view beside the plot). A
per-structure-and-element metric also colours atoms: each atom takes its
element's value in the frame on screen, the same number the timeline shows. It never changes a built-in or project tab. Any saved metric can also be
put into a panel of any tab with the tab editor.

If a project or built-in metric has the same id as a user file, that one wins and
the file is listed as "Hidden"; duplicate it under a new id, or delete it.

User metrics belong to the server: the desktop client and `ffast-cli` do not load
them, so a command gives the same numbers on every machine. Export a metric to
the project first if a script needs it. A saved session carries the formulas of
all your user metrics, and loading it on a server that lacks
them asks "Add them?". If the server has the same id with a different formula,
the session's one is added as `<id>.2` and the server's is left alone.

### Panels and tabs

A tab is a grid of panels; a panel binds metrics to axes. Panel kinds available:
`timeline`, `density`, `scatter`, `table`, `overlay_timeline`, `grouped_density`,
`grouped_table`, and `bars` (one per-element metric, `value`, as a group of
bars per element; an element with no value has no bar and says so). A
`timeline` also takes a per-structure-and-element metric as `y`: one line per
element in the element's colour, one line style per prediction, gaps for
empty cells.

```toml
[[visualization.tabs]]
name = "Dataset Fields (demo)"
has_data_selector = true

[[visualization.tabs.panels]]
kind = "timeline"
row = 0
col = 0
title = "Total charge per frame"
  [visualization.tabs.panels.metrics.y]
  metric = "demo.total_charge"

[[visualization.tabs.panels]]
kind = "density"
row = 1
col = 0
title = "Reference-energy distribution"
  [visualization.tabs.panels.metrics.value]
  metric = "demo.ref_energy_field"
  transform = "value_kde"          # reductions such as KDE are transforms
```

### Metrics in Python

For a genuinely new calculation, write a function. The decorator reads its ID,
inputs, output shape, parameters and label off the signature, annotations and
docstring, so there is very little to declare:

```python
# my_metrics.py
import numpy as np
from jaxtyping import Float
from ffast.metrics import metric, units, inputs as I
from ffast.metrics.signature import Ref

METRIC_NAMESPACE = "my_lab"          # id becomes "my_lab.max_force_per_frame"

@metric(
    unit=units.force,
    tests=[
        {
            "inputs": {"forces": [[[0.0, 0.0, 1.0], [0.0, 3.0, 0.0]]]},
            "parameters": {},
            "expected": [3.0],
            "atol": 1e-10,
        }
    ],
)
def max_force_per_frame(
    forces: Ref[I.reference_forces],
) -> Float[np.ndarray, "N_frames"]:
    """Max force component (per frame)"""
    return np.max(np.abs(forces), axis=(1, 2))
```

What the pieces mean:

- **Inputs** are the parameters before `*`, each annotated `Ref["<ref>"]`. The
  server resolves the reference before your function runs. Available:
  `reference.{energies, forces, stress, positions, elements, masses}`,
  `prediction.{energies, forces, stress}`, `selection.indices`, any dataset field,
  and any other metric's ID. Referring to another metric builds a graph edge that
  the server resolves in order.
- **Shape** comes from the return annotation. Axis names are dimension names
  (`N_frames`, `N_atoms`, `N_elements`, `xyz`, `curve_xy`), or use `-> float` for
  a scalar. Unit is the one thing you still pass by hand.
- **Tunable parameters** are keyword-only arguments after `*`. Types and defaults
  come from the signature; `Literal["fast","exact"]` becomes a choice,
  `Annotated[float, P(min=…, max=…)]` adds bounds.
- **Optional inputs** are positional with a default, and resolve to `None` when
  absent.
- Metrics run in a worker process, so they must be picklable and deterministic.
  Module level, no closures, no lambdas, no global mutation. Nothing in the
  function ever sees the Environment, a Dataset or a Model.
- `tests=[...]` run with `ffast-cli metrics test` against no project data at all,
  checking values, shape, dtype and unit.

Register the module:

```toml
[[metrics.modules]]
path = "my_metrics.py"        # relative to this config, or import_path = "pkg.mod"
```

Then check it before launching anything:

```bash
ffast-cli config validate ffast.toml
ffast-cli metrics validate
ffast-cli metrics test my_lab.max_force_per_frame
ffast-cli metrics run my_lab.max_force_per_frame --dataset examples/data/dataset.xyz
```

Anything you pass to `@metric(...)` overrides what would be inferred, so the
fully explicit form still works if you prefer it.

## Scripting without a UI

For batch work, drive the Environment directly. A complete example is in
[examples/headless/headless.py](../examples/headless/headless.py):

```python
from ffast.core.environment import startHeadlessEnvironment

env = startHeadlessEnvironment()
env.taskLoadDataset("examples/data/dataset.xyz", "ase (auto)")
env.waitForTasks(verbose=True)

dataset = env.getDatasetFromPath("examples/data/dataset.xyz")
env.loadPrepredictedDataset("examples/data/prediction.xyz", dataset.fingerprint)
model = env.models.all()[0]

energy_mae = "ffast.energy_difference[dataset;mae]"    # a keep-box setting
key = env.data.make_metric_cache_key(energy_mae, {}, model, dataset)
env.data.taskGenerateMetric(energy_mae, {}, model, dataset, key)
env.waitForTasks(verbose=True)
print(float(env.data.getCacheByKey(key, subChecks=False).values))

env.persistence.save("results")     # opens later in the UI
env.headlessQuit()
```

For a single number, don't write a script; `ffast-cli metrics run` already does
it.

## Configuration files

| File | Holds |
|------|-------|
| `config/default.json` | App defaults: plot parameters, 3D view defaults, colours |
| `config/userConfig.py` | Overrides of the above |
| `config/atoms.py` | Element data: colours, covalent radii, names |
| `config/clusters.json` | Saved cluster profiles |
| `ffast.toml` | Project config: metrics, dataset fields, tabs |

Settings in `default.json` you are most likely to want:

| Key | Default | Meaning |
|-----|---------|---------|
| `plotDistNum` | 500 | Points in a KDE distribution |
| `scatterPlotNPoints` | 50000 | Cap on scatter plot points |
| `plotPenWidth` | 3 | Plot line width |
| `energyUnit` / `forceUnit` | null | Unit labels; auto-detected when null |
| `loupeBondsWidth` | 25 | Default bond width |
| `loupeAtomSizeScale` | 1.0 | Default atom size multiplier |
| `loupeBondsLenience` | 1.1 | Multiplier on the bond detection distance |
| `loupeBGColor` | `#000000` | 3D view background |
| `loupeBondsColor` | `#404040` | Default bond colour |
| `loupeForceErrorPercentile` | 0.995 | Percentile that saturates the force-error colour scale |

## Notes on the two clients

Anything above that says "click" applies to both clients; only the route differs.
Where the desktop uses a menu item, the browser uses a button in the top bar or
the object rail.

If the desktop client will not start, [troubleshooting.md](troubleshooting.md)
covers the usual causes, nearly all of which are the native OpenGL and Qt stack
rather than FFAST itself.
