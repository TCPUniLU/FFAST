# FFAST - Force Field Analysis and Screening Tool

A Python application for analyzing and visualizing Machine Learning Force Field (MLFF) models, driven from your web browser. FFAST provides interactive tools for comparing predictions from different models against ground truth data, with rich visualization capabilities including error analysis plots and a 3D molecular viewer. The client runs in the browser over a WebSocket to a local or remote/HPC server — no native GL/Qt libraries to install.

**Key Features:**
- Config-driven error analysis tabs (Basic, Atomic, Subsystem, Gyration) built from declarative TOML — add your own metrics, plots, and whole tabs without touching Python
- Interactive 3D molecular visualization ("3D View" / Loupe) with geometry measurement, animated trajectory playback, and Kabsch alignment
- Remote execution on an HPC cluster: the GUI runs locally while all heavy compute runs on a SLURM compute node, reached over an SSH-tunnelled WebSocket
- Headless command-line tools (`ffast-cli`) to validate configs, list/inspect/run metrics, and discover dataset fields
- Dataset Fields: surface arbitrary extxyz `info`/`arrays` keys as plottable metrics with no code
- Full support for variable-sized molecular datasets
- Energy shift correction (subtract mean energy offset) across all energy error plots
- Dynamic sub-dataset creation from plot zoom/selection
- Automatic caching of expensive computations via content fingerprinting

