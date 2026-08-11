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
```

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
- **Tabs.** The 3D view plus one per analysis tab defined in config.
- **Right sidebar** (in the 3D view). Collapsible panes, grouped: Display, Bonds
  and Unit Cell under Appearance; Colour By, Force Vectors, Extract Subset and
  Alignment under Analysis; Camera and Export under View.

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

`examples/data/variable-sized-molecular/dataset.xyz` and `prediction.xyz` are what ship today: 100
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
- **Colour By** — elements, displacement, or any per-atom metric such as force
  error, with a choice of colormap and a colourbar. Metric parameters appear
  under the picker when the chosen metric has any.
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
2. Click **Sub** on the plot.
3. A new "Sub: <name>" dataset appears in the rail, containing exactly the
   configurations in view. It follows further zooming until you freeze it.
4. Open it in the 3D view.

The same works from scatter panels by box-selecting outliers. On a remote
session this is also the mechanism that keeps data on the cluster: only the
subset you asked for crosses the network.

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

### Panels and tabs

A tab is a grid of panels; a panel binds metrics to axes. Panel kinds available:
`timeline`, `density`, `scatter`, `table`, `overlay_timeline`, `grouped_density`,
`grouped_table`.

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
  come from the signature; `Literal["l1","l2"]` becomes a choice,
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
ffast-cli metrics run my_lab.max_force_per_frame --dataset examples/data/variable-sized-molecular/dataset.xyz
```

Anything you pass to `@metric(...)` overrides what would be inferred, so the
fully explicit form still works if you prefer it.

## Scripting without a UI

For batch work, drive the Environment directly. A complete example is in
[examples/headless/headless.py](../examples/headless/headless.py):

```python
from ffast.core.environment import startHeadlessEnvironment

env = startHeadlessEnvironment()
env.taskLoadDataset("examples/data/variable-sized-molecular/dataset.xyz", "ase (auto)")
env.waitForTasks(verbose=True)

dataset = env.getDatasetFromPath("examples/data/variable-sized-molecular/dataset.xyz")
env.loadPrepredictedDataset("examples/data/variable-sized-molecular/prediction.xyz", dataset.fingerprint)
model = env.models.all()[0]

key = env.data.make_metric_cache_key("ffast.energy_mae", {}, model, dataset)
env.data.taskGenerateMetric("ffast.energy_mae", {}, model, dataset, key)
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
