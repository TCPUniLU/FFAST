"""Ratcheting guard on ffast/ -> Desktop-Client import edges (ADR 0047).

The keystone (ADR 0047) relocates the Environment graph out of the flat
`client/` dirs into `ffast/core/` so the Headless Core imports **no** flat
Desktop-Client module. We check this statically (a runtime `import server`
snapshot can't: the server imports the env lazily inside `_main`, and the phased
migration leaves re-export shims behind, hiding the real dependency until
Phase 6).

We distinguish two kinds of ffast/ -> flat import edge, because they have very
different consequences for a headless `pip install ffast`:

* **EAGER** — a module-level import (incl. inside a module-level if/try/with).
  It runs at import time, so it *breaks a headless import* if the flat target
  isn't installed. These MUST go to zero as the keystone completes. Every
  remaining one is scheduled for removal at Phase 5.

* **LAZY** — an import nested inside a function/method. It runs only if that
  code path executes. All remaining ones are client-only paths (cluster
  connect-out, ASE file loading on the desktop) that never execute in the
  headless server, so cluster/ and the loaders can stay Desktop-Client. Tracked
  here so a *new* client-only dependency can't sneak in unnoticed, but they are
  allowed to be non-empty by design.

When a phase changes an edge, update the matching set below; the tests explain
what drifted.
"""
import ast
import os

_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_FFAST = os.path.join(_ROOT, "ffast")

# First-party TOP-LEVEL names that are NOT the Headless Core. An import under
# ffast/ whose root is one of these is a Desktop-Client dependency = an edge.
# NB: flat top-level `config/` (config.userConfig) is distinct from the
# `ffast.config` subpackage, which is core and allowed.
_FLAT_ROOTS = {
    "client", "cluster", "datasetLoaders", "modelLoaders", "modules", "UI",
    "theme", "events", "main", "server", "tasks", "utils", "config",
}

# EAGER (module-level) ffast/ -> flat edges — these break a headless import.
# Every one is scheduled to disappear at Phase 5 (dataType.AtomsList and the
# loaders move behind the ffast/-owned dataset-IO port). Target: EMPTY.
ALLOWED_EAGER = {
    ("ffast/core/loading_coordinator.py", "modelLoaders.ghost"),   # -> Phase 5b
}

# LAZY (function-level) ffast/ -> flat edges — client-only code paths that never
# run in the headless server; cluster/ and the loaders stay Desktop-Client by
# design (ADR 0047). Allowed to be non-empty; asserted exactly so a NEW one is
# caught. cli/main.environment resolves once Phase 6 repoints it off the shim.
ALLOWED_LAZY = {
    ("ffast/core/loading_coordinator.py", "modules.loaders.aseDataset"),
    ("ffast/session/server_session.py", "modules.loaders.aseDataset"),
    # Environment's Desktop-Client loaders + color helper (Phase 4), lazily
    # imported so ffast.core.environment stays eager-flat-free. Loaders clear at
    # Phase 5; utils (mixColors pure, loadModules deferred) at the plugin ADR.
    ("ffast/core/environment.py", "datasetLoaders.loader"),
    ("ffast/core/environment.py", "modelLoaders.zeroModel"),
    ("ffast/core/environment.py", "utils"),
    # ConnectionManager's cluster connect-out / SLURM / bootstrap machinery
    # (Phase 3). All lazy and client-only — the headless server never initiates
    # an outbound cluster connection, so cluster/ never enters its import
    # closure and stays a Desktop-Client dir (ADR 0047).
    ("ffast/core/connection_manager.py", "cluster.backend"),
    ("ffast/core/connection_manager.py", "cluster.bootstrap"),
    ("ffast/core/connection_manager.py", "cluster.connection"),
    ("ffast/core/connection_manager.py", "cluster.remote_dataset"),
    ("ffast/core/connection_manager.py", "cluster.slurm"),
}


class _EagerFlatVisitor(ast.NodeVisitor):
    """Collect flat imports, split by eager (module-level) vs lazy (in a fn)."""

    def __init__(self):
        self.fn_depth = 0
        self.eager = set()
        self.lazy = set()

    def _fn(self, node):
        self.fn_depth += 1
        self.generic_visit(node)
        self.fn_depth -= 1

    visit_FunctionDef = _fn
    visit_AsyncFunctionDef = _fn
    visit_Lambda = _fn

    def _record(self, module):
        if module and module.split(".")[0] in _FLAT_ROOTS:
            (self.lazy if self.fn_depth else self.eager).add(module)

    def visit_Import(self, node):
        for a in node.names:
            self._record(a.name)

    def visit_ImportFrom(self, node):
        if not node.level:
            self._record(node.module)


def _edges():
    eager, lazy = set(), set()
    for dirpath, dirnames, filenames in os.walk(_FFAST):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, _ROOT)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read(), path)
            except SyntaxError:
                continue
            v = _EagerFlatVisitor()
            v.visit(tree)
            eager |= {(rel, m) for m in v.eager}
            lazy |= {(rel, m) for m in v.lazy}
    return eager, lazy


def test_ffast_has_no_unexpected_eager_flat_import():
    eager, _ = _edges()
    unexpected = eager - ALLOWED_EAGER
    stale = ALLOWED_EAGER - eager
    assert not unexpected, (
        "New EAGER ffast/ -> Desktop-Client import(s) — these break a headless "
        f"install (ADR 0047): {sorted(unexpected)}"
    )
    assert not stale, (
        "ALLOWED_EAGER lists eager edges that no longer exist; a phase resolved "
        f"them — delete from the allowlist: {sorted(stale)}"
    )


def test_ffast_lazy_flat_imports_match_allowlist():
    _, lazy = _edges()
    unexpected = lazy - ALLOWED_LAZY
    stale = ALLOWED_LAZY - lazy
    assert not unexpected, (
        "New LAZY ffast/ -> Desktop-Client import(s); confirm it is a client-only "
        f"path (never runs headless) and add it to ALLOWED_LAZY: {sorted(unexpected)}"
    )
    assert not stale, (
        f"ALLOWED_LAZY lists lazy edges that no longer exist — delete: {sorted(stale)}"
    )
