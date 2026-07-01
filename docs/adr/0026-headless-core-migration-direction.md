# ADR 0026: Headless Core — Installable Server Without the Client

**Status:** Accepted  
**Date:** 2026-06-30 (revised 2026-07-01)

## Context

FFAST has two organisational regimes: the namespaced **`ffast/`** package
(`cache`, `cli`, `config`, `metrics`, `protocol`, `renderers`, `session`,
`visualization`) and the flat top-level directories (`UI/`, `client/`,
`cluster/`, `modules/`, loader bases) plus a small root spine (`events.py`,
`tasks.py`, `utils.py`, `main.py`, `server.py`).

The concrete goal that gives this boundary its purpose: **`pip install` should
be able to produce a runnable `ffast-server` (and `ffast-cli`) with no Qt/GUI
dependencies and no desktop-client code** — for deployment on a SLURM compute
node or in a container.

Two facts frame the decision:

1. **The server's import closure is already Qt-free (measured 2026-07-01).**
   Importing `server` + `ffast.cli.main` pulls in **zero** GUI modules
   (`PySide6`/`PyQt*`/`shiboken6`/`qasync`/`vispy`/`pyqtgraph`). At the code
   level a headless server already works; nothing on its runtime path imports Qt.
2. **Packaging, not code, is the blocker.** `pyproject.toml` lists
   `pyside6`/`pyqtgraph`/`vispy`/`qasync` in the *mandatory* `dependencies`, so
   `pip install ffast` drags in Qt the server never uses; and `packages.find`
   installs `UI/` regardless.

Flattening `ffast/` out to the repo root was considered and rejected (name
collisions: root `events.py` ↔ `ffast/protocol/events.py`, root `config/` ↔
`ffast/config/`, `cluster/session.py` ↔ `ffast/session/`, 4× `models.py`; and it
would break `import ffast` + entry points). Direction is *into* `ffast/`.

## Decision

- **Name the boundary.** `ffast/` is the **Headless Core** (engine, server,
  protocol, renderer-agnostic visualization, CLI — no Qt). The flat top-level
  dirs are the **Desktop Client** (Qt UI + client orchestration + cluster
  connection + plugins). See the CONTEXT glossary terms.

- **The membership criterion is the *server import closure*.** A module belongs
  in the installable headless core iff it is reachable from the `ffast-server` /
  `ffast-cli` entry points — which is, empirically, a Qt-free set. This is
  sharper than "is it science?" or "is it Qt-free?":
  - `cluster/rpc` (the msgpack **transport codec**) IS on the closure → it is
    core, even though it is transport. This corrects a tempting split where
    presentation and transport both "stay in the client": presentation
    (KDE/plot styling in `UI/panels.py`) is *not* on the closure and does stay
    out, but transport does not stay in the client once the server must install
    without it.
  - `UI/panels.py` presentation, Qt widgets, picking, camera → NOT on the
    closure → stay in the Desktop Client.

- **Step A (DONE 2026-07-01): make GUI dependencies optional.**
  Moved `pyside6`/`pyqtgraph`/`vispy`/`qasync`/`pyopengl` out of `dependencies`
  into `[project.optional-dependencies].gui`. Now `pip install ffast` yields a
  headless server + `ffast-cli`; `pip install ffast[gui]` adds the desktop app.
  This works because the closure is already Qt-clean; installed-but-unimported
  `UI/` files are harmless dead weight. The closure guard is enforced by
  `tests/ffast/test_headless_closure.py`.

- **Step B (later): a true two-distribution split.** Migrate the Qt-free
  server-closure flat modules (`cluster/rpc`, the `HeadlessEnvironment` slice of
  `client/environment.py`, `modules/loaders`, `utils.loadModules`, `tasks`,
  `events`) *into* `ffast/`, so `ffast` is a self-contained headless
  distribution and a separate `ffast-desktop` depends on it. This is the
  opportunistic migration direction, with a precise target: **everything on the
  server import closure.**

## Consequences

- `pip install ffast` → headless server/CLI; `pip install ffast[gui]` → desktop.
- The import-closure set is a **testable guard**, now live as
  `tests/ffast/test_headless_closure.py`: it imports `server` + `ffast.cli.main`
  in a fresh subprocess and asserts no GUI module entered `sys.modules`, keeping
  Qt from creeping into the core.
- The incremental *science* migration (moving compute/reduction math out of
  `modules/` into `ffast/`) is tracked separately from this ADR, which governs
  only the *packaging* boundary and the server-closure criterion. The two are
  complementary, not competing.
- Reversing (shipping Qt in the core again) is easy; committing to the split
  hardens the boundary — hence recording it.
