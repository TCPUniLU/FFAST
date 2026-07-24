"""Ratcheting guard on ffast/ → Desktop-Client import edges (ADR 0047).

The keystone (ADR 0047) relocates the Environment graph out of the flat
`client/` dirs into `ffast/core/` so the Headless Core imports **no** flat
Desktop-Client module. This is the enforceable invariant.

A runtime `import server` snapshot does NOT capture it: the server imports
`client.environment` lazily inside `_main`, and the phased migration leaves
re-export shims behind, so sys.modules hides the real dependency until Phase 6.
Instead we check it **statically**: scan every module under `ffast/` and collect
each import whose top-level package is a first-party *flat* (non-`ffast`) name.
That edge set must equal `ALLOWED_EDGES` — an explicit, documented allowlist that
shrinks to empty as the keystone completes.

How to use across phases: when a phase removes a flat dependency (by relocating
the target into `ffast/` and repointing the import), delete that edge here; when
a phase relocates a module *into* `ffast/` that still depends on a not-yet-moved
flat module, add the transient edge here with a note. The end state (post
Phase 5/6) is an empty set.
"""
import ast
import os

_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_FFAST = os.path.join(_ROOT, "ffast")

# First-party TOP-LEVEL names that are NOT the Headless Core. An import under
# ffast/ whose root is one of these is a Desktop-Client dependency = a leak.
_FLAT_ROOTS = {
    "client", "cluster", "datasetLoaders", "modelLoaders", "modules", "UI",
    "theme", "events", "main", "server", "tasks", "utils",
    # NB: flat top-level `config/` (config.userConfig) — distinct from the
    # `ffast.config` subpackage, which is core and allowed.
    "config",
}

# The ONLY ffast/ → flat edges permitted today. Each is scheduled for removal:
#   - client.environment          → gone at Phase 4 (Environment moves to ffast/core)
#   - client.dataType             → gone at Phase 5 (AtomsList into the dataset-IO port)
#   - modules.loaders.aseDataset  → gone at Phase 5 (loader registers into the port)
# Target: this set is empty when ADR 0047 completes.
ALLOWED_EDGES = {
    ("ffast/cli/main.py", "client.environment"),
    ("ffast/session/server_session.py", "client.dataType"),
    ("ffast/session/server_session.py", "modules.loaders.aseDataset"),
}


def _flat_import_edges():
    edges = set()
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
            for node in ast.walk(tree):
                targets = []
                if isinstance(node, ast.Import):
                    targets = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    targets = [node.module]
                for t in targets:
                    if t.split(".")[0] in _FLAT_ROOTS:
                        edges.add((rel, t))
    return edges


def test_ffast_imports_no_unexpected_flat_module():
    edges = _flat_import_edges()
    unexpected = edges - ALLOWED_EDGES
    stale = ALLOWED_EDGES - edges
    assert not unexpected, (
        "New ffast/ -> Desktop-Client import edge(s) — a Headless Core leak "
        f"(ADR 0047): {sorted(unexpected)}"
    )
    assert not stale, (
        "ALLOWED_EDGES lists ffast/ -> flat edges that no longer exist; a phase "
        f"resolved them — delete from the allowlist: {sorted(stale)}"
    )
