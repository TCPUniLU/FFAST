"""Re-export shim — dataset loader base + subset views now live in
ffast.loaders.dataset (ADR 0047 Phase 5c).

Desktop-Client call sites (UI, cluster proxy, ASE plugin) reach these here;
deleted in Phase 6.
"""
from ffast.loaders.dataset import (  # noqa: F401
    AtomFilteredDataset,
    DatasetLoader,
    FrozenSubDataset,
    SubDataset,
    VariableDatasetLoader,
)
