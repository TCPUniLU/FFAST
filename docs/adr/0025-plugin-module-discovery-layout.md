# ADR 0025: Plugin Module Discovery and Layout

**Status:** Accepted  
**Date:** 2026-06-30

## Context

FFAST has a plugin mechanism: at startup `loadModules` (in `utils.py`)
glob-scans a directory, imports each `.py` file via
`importlib.util.spec_from_file_location`, reads an optional `DEPENDENCIES`
list, topologically orders the files with Kahn's algorithm, then calls each
module's optional `loadData(env)` / registers its `CLIENT_FEATURES` /
`DATASET_FEATURES`. Dropping a file into the scanned directory is all it takes
to register a new Loupe feature, model loader, dataset loader, or analysis tab.

For years that directory was a single flat `modules/`, so four unrelated plugin
*types* sat side by side with no structure:

- 14 Loupe Qt feature plugins (`loupe*.py`)
- 5 concrete model-loader plugins (`MACE.py`, `Nequip.py`, `SchNet.py`,
  `SpookyNet.py`, `sGDML.py`) — each subclasses a base in `modelLoaders/`
- 1 concrete dataset-loader plugin (`aseDataset.py`) — subclasses a base in
  `datasetLoaders/`
- 1 analysis-tab plugin (`configTabs.py`)

This was read as a "grab-bag" and made the Loupe especially hard to locate: its
shell lives in `UI/`, its render core in `ffast/visualization/` +
`ffast/renderers/`, and its 14 features were scattered in flat `modules/`.

Two alternatives were rejected:

- **Move loaders out to `modelLoaders/` / `datasetLoaders/`.** Those directories
  hold the *abstract base classes* (`ModelLoaderACE`, etc.), not plugins. Moving
  the concrete plugins there would mix framework with plugins and break the
  base/plugin separation that already exists.
- **Migrate everything into the `ffast/` package.** Too large a change for the
  benefit; the flat top-level app dirs (`UI/`, `client/`, `cluster/`, loaders)
  are out of scope here.

## Decision

- **`modules/` is the plugin-discovery directory, now organised into typed
  sub-packages** rather than a flat dir:
  - `modules/loupe/` — the 14 Loupe Qt feature plugins
  - `modules/loaders/` — the concrete model- and dataset-loader plugins
  - `modules/tabs/` — analysis-tab plugins (`configTabs.py`)
- **Discovery recurses.** `loadModules` globs `modules/**/*.py`
  (`recursive=True`). Plugin identity remains the file *basename* (loaded as the
  synthetic module `module_<basename>`); basenames are unique across the tree.
- **Base classes stay where they are.** `modelLoaders/` and `datasetLoaders/`
  remain the home of the abstract loader bases; `modules/loaders/*` are their
  concrete, auto-discovered subclasses.
- **`modules/` is a PEP 420 namespace package** (no `__init__.py`); the new
  sub-packages need none either. The handful of *direct* imports
  (`from modules.aseDataset import …`, `from modules.loupeAtoms import …`) were
  rewritten to the sub-package paths (`modules.loaders.aseDataset`,
  `modules.loupe.loupeAtoms`).

## Consequences

- The Loupe now has a clear feature home (`modules/loupe/`), and plugin type is
  legible from the path.
- New plugins go in the matching sub-directory; the recursive glob picks them up
  with no loader change.
- The discovery contract (`DEPENDENCIES` / `loadData` / `CLIENT_FEATURES` /
  `DATASET_FEATURES`, Kahn ordering) is unchanged.
- The `ffast/` visualization core + renderers are untouched: the Loupe's render
  layer is *correctly* shared with the web renderer and is not part of this move.
- Anything that loads a plugin file *by path* (e.g. tests) must use the
  sub-package path; the flat `modules/<file>.py` paths no longer exist.
