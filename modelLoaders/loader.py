"""Re-export shim — model loader base now lives in ffast.loaders.model (ADR 0047 Phase 5b).

The ML-backend plugin loaders under modules/loaders/ (MACE, NequIP, ...) subclass
ModelLoader/ModelLoaderACE via this path; deleted in Phase 6.
"""
from ffast.loaders.model import ModelLoader, ModelLoaderACE  # noqa: F401