**Please cite:** Fonseca G, Poltavsky I, Tkatchenko A. *J Chem Theory Comput.* 2023;19(23):8706-8717. [DOI: 10.1021/acs.jctc.3c00985](https://doi.org/10.1021/acs.jctc.3c00985)

---

## Table of Contents

- [Installation](#installation)
  - [Prerequisites](#prerequisites)
  - [Using pipx (Recommended)](#using-pipx-recommended)
  - [Using pip](#using-pip)
  - [Using conda / pixi](#using-conda--pixi)
  - [Using uv](#using-uv)
  - [Optional: the Qt desktop (legacy)](#optional-the-qt-desktop-legacy)
  - [Verify Installation](#verify-installation)
- [Quick Start](#quick-start)
  - [Qt → web migration](#qt--web-migration)
- [Features](#features)
  - [Model Support](#model-support)
  - [Dataset Support](#dataset-support)
  - [3D Molecular Viewer (Loupe)](#3d-molecular-viewer-loupe)
  - [Error Analysis Tools](#error-analysis-tools)
  - [Advanced Features](#advanced-features)
  - [Keyboard Shortcuts](#keyboard-shortcuts)
- [Usage Guide](#usage-guide)
  - [Working with Datasets](#working-with-datasets)
  - [Working with Models](#working-with-models)
  - [Using the Loupe 3D Viewer](#using-the-loupe-3d-viewer)
  - [Error Analysis Workflows](#error-analysis-workflows)
  - [Headless Batch Processing](#headless-batch-processing)
- [Remote Cluster Execution](#remote-cluster-execution)
- [Command-line Tools (ffast-cli)](#command-line-tools-ffast-cli)
- [Custom Metrics & Tabs (ffast.toml)](#custom-metrics--tabs-ffasttoml)
- [Example Workflow](#example-workflow)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Citation](#citation)
- [License](#license)
- [Contact](#contact)

---

## Installation

### Prerequisites

- **Python 3.11** (`pyproject.toml` pins `requires-python = "==3.11.*"`)
- **A modern web browser** (Chrome/Edge/Chromium give a chromeless app window; any browser works)
- **Supported OS**: Linux, macOS, Windows

FFAST ships as a headless Python core plus a **web client**: the `ffast` command
starts a local server and opens the app in your browser — there are **no native
GL/Qt libraries to install**, which is what makes it install and launch reliably
on any distro (see [ADR 0045](docs/adr/0045-web-client-replaces-qt.md)). The
same base install also gives you the `ffast-server` and `ffast-cli` tools.

> **Migrating from the Qt desktop?** See [Qt → web migration](#qt--web-migration)
> below. In short: `ffast` now launches the web client; the old Qt/Vispy desktop
> is available as `ffast-qt` if you install the optional `gui` extra.

### Using pipx (Recommended)

Installs `ffast` into an isolated environment and puts the command on your PATH —
the simplest way to get a working launch on a clean machine:

```bash
pipx install ffast          # from a release; or `pipx install .` from a checkout
ffast                       # starts the server on loopback + opens the browser
```

### Using pip

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

pip install ffast           # web client + server + CLI (no Qt/GL)
# or from a source checkout:
pip install -e .
```

### Using conda / pixi

A [`pixi.toml`](pixi.toml) and a conda-forge [`recipe/meta.yaml`](recipe/meta.yaml)
are provided (PyTorch resolves most reliably from conda-forge):

```bash
pixi install                # resolve + create the environment
pixi run ffast              # launch the web client
```

### Using uv

```bash
uv venv --python 3.11
uv sync                     # web client + server + CLI
```

### Optional: the Qt desktop (legacy)

```bash
pip install -e ".[gui]"     # adds PySide6/Vispy
ffast-qt                    # launch the legacy Qt desktop
```

<!-- ### Install Model Support (Optional)

Install packages for the ML models you plan to use:

```bash
pip install sgdml        # sGDML
pip install schnetpack   # SchNet
pip install mace-torch   # MACE
pip install nequip       # Nequip
pip install spookynet    # SpookyNet
``` -->

### Verify Installation

```bash
# Web client (opens the app in your browser)
ffast

# Headless tools
ffast-cli metrics list
```

If the app opens in your browser (or the CLI lists metrics), installation is
complete.

---

## Quick Start

### 1. Launch FFAST

```bash
ffast                       # server on loopback + browser
ffast --app                 # chromeless app window (Chrome/Edge/Chromium)
ffast --no-browser          # print the URL, open it yourself
```

The launcher picks free ports automatically; `--ws-port` / `--web-port` pin
them. Everything binds `127.0.0.1`, so a local session is not reachable from the
network.

**Remote / HPC server.** To drive a server running elsewhere (e.g. a SLURM
compute node), start the server there with a web port and open the app pointed
at its WebSocket port:

```bash
ffast-server --host 0.0.0.0 --port 8765 --web-port 9000
# then browse to  http://<server-host>:9000/?port=8765
```

### Qt → web migration

Phase 6 of [ADR 0045](docs/adr/0045-web-client-replaces-qt.md) retires the
Qt/Vispy desktop as the default client — it was unreliable on some Linux
distributions (native GL/Qt bundle failures). The web client delivers the same
workflow through your browser with no native libraries to install.

| Before (Qt) | Now (web) |
|-------------|-----------|
| `ffast` / `python main.py` opened the Qt desktop | `ffast` launches the web client |
| `pip install ffast[gui]` was required | base `pip install ffast` is enough |
| — | `ffast-qt` still opens the legacy Qt desktop (needs the `[gui]` extra) |

Everything else — datasets, predictions, error-analysis tabs, the 3D viewer,
remote/HPC execution, sessions — works the same; the UI just runs in a browser
tab instead of a Qt window.

### 2. Load a Dataset

- Menu: File > Load Dataset (or `Ctrl+d`)
- Supported formats:
  - sGDML `.npz` files (with `R`, `E`, `F`, `z` keys) for fixed-size datasets (same system per configuration)
  - ASE-compatible formats (`.db`, `.extxyz`, `.traj`, `.xyz`, and others)
  - Variable-sized datasets (different atom counts per configuration) are automatically detected

The dataset appears in the left sidebar.

### 3. Load a Pre-computed Predictions

<!-- **Method A: Load a trained model file**
- Menu: File > Load Model (or `Ctrl+m`)
- Select your model file (`.model`, `.pth`, `.npz`, etc.)
- Model type is auto-detected -->

<!-- **Method B: Load pre-computed predictions** -->
- Menu: File > Load Prediction (or `Ctrl+p`)
- Select an `.npz` file with `E` (energies) and `F` (forces) keys
- Select the corresponding dataset from the dropdown

### 4. Explore Error Analysis

Once a model and dataset are loaded:
- Click the **Basic Errors** tab to see energy and force MAE/RMSE timelines, distributions, true-vs-predicted scatter, and metric tables
- Explore the other tabs: **Atomic Errors**, **Subsystem Errors**, **Gyration**

### 5. Open the 3D Viewer

- Menu: 3D View > New (or `Ctrl+n`)
- Select your dataset from the dropdown
- Use the slider to navigate through configurations
- Left-drag to rotate, right-drag to pan, scroll to zoom

---

## Features

### Model Support

<!-- - **Supported models**: sGDML, MACE, Nequip, SchNet, SpookyNet -->
<!-- - **Custom predictions**: Load pre-computed energies/forces from `.npz` files -->
<!-- - **Ghost models**: When loading a saved session, models are reconstructed from cached predictions if the original model file is unavailable -->
<!-- - **Model comparison**: Load multiple models and compare side-by-side with automatic color coding -->
- **Zero model**: Load a reference model that predicts zero for all outputs (File > Load Zero Model, or `Ctrl+0`)
   - Used for quick check of suspicious energy/force ranges in the dataset.

### Dataset Support

- **sGDML format**: `.npz` files with `R`, `E`, `F`, `z` keys
- **ASE formats**: `.db`, `.extxyz`, `.traj`, `.xyz`, and all other ASE-supported formats
- **Variable-sized molecules**: Full support for datasets with different atom counts per configuration, automatically detected on load
- **Unit cells**: Periodic boundary conditions are supported when present in the data

### 3D Molecular Viewer (Loupe)

Interactive 3D visualization with:
- **Atom rendering**: Customizable colors (by element, force error, mean force error, or displacement) and adjustable sizes
- **Bond visualization**: Dynamic bond detection with adjustable distance cutoff, or fixed bonds
- **Force vectors**: Display force arrows with adjustable length and normalization, with an option to draw them only on a selected atom subset
- **Unit cells**: Visualize periodic boundary conditions
- **Geometry measurement**: Measure distances (2 atoms), angles (3 atoms), and dihedral angles (4 atoms) interactively
- **Trajectory playback**: Animate the trajectory with play/step controls, configurable FPS, and frame skipping (INDEX / VIDEO panel)
- **View settings**: Kabsch alignment of every frame onto frame 0 (optionally heavy-atoms-only), 3-atom reference-frame alignment, atom index labels, index/element filter and highlight, and adjustable pick radius
- **Atom alignment**: Align structures using a 3-atom reference frame
- **XYZ axes**: Display orientation axes in the viewport corner
- **Camera controls**: Manual positioning, field of view adjustment, center-of-mass tracking, save/load camera positions
- **Atom filtering**: Select specific atoms to focus analysis on a subset, letting you isolate and analyze specific regions of a molecule, such as an active site or functional group
- **Selection**: Click atoms to select, or rectangle-select with Ctrl+drag
- **Export**: Save screenshots as PNG (with optional transparent background)

**3D View menu controls** (apply to all open viewer windows):
- Bond Width: Thin, Normal, Thick, Extra Thick
- Atom Size: 50%, 75%, 100%, 150%, 200%
- Bond Color and Background Color pickers

### Error Analysis Tools

The analysis tabs are built declaratively from TOML (see [Custom Metrics & Tabs](#custom-metrics--tabs-ffasttoml)). Four tabs ship built-in:

- **Basic Errors**: Energy and force MAE/RMSE timelines and KDE distributions, true-vs-predicted scatter plots for energies and forces, plus MAE and RMSE summary tables. Includes a "Subtract mean energy offset" toggle that removes the constant energy bias from all energy error plots and tables.
- **Atomic Errors**: Per-element force error distributions and tables (grouped by element, with an element picker) to identify problematic species.
- **Subsystem Errors**: Net (total) per-structure force error distribution and MAE/RMSE tables.
- **Gyration**: Radius of gyration (weighted by atomic number) timeline and distribution, plus overlay timelines against energy and force error. Shares a smoothing control across its panels.

You can add further tabs, panels, and metrics yourself without writing code — see [Custom Metrics & Tabs](#custom-metrics--tabs-ffasttoml).

### Advanced Features

- **Remote cluster execution**: Connect to an HPC cluster (SLURM) and run all compute on a compute node while the GUI stays local — see [Remote Cluster Execution](#remote-cluster-execution)
- **Custom metrics & tabs**: Declare new metrics, Dataset Fields, plots, and whole analysis tabs in a project `ffast.toml` with no Python — see [Custom Metrics & Tabs](#custom-metrics--tabs-ffasttoml)
- **Command-line tools**: `ffast-cli` validates configs, lists/inspects/tests/runs metrics, and discovers dataset fields headlessly — see [Command-line Tools](#command-line-tools-ffast-cli)
- **Sub-datasets**: Create filtered datasets from plot zoom/selection
  - Click the "Sub" button on any compatible plot
  - Sub-dataset updates dynamically as you zoom/pan
  - Can be opened in a separate 3D View window for inspection
- **Energy shift correction**: A toggle in the Basic Errors tab subtracts the mean energy offset (mean of predicted minus true energies) from all energy error calculations, affecting distributions, timelines, scatter plots, and MAE/RMSE tables
- **Atom filtering**: Focus analysis on specific atoms via the 3D View atom filter panel
- **Headless mode**: Batch processing without the GUI for large-scale computations on remote machines
- **Data caching**: Automatic caching of expensive computations, keyed by content fingerprints of models and datasets
- **Save/Load sessions**: Save the entire working state (datasets, models, all cached computations) to a directory for later restoration

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+d` | Load dataset |
| `Ctrl+m` | Load model |
| `Ctrl+p` | Load prediction |
| `Ctrl+0` | Load zero model |
| `Ctrl+l` | Load saved session |
| `Ctrl+s` | Save session |
| `Ctrl+n` | New 3D View window |
| `Ctrl+Shift+C` | Connect to cluster |
| `Ctrl+Shift+L` | Connect to local server |
| `Ctrl+Shift+D` | Load remote dataset |
| `Ctrl+Shift+P` | Load remote prediction |

---

## Usage Guide

### Working with Datasets

#### Loading Datasets

FFAST supports two main dataset formats:

**1. sGDML .npz format:**
- Required keys: `R` (positions), `E` (energies), `F` (forces), `z` (atomic numbers)
- Shapes: R: `(N, n_atoms, 3)`, E: `(N,)`, F: `(N, n_atoms, 3)`, z: `(n_atoms,)`
- Optional: lattice vectors for periodic systems

**2. ASE-compatible formats:**
- `.db`, `.extxyz`, `.traj`, `.xyz`, and others
- Energies read from `.info['energy']` or `.get_potential_energy()`
- Forces read from `.arrays['forces']` or `.get_forces()`
- Automatically detects whether atom counts are uniform or variable across configurations

#### Dataset Information

After loading, view dataset details in the left sidebar:
- Number of configurations
- Atom count (range shown for variable-sized datasets)
- Chemical formula
- Dataset fingerprint (MD5 hash used for cache matching)

<!-- ### Pre-computed Predictions -->

<!-- #### Method 1: Load Trained Model Files

1. Menu: File > Load Model (or `Ctrl+m`)
2. Select your model file:
   - MACE: `.model`
   - Nequip: `.pth`
   - sGDML: `.npz`
   - SchNet: `.pth`
   - SpookyNet: `.pth`
3. Model type is automatically detected
4. Model appears in the sidebar -->

### Load Pre-computed Predictions

<!-- Useful for sharing results without sharing trained models: -->
#### For .npz predictions:
1. Create an `.npz` file with:
   - `E`: energies array, shape `(N,)`
   - `F`: forces array, shape `(N, n_atoms, 3)`
2. Menu: File > Load Prediction (or `Ctrl+p`)
3. Select your `.npz` file
4. Select the corresponding dataset from the dropdown
5. The prediction appears as a model in the sidebar

#### For ASE-compatible predictions:
1. Create an ASE-readable file (e.g., `.xyz`, `.db`) with:
   - Energies in `.info['energy']` or via `.get_potential_energy()`
   - Forces in `.arrays['forces']` or via `.get_forces()`
2. Load the file as a dataset (File > Load Dataset)
3. The energies and forces are automatically treated as predictions for error analysis

<!-- #### Generating Predictions

Predictions are generated automatically when needed (e.g., when opening error plots). Progress is shown in the sidebar task list. For large datasets, consider using [headless mode](#headless-batch-processing) to pre-compute predictions. -->

<!-- #### Model Fingerprints

Models are identified by MD5 fingerprints based on their parameters. This enables automatic matching of cached predictions to models and datasets across sessions. -->

### Using the Loupe 3D Viewer

#### Opening the 3D Viewer

Menu: 3D View > New (or `Ctrl+n`). A window opens with a dataset selection dropdown. (The "New" item is enabled once the local server connection is established.)

#### Basic Controls

| Action | Control |
|--------|---------|
| Rotate view | Left-click and drag |
| Pan | Right-click and drag |
| Zoom | Mouse scroll wheel |
| Select atom | Left-click on atom |
| Rectangle select | Ctrl + drag |

#### Navigating the Trajectory

- **Frame slider**: Drag to change configuration
- **Frame number**: Displays the current frame index

#### Sidebar Panels

**ATOMS:**
- Show/Hide atoms
- Size: Adjust atom sphere radius
- Coloring modes: Elements (default), Force Error, Mean Force Error, Total Displacement, Mean Displacement

**BONDS:**
- Show/Hide bonds
- Width: Adjust bond line thickness
- Type: Dynamic (distance-based detection) or Fixed
- Cutoff lenience: Multiplier for bond detection distance threshold

**FORCE VECTORS:**
- Enable/Disable force arrow display
- Length: Scale arrow length
- Normalized: Set all arrows to equal length per frame
- Filter to selection: Draw arrows only on a chosen atom subset

**UNIT CELL:**
- Show/Hide periodic cell boundary edges (available when lattice data is present)

**CAMERA:**
- Manual camera positioning (coordinates and target)
- Field of view adjustment
- Center of mass tracking (auto-center on molecular COM)
- Save/Load camera positions

**Info / Measurement** (tools within the ATOMS pane):
- Select 1 atom: View position, element, and index
- Select 2 atoms: Measure distance
- Select 3 atoms: Measure bond angle
- Select 4 atoms: Measure dihedral angle

**Alignment** (tools within the ATOMS pane):
- Select 3 reference atoms to align the molecular structure
- Provides translation and rotation alignment

**INDEX / VIDEO:**
- Play / step through the trajectory
- FPS: Playback speed
- Skip frames: Advance more than one frame per tick

**VIEW SETTINGS:**
- Kabsch align: Rigidly align every frame onto frame 0 (minimizes RMSD); optionally heavy-atoms-only
- 3-atom frame align: Align frames using three reference atom indices
- Atom index labels: Overlay atom index labels in the 3D view
- Filter indices: Keep only listed atoms/elements (e.g. `0 1 2`, `C`, or `-H` to exclude)
- Highlight indices: Show a selection overlay on listed atoms
- Pick radius: Pointer-picking tolerance in pixels

**Axes** (toggle within the CAMERA pane):
- Toggle XYZ orientation axes display in the viewport corner

**ATOM FILTER:**
- Select specific atoms by clicking or rectangle-selecting
- Apply filter to focus analysis on atom subsets

**EXPORT:**
- Save current view as PNG
- Optional transparent background

### Error Analysis Workflows

#### Basic Error Analysis

1. Load a dataset and model (or pre-computed predictions)
2. Click the **Basic Errors** tab
3. View plots:
   - Energy MAE timeline: Identifies configurations with high energy errors
   - Force MAE timeline: Tracks force prediction quality across the trajectory
   - Energy/Force error distributions: KDE-smoothed histograms of error magnitudes
   - MAE and RMSE summary tables: Per-model, per-dataset metrics

**Energy shift correction:** Check "Subtract mean energy offset" to remove the constant energy bias (mean of E_predicted - E_true) from all energy error calculations. When active, all affected plots and tables update their titles to show "(shifted)".

#### Identifying Problematic Configurations

**Using timeline plots:**
1. In Basic Errors, look for peaks in the MAE timeline
2. Note the frame index of high-error configurations

**Creating sub-datasets:**
1. Open any error timeline plot
2. Zoom/pan to a region of interest (e.g., a high-error region)
3. Click the **Sub** toggle button in the plot toolbar
4. A new sub-dataset appears in the sidebar: "Sub: [dataset_name]"
5. Open the sub-dataset in Loupe to visualize these configurations in 3D

Sub-datasets update dynamically as you zoom.

#### Atomic-Level Error Analysis

1. Click the **Atomic Errors** tab
2. View the per-element force error distributions
3. Identify which elements or specific atoms have consistently high errors

#### Correlation Analysis

1. Click the **Basic Errors** tab
2. View the predicted vs. actual scatter panels for energies and forces
3. Points close to the diagonal indicate good predictions; outliers indicate problematic configurations
4. Click or box-select points to create a sub-dataset from outliers

### Headless Batch Processing

For expensive computations on large datasets, use headless mode to run without a GUI, including on remote compute nodes.

#### Example Script

```python
import os
import sys
from pathlib import Path

# Set working directory and Python path to the FFAST project root
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from client.environment import startHeadlessEnvironment

# Initialize headless environment
env = startHeadlessEnvironment()

# Load dataset (use "sGDML" for .npz or "ase (auto)" for ASE formats)
env.taskLoadDataset("examples/data/dataset.xyz", "ase (auto)")
env.waitForTasks(verbose=True)

# Get the loaded dataset and its fingerprint
dataset = env.getDatasetFromPath("examples/data/dataset.xyz")

# Load pre-computed predictions (ASE file with energies and forces).
# The second argument is the dataset fingerprint to match against.
env.loadPrepredictedDataset("examples/data/prediction.xyz", dataset.fingerprint)

# Get the model created from the predictions (ghost model)
model = env.models.all()[0]

# Queue metric computations by Metric ID (see `ffast-cli metrics list`)
metrics = [
    ("ffast.energy_mae", {}),
    ("ffast.energy_rmse", {}),
    ("ffast.force_mae_global", {}),
    ("ffast.force_rmse_global", {}),
]
for metric_id, params in metrics:
    key = env.data.make_metric_cache_key(metric_id, params, model, dataset)
    env.data.taskGenerateMetric(metric_id, params, model, dataset, key)
env.waitForTasks(verbose=True)

# Retrieve computed metrics from the cache
def get_metric(metric_id, params={}):
    key = env.data.make_metric_cache_key(metric_id, params, model, dataset)
    result = env.data.getCacheByKey(key, subChecks=False)
    return float(result.values) if result is not None else None

print(f"Energy MAE:  {get_metric('ffast.energy_mae'):.4f}")
print(f"Energy RMSE: {get_metric('ffast.energy_rmse'):.4f}")
print(f"Force MAE:   {get_metric('ffast.force_mae_global'):.4f}")
print(f"Force RMSE:  {get_metric('ffast.force_rmse_global'):.4f}")

# Save session for later use in the GUI
# Creates a directory at the given path containing:
#   info.json      - dataset/model metadata
#   cache/*.npz    - all computed data (metrics, distributions, errors)
# Load it in the GUI via File > Load (Ctrl+l).
savePath = os.path.join(PROJECT_ROOT, "results")
env.persistence.save(savePath)
print(f"\nSession saved to: {savePath}")

# Clean up
env.headlessQuit()
```

Run:
```bash
python examples/headless/headless.py
```

> **Tip:** For one-off metric computation you don't need to write a script — use the [`ffast-cli metrics run`](#command-line-tools-ffast-cli) command instead.

#### Loading Pre-computed Results in the GUI

1. Launch the GUI: `python main.py`
2. Menu: File > Load (or `Ctrl+l`)
3. Navigate to the saved directory (e.g., `results/`)
4. Datasets, models, and all cached computations are restored automatically

Note: Original dataset files must still be accessible at their saved paths. If model files are unavailable, ghost models are created from the cached predictions.

---

## Remote Cluster Execution

FFAST can run all heavy computation on an HPC cluster while the GUI stays on your laptop. A lightweight server process (`ffast-server`) runs on a SLURM compute node, and the local GUI connects to it over a WebSocket tunnelled through SSH. Large datasets never leave the cluster — only the subsets you select (sub-datasets) are transferred to the local 3D viewer.

> Desktop mode already runs a *managed local* `ffast-server` automatically; remote mode points the very same protocol at a cluster node instead.

### 1. Define a Cluster Profile

Add a profile to `config/clusters.json`:

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

| Group | Fields |
|-------|--------|
| SSH | `name`, `host`, `username`, `identity_file` (key auth only), `ffast_server_cmd` (must end by launching `ffast-server` on the node) |
| Scheduler | `partition`, `account`, `qos`, `job_name` |
| Resources | `cores`, `cpus_per_task`, `ntasks_per_node`, `gpus_per_task`, `gpu_count`, `memory_mb`, `time_limit` (`HH:MM:SS`) |
| Recovery | `snapshot_interval_minutes` (server auto-snapshots state; `0` disables) |

Profiles can also be created, edited, and saved from the connection dialog.

### 2. Connect

- Menu: File > Connect to Cluster… (or `Ctrl+Shift+C`)
- Choose a profile and confirm. FFAST submits a SLURM job, waits for it to start, opens an SSH tunnel to the node, and connects.
- If a job for that profile is already running, you are offered to **reconnect** to it instead of submitting a new one.

(For testing, File > Connect to Local Server… / `Ctrl+Shift+L` connects directly to a running `ffast-server` at a given `host:port`.)

### 3. Load Remote Data

- **Load Remote Dataset…** (`Ctrl+Shift+D`): enter a path on the cluster filesystem. For ASE files the server probes the first frame and lets you pick the energy/force keys; a stride dialog lets you subsample very large trajectories.
- **Load Remote Prediction…** (`Ctrl+Shift+P`): attach a cluster-side prediction file (`.npz` or ASE) to a loaded remote dataset. Only the prediction arrays are transferred to the client.

Open a sub-dataset in the 3D View to inspect specific configurations locally.

> **Note:** Live model inference driven from the remote UI is not yet wired — remote workflows compute predictions ahead of time and attach them with *Load Remote Prediction*.

---

## Command-line Tools (ffast-cli)

`ffast-cli` is a headless companion CLI (installed as a console script with `pip install -e .`, or run as `python -m ffast.cli`). It drives the metric/config machinery without launching the GUI.

```bash
# Validate a project config
ffast-cli config validate ffast.toml

# Metrics
ffast-cli metrics list [--config ffast.toml]          # list registered metric IDs
ffast-cli metrics inspect <metric_id> [--config ...]  # show inputs, params, shape, unit
ffast-cli metrics test [<metric_id>] [--config ...]   # run a metric's self-tests (all if omitted)
ffast-cli metrics validate [--config ...]             # freeze the metric graph; report ref/shape/cycle errors

# Compute a metric against real data
ffast-cli metrics run <metric_id> --dataset path/to/data.xyz \
    [--dataset-type "ase (auto)"] \
    [--prediction pred.xyz] [--pred-energy-key energy] [--pred-force-key forces] \
    [--param KEY=VALUE ...] [--config ffast.toml] [--json] [--verbose]

# Visualization (3D scene) stages
ffast-cli stages list
ffast-cli stages inspect <stage_id>
ffast-cli stages test [<stage_id>]

# Discover which extxyz keys a file exposes as Dataset Fields
ffast-cli dataset keys path/to/data.xyz
```

`metrics list`, `inspect`, `run`, and `validate` all honor a project config (auto-discovered or via `--config`), so any custom metrics and Dataset Fields you declare are available to them too.

---

## Custom Metrics & Tabs (ffast.toml)

FFAST's metrics, plots, and analysis tabs are configured declaratively. A project `ffast.toml` overlays the built-in defaults: an empty file keeps the full default experience, and named entries add or tune features. The config is auto-discovered as the nearest `ffast.toml` found by searching upward from the dataset/session directory, or loaded explicitly via **File > Load Config…** (or `--config` on the CLI). Unknown keys are rejected rather than ignored.

You can do three things with **no Python**:

### 1. Dataset Fields — surface extxyz keys as metrics

Any per-frame (`atoms.info`) or per-atom (`atoms.arrays`) numeric key in your file can be exposed as a plottable metric:

```toml
[[metrics.fields]]
id    = "demo.total_charge"
ref   = "reference.info.total_charge"   # info.<key> → per-frame scalar
label = "Total charge"
unit  = "dimensionless"
```

`ref` is `{reference,prediction}.{info,atoms}.<key>`; `info` keys become per-frame scalars and `atoms` keys become per-atom values. Discover a file's usable keys with `ffast-cli dataset keys <file>`. (A demo `ffast.toml` lives at the repo root.)

### 2. Analysis Tabs & Panels — new plots and tabs

Declare a grid of panels that bind metrics to axes. Built-in panel kinds include `timeline`, `density`, `scatter`, `table`, `overlay_timeline`, `grouped_density`, and `grouped_table`:

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
  transform = "value_kde"          # reductions (KDE, smoothing, …) are transforms
```

The four built-in tabs (Basic Errors, Atomic Errors, Subsystem Errors, Gyration) are themselves defined this way in `ffast/config/builtin_tabs/`.

### 3. Custom metric modules — new calculations

For genuinely new computations, write a small Python module and point the config at it. A metric is a pure function wrapped with the `@metric` decorator from `ffast.metrics`. Its **id, inputs, output shape, compute parameters, and label are inferred from the function signature, type annotations, and docstring** — you only declare what can't be inferred (`unit`, `tests`):

```python
# my_metrics.py
import numpy as np
from jaxtyping import Float
from ffast.metrics import metric, units, inputs as I
from ffast.metrics.signature import Ref

METRIC_NAMESPACE = "my_lab"     # metric id = "<namespace>.<function name>"

@metric(
    unit=units.force,
    tests=[                                     # runnable via `ffast-cli metrics test`
        {
            "inputs": {"forces": [[[0.0, 0.0, 1.0], [0.0, 3.0, 0.0]]]},
            "parameters": {},
            "expected": [3.0],
            "atol": 1e-10,
        }
    ],
)
def max_force_per_frame(
    forces: Ref[I.reference_forces],            # input ref → resolved by the server
) -> Float[np.ndarray, "N_frames"]:             # return shape: one value per frame
    """Max force component (per frame)"""        # docstring line 1 → metric label
    # forces has shape (N_frames, N_atoms, 3); return one scalar per frame.
    # The function receives ONLY its declared inputs/parameters — never the
    # Environment, Dataset, or Model objects.
    return np.max(np.abs(forces), axis=(1, 2))
```

Key points:

- **Id** is `METRIC_NAMESPACE + "." + function name` (must contain a dot). **Label/description** come from the docstring (first line / remainder). Pass `id=`/`namespace=`/`label=`/`description=` to override.
- **Inputs** are the parameters *before* `*`, each annotated with `Ref["<ref>"]`; the server resolves the ref before your function runs. Available constants (`from ffast.metrics import inputs as I`): `reference.{energies, forces, stress, positions, elements, masses}`, `prediction.{energies, forces, stress}`, and `selection.indices`. A `Ref` to any **Dataset Field** (`"reference.atoms.charges"`) or **another metric's ID** (`"ffast.energy_difference"`) builds a [Metric Graph](docs/adr/0011-pure-metrics-with-configuration-driven-presentation.md) edge that the server resolves in order.
- **Shape** comes from the return annotation: a jaxtyping array `Float[np.ndarray, "<dims>"]` whose axis names are `dims` names (`N_frames`, `N_atoms`, `N_elements`, `xyz`, `curve_xy`, …), or `-> float` for a scalar. **Unit** is the one thing you still pass explicitly (`units.energy`, `units.force`, `units.dimensionless`, …).
- **Optional inputs** that may be absent are positional params with a default — e.g. `offsets=None` resolves to `None` when missing.
- **Tunable compute parameters** are keyword-only args *after* `*`; their type/default come from the signature (`Literal["l1","l2"]` → choice, `bool`/`int`/`float`), with `Annotated[float, P(min=…, max=…, label=…)]` for extra metadata.
- Metrics must be **deterministic and picklable** — they execute in an isolated worker pool, so keep them at module level (no closures/lambdas) with no global mutation.
- `tests=[...]` run headlessly with `ffast-cli metrics test my_lab.max_force_per_frame` and validate values, shape, dtype, and unit without any project data.

> Inference is optional: anything you pass to `@metric(...)` overrides what would be inferred, so the fully-explicit form (`@metric(id=..., inputs={...}, shape=(dims.N_frames,), parameters={...})`) still works unchanged.

Then register the module in your `ffast.toml`:

```toml
[[metrics.modules]]
path = "my_metrics.py"        # relative to this config file (or use import_path = "pkg.mod")
```

Validate everything before launching the GUI:

```bash
ffast-cli config validate ffast.toml      # config structure
ffast-cli metrics validate                # freezes the metric graph; checks refs/shapes/cycles
ffast-cli metrics test my_lab.max_force_per_frame
ffast-cli metrics run  my_lab.max_force_per_frame --dataset examples/data/dataset.xyz
```

Once it validates, the new metric ID can be bound to any panel (see [Analysis Tabs & Panels](#2-analysis-tabs--panels--new-plots-and-tabs) above) or used for 3D-view atom coloring.

---

## Example Workflow

This tutorial uses the pre-computed predictions in `examples/` for two MD22 datasets.

### Prerequisites

Download MD22 datasets from http://www.sgdml.org/#datasets:
- MD22 Docosahexaenoic acid (DHA)
- MD22 Stachyose

Save as `dha.npz` and `stachyose.npz` in a directory of your choice.

### Step-by-Step Tutorial

#### 1. Launch FFAST

```bash
python main.py --workdir /path/to/downloaded/datasets
```

#### 2. Load Datasets

- Menu: File > Load Dataset (`Ctrl+d`)
- Select `dha.npz`, then repeat for `stachyose.npz`
- Both datasets appear in the left sidebar

#### 3. Load Pre-computed Predictions

The `examples/` directory contains pre-computed MACE and Nequip predictions.

- Menu: File > Load (`Ctrl+l`)
- Navigate to `examples/MACE/` and open it
- Repeat for `examples/Nequip/`

If the dataset fingerprints match, you will see models appear in the sidebar. These may appear as ghost models (reconstructed from cached predictions).

You should now have 2 datasets and 2 models.

#### 4. View Basic Errors

Click the **Basic Errors** tab. You will see:
- Energy and force MAE timelines
- Error distribution histograms (KDE-smoothed)
- MAE and RMSE summary tables

Exploration tips:
- Hover over points to see values
- Zoom with the scroll wheel
- Pan by dragging
- Right-click for view options

Both models are shown in different colors for comparison.

#### 5. Explore Atomic Errors

Click the **Atomic Errors** tab to see per-element force error distributions. Look for elements with consistently higher errors.

#### 6. Create a Sub-dataset

1. Go to the **Basic Errors** tab
2. In the force MAE timeline, zoom into a region with high errors
3. Click the **Sub** toggle button in the plot toolbar
4. A new "Sub: dha" dataset appears in the sidebar, containing only the configurations visible in the zoomed view

#### 7. Visualize in the 3D Viewer

1. Menu: 3D View > New (`Ctrl+n`)
2. Select "Sub: dha" from the dropdown
3. Enable force vectors in the FORCE VECTORS panel
4. Drag the frame slider to browse configurations with high errors
5. Use the Info/Measurement panel to measure distances or angles of interest

#### 8. Compare Model Performance

In the **Basic Errors** tab, compare the predicted vs. actual scatter panels for both models. Check how tightly the points cluster around the diagonal.

Explore the other tabs (Atomic Errors, Subsystem Errors, Gyration) for additional insights.

---

## Configuration

### Command-line Options

```bash
python main.py [--workdir PATH]
```

- `--workdir PATH`: Set default directory for file dialogs

Debug logging is automatically saved to `debug.log` in the FFAST directory.

### Configuration Files

- `config/default.json`: Default app settings (plot parameters, 3D viewer defaults, colors)
- `config/userConfig.py`: User configuration overrides
- `config/atoms.py`: Atomic element data (colors, covalent radii, element names)
- `config/clusters.json`: Saved cluster connection profiles for [remote execution](#remote-cluster-execution)
- `ffast.toml` (project config): Custom metrics, Dataset Fields, and analysis tabs — see [Custom Metrics & Tabs](#custom-metrics--tabs-ffasttoml)

### Key Configuration Options (default.json)

| Option | Default | Description |
|--------|---------|-------------|
| `plotDistNum` | 500 | Number of points in KDE distributions |
| `scatterPlotNPoints` | 50000 | Maximum points in scatter plots |
| `plotPenWidth` | 3 | Line width in plots |
| `energyUnit` | null | Energy unit label (auto-detected if null) |
| `forceUnit` | null | Force unit label (auto-detected if null) |
| `loupeBondsWidth` | 25 | Default bond line width |
| `loupeAtomSizeScale` | 1.0 | Default atom size multiplier |
| `loupeBondsLenience` | 1.1 | Bond detection distance multiplier |
| `loupeBGColor` | "#000000" | Loupe background color |
| `loupeBondsColor` | "#404040" | Default bond color |
| `loupeForceErrorPercentile` | 0.995 | Percentile for force error color scaling |

---

## Troubleshooting

### Installation Issues

**Segmentation fault on startup:**
- Ensure you are using Python 3.11 (`requires-python = "==3.11.*"`).
- Recreate the virtual environment: `rm -rf .venv && uv venv --python 3.11 && uv sync`
- Test PySide6: `python -c "from PySide6.QtWidgets import QApplication"`

**ImportError: No module named 'PySide6':**
- Install with: `pip install "pyside6>=6.8,<6.9"`

**OpenGL errors on startup:**
- Update graphics drivers
- On Linux: `sudo apt install libgl1-mesa-glx`

### Qt Platform Plugin Issues

**"Could not find the Qt platform plugin 'cocoa'" (macOS):**

This can occur when PySide6 is installed in a directory synced by iCloud Drive.

Solutions:
1. **Move the virtual environment outside of iCloud Drive:**
   ```bash
   python -m venv ~/venvs/ffast
   source ~/venvs/ffast/bin/activate
   pip install -e .
   ```

2. **Recreate the environment:**
   ```bash
   rm -rf .venv && uv venv --python 3.11 && uv sync
   ```

**UI elements not rendering correctly:**
- Ensure PySide6 is 6.8.x: `pip install "pyside6>=6.8,<6.9"`

### Model Loading Issues

**"Model type not recognized":**
- Install the corresponding model package (see [Install Model Support](#install-model-support-optional))

**"Fingerprint mismatch" when loading predictions:**
- The dataset has changed since predictions were computed. Regenerate predictions with the current dataset, or use the exact same dataset file.

**Model loads but predictions fail:**
- Verify the model was trained for the correct dataset format
- Check that the dataset has all required fields (`R`, `E`, `F`, `z`)
- Check `debug.log` for detailed error messages

### Dataset Issues

**"Cannot load dataset" error:**
- Verify the file format is supported (sGDML `.npz` or ASE-compatible)
- For `.npz`: Check it contains `R`, `E`, `F`, `z` keys
- For ASE formats: Test with `python -c "import ase.io; ase.io.read('file')"`

### Performance Issues

**Slow predictions on large datasets:**
- Use headless mode for batch processing
- Predictions are cached automatically for reuse

**Loupe viewer is laggy:**
- Hide bonds or force vectors when not needed
- Reduce atom size
- Create a smaller sub-dataset
- Update graphics drivers

**Loupe window is blank:**
- Check OpenGL support: `glxinfo | grep OpenGL` (Linux)
- Try software rendering: `export LIBGL_ALWAYS_SOFTWARE=1` before running
- Update graphics drivers

### Data Issues

**"No data available" in plots:**
- Ensure both a dataset and model are loaded
- Wait for predictions to finish (check progress in the sidebar)
- Verify the model and dataset are compatible

**Ghost models appearing (models with hash names):**
- These are created from cached predictions when the original model file is unavailable. They function normally for viewing pre-computed results. Delete them from the sidebar if not needed.

### Getting Help

If you encounter issues not covered here:
1. Check `debug.log` in the FFAST directory for detailed error messages
2. Report issues at the project's GitHub repository

---

## Development

### For Developers

If you want to contribute to the development of FFAST, here are some guidelines:

#### Code Structure

- `main.py`: GUI entry point and main event loop
- `server.py`: `ffast-server` — the WebSocket server that runs the Environment headlessly on a (local or cluster) node
- `ffast/`: Core engine package
  - `metrics/`: Metric registry, built-in metrics, and transforms
  - `config/`: TOML config schema and the built-in analysis tab definitions (`builtin_tabs/`)
  - `visualization/`: Scene, pipeline, and stages that drive the 3D view
  - `cli/`: The `ffast-cli` command-line tool
  - `protocol/`, `renderers/`, `session/`: Wire protocol, renderer backends, session persistence
- `client/`: The Environment and its services (cache, models, datasets, data, remote, persistence) plus the TaskManager
- `cluster/`: SLURM backend, remote session, SSH tunnelling, and the remote dataset proxy
- `UI/`: Qt UI components (MainWindow, SideBar, Plots, 3D View/Loupe, panels, controls, dialogs)
- `modules/`: Auto-discovered pluggable modules (3D viewer features; config-driven analysis tabs via `configTabs.py`)
- `datasetLoaders/`, `modelLoaders/`: Dataset and model loader base classes
- `config/`: JSON config files (`default.json`, `clusters.json`)

#### Adding Analysis or Features

- **New metrics, plots, or whole tabs** — prefer the declarative route: add them to a project `ffast.toml`, no Python required. See [Custom Metrics & Tabs](#custom-metrics--tabs-ffasttoml).
- **New 3D viewer features or loaders** — add a `modules/my_module.py`:
  1. Define `DEPENDENCIES = ["other_module"]` if needed.
  2. Implement one or more hooks:
     - `loadData(env)`: Register data types / metrics
     - `loadUI(UIHandler, env)`: Add UI components (panels, tabs)
     - `loadLoupe(loupeViewer, env, dataset)`: Add 3D viewer features
  3. The module is auto-discovered and loaded in dependency order.

### Contributing

Contributions are welcome:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Follow code style and add tests if applicable
4. Test your changes by running `python main.py` and checking `debug.log`
5. Submit a pull request

For major changes, open an issue first to discuss the approach.

---

## Citation

If you use FFAST in your research, please cite:

```bibtex
@article{fonseca2023ffast,
  title={Force Field Analysis Software and Tools (FFAST): Assessing Machine Learning Force Fields under the Microscope},
  author={Fonseca, Gregory and Poltavsky, Igor and Tkatchenko, Alexandre},
  journal={Journal of Chemical Theory and Computation},
  volume={19},
  number={23},
  pages={8706--8717},
  year={2023},
  publisher={American Chemical Society},
  doi={10.1021/acs.jctc.3c00985},
  pmid={38011895},
  pmcid={PMC10720330}
}
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Acknowledgments

- **MD22 datasets**: Stefan Chmiela et al., sgdml.org
- **ASE**: Atomic Simulation Environment developers
- **Vispy**: High-performance interactive 2D/3D data visualization library
- **PyQtGraph**: Scientific graphics and GUI library
- **Model frameworks**: MACE, Nequip, sGDML, SchNet, SpookyNet developers

---