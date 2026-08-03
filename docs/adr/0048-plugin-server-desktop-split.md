Status: Accepted — 2026-07-25 (all decisions settled; ready to implement)

# Split the plugin directory along the server/desktop boundary

## Context

ADR 0047 relocated the engine into `ffast/` and left one thread open: **headless
plugin discovery**. `utils.loadModules` finds every plugin by globbing
`modules/**/*.py` (relative to `utils.py`) and calling each file's `loadData(env)`
/ `CLIENT_FEATURES` / `DATASET_FEATURES` hooks. `modules/` is a Desktop-Client
dir a slim `pip install ffast` would not ship — so a headless server globs, finds
nothing, and cannot register its ASE loader. One shim (`modules/loaders/
aseDataset.py`) survives only to keep that registration alive on the desktop.

The deeper problem is that **`modules/` mixes both regimes**:

| subdir | declares | regime |
|---|---|---|
| `loaders/` (ASE, MACE, NequIP, SchNet, SpookyNet, sGDML) | `loadData` only, no Qt | **Server** — loading data + running model inference (ADR 0030) |
| `loupe/` (14 panes) | `CLIENT_FEATURES` + PySide6 | **Desktop** |
| `tabs/configTabs` | `loadData` + `DATASET_FEATURES` + Qt | **mixed** |

Two files are mixed *inside themselves*: `loupe/loupeForceError.py` registers a
server-side metric (`loadData`) as well as its Qt pane; `tabs/configTabs.py`
registers server-side tab/metric config (`loadData`) alongside Qt tab building.

So the loaders and ML models are **server functionality that happens to live in a
Desktop-Client dir**. That is the mixing to remove — the same Headless-Core /
Desktop-Client line ADR 0047 drew everywhere else.

## Decision

Split plugins by regime, and make **server plugins part of the server package** so
a headless install ships and self-registers them.

- **Server plugins → `ffast/plugins/{loaders,models}/`.** Dataset loaders in
  `ffast/plugins/loaders/`, model backends in `ffast/plugins/models/`. Ships with
  `pip install ffast`; discovered and registered without `modules/`. Drop-a-file
  ergonomics preserved: a new server plugin is still a file with a `loadData(env)`
  hook, now on the server side. (`ffast/loaders/` keeps the base classes +
  relocated shared loader code; `ffast/plugins/loaders/` holds the discoverable
  concrete plugins — including the ASE loader's `loadData`.)
- **ML backends are optional install extras.** The `models/` plugins (MACE,
  NequIP, SchNet, SpookyNet, sGDML) are server plugins but pull heavy deps
  (torch/mace/…), so each is gated behind an extra (`ffast[mace]`, `ffast[nequip]`,
  …). Their heavy imports are already lazy inside `predict`, so a plugin whose
  backend isn't installed is skipped gracefully (same headless-skip branch),
  keeping a base headless server slim.
- **Desktop plugins → stay in `modules/`** (Loupe panes, tab UI). Desktop-Client,
  not shipped headless, Qt-dependent; discovered by the existing glob with its
  headless-skip-on-import-fail branch.
- **The two "mixed" files stay in `modules/` unchanged.** Investigation showed
  their server-side `loadData` is redundant on the server: `configTabs`'
  metric compilation is already done directly by `server._main`
  (`_compile_project_metrics`) and the CLI; `loupeForceError`'s force metrics
  already self-register via `ffast/metrics/builtin/__init__`. So these files are
  desktop panes/tabs whose `loadData` is belt-and-suspenders — nothing to peel to
  the server; keep or drop the redundant hook, but the files stay Desktop-Client.
- **Duplicate names are an error, not a shadow.** Two plugins registering the same
  `datasetName` / `modelName` (across the bundled + `modules/` + entry-point roots)
  raises at registration time rather than silently last-wins. Users add a custom
  loader under a *distinct* name; there is no override-by-shadowing.
- **Third-party plugins via entry points, in scope.** Alongside the bundled folder
  and the `modules/` glob, `ffast/` also discovers plugins advertised through
  `importlib.metadata` entry points (e.g. `[project.entry-points."ffast.loaders"]`
  / `"ffast.models"`), so a separately pip-installed package (`ffast-mace`) can
  self-register with no `modules/` and no core edit.

### Discovery mechanic

The bundled `ffast/plugins/` folder is inside the installed package, so discover
it by **real dotted-name import** (`importlib.import_module("ffast.plugins.ase")`),
**not** the current `spec_from_file_location` fresh-exec. The fresh-exec trick
loads a file as a differently-named module (`module_ase`), which would create a
second copy of every class under a second identity — breaking `isinstance` and
the existing `patch("...aseDataset.aseDatasetLoader...")` tests. Dotted import
guarantees one identity and is strictly cleaner than today's glob. `modules/`
keeps its glob (its files are not imported by dotted name elsewhere).

There are three discovery roots, unified behind one registrar:
1. **Bundled** `ffast/plugins/{loaders,models}/` — dotted-name import, always
   scanned (ships headless).
2. **Entry points** — `importlib.metadata.entry_points()` for third-party pip
   packages, always available.
3. **`modules/` glob** — the existing `spec_from_file_location` scan, only when a
   Desktop-Client tree is present (desktop panes/tabs + any local drop-in plugins).

The registration contract is unchanged: `loadData(env)`, `DEPENDENCIES` ordering,
`CLIENT_FEATURES`/`DATASET_FEATURES`. Registration is the single choke point that
enforces the **duplicate-name error** across all three roots. `loadModules` moves
into `ffast/` (scanning roots 1–2 always, root 3 when a UI is present), clearing
the last `Environment → utils` boundary edge.

## Consequences

- `pip install ffast` with **no `modules/`** registers its dataset + model loaders
  and can load data / run inference headless — the concrete ADR 0026 goal.
- The last shim (`modules/loaders/aseDataset.py`) is deleted; its class importers
  (`UI/mainMenu`, a couple of tests) repoint to `ffast.plugins.ase`.
- Adding a server plugin (incl. a new ML backend) is drop-a-file in `ffast/plugins/`;
  adding a desktop feature is drop-a-file in `modules/`. Both ergonomics survive,
  now on the correct side of the line.
- Touches the load/registration path GUI-verified under ADR 0047, so it lands as
  its own change with full-suite + GUI re-check, not folded into that migration.

## Resolved (2026-07-25)

1. **Folder shape:** `ffast/plugins/{loaders,models}/`. ✓
2. **ML backends:** optional install extras (`ffast[mace]`, …); lazy backend
   imports mean an uninstalled backend is skipped gracefully. ✓
3. **Mixed files:** non-issue — their server `loadData` is redundant (server
   registers those metrics directly); the files stay Desktop-Client. ✓
4. **Duplicate names:** hard error at registration, no shadowing — custom plugins
   must use a distinct name. ✓
5. **Third-party plugins:** in scope now, via entry points (root #2 above). ✓

## Open (implementation detail, not blocking)

- Ordering across the three roots relative to `registry.freeze()` — bundled and
  entry-point server plugins must register before freeze on the server; confirm
  the single registrar runs at the right point in `server._main` / CLI / desktop
  bootstrap.
- Migration of `modules/loaders/` (MACE, NequIP, …) into `ffast/plugins/models/`
  + `pyproject` extras, and deletion of the `modules/loaders/aseDataset.py` shim.
