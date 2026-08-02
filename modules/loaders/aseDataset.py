"""Re-export shim — the ASE dataset loaders now live in ffast.loaders.ase
(ADR 0047 Phase 5c).

The essential ASE loader is part of the Headless Core baseline now. This shim
keeps the plugin-discovery entry point (``loadData``) so ``utils.loadModules``
still registers the loader when it globs ``modules/`` on the desktop, and
re-exports the loader classes + field helpers for Desktop-Client call sites and
tests. The optional ML-backend loaders (MACE, NequIP, ...) remain real plugins
in this dir. Deleted once headless plugin discovery is redesigned (Phase 6).
"""
from ffast.loaders.ase import (  # noqa: F401
    VariableASEDatasetLoader,
    aseDatasetLoader,
    available_field_keys,
    loadData,
    read_atom_field,
    read_frame_field,
)
