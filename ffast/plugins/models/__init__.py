"""Bundled model-backend plugins — each module declares ``loadData(env)``
and is discovered by ``ffast.core.plugin_discovery`` (ADR 0048).

Each backend (MACE, NequIP, SchNet, SpookyNet, sGDML) pulls its own heavy
dependency, gated behind a ``pip install ffast[<extra>]`` extra. The concrete
backend import happens lazily inside the loader's ``__init__``/``predict``, so
a module here always imports cleanly and registers its loader class
regardless of whether the extra is installed — a missing backend surfaces
only when a user actually loads a model of that type. (The discovery
registrar separately skips, with a warning, any plugin whose own top-level
import fails outright — a different, more general safety net than these
backends happen to need.)
"""
