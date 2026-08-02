"""FFAST Headless Core — built-in dataset/model loaders (ADR 0047 Phase 5).

The loader BASE classes (ModelLoader, DatasetLoader + SubDataset views) and the
essential built-in loaders (ghost, zero, ASE) live here so the server can load
data without the flat Desktop-Client dirs. Optional ML-backend loaders (MACE,
NequIP, ...) remain additive plugins under modules/loaders/ and subclass these.
"""
