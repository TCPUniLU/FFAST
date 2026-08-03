"""FFAST Headless Core — built-in dataset/model loaders (ADR 0047 Phase 5).

The loader BASE classes (ModelLoader, DatasetLoader + SubDataset views) and the
essential built-in loaders (ghost, zero, ASE) live here so the server can load
data without the flat Desktop-Client dirs. Optional ML-backend loaders (MACE,
NequIP, ...) are additive plugins under ffast/plugins/models/ (ADR 0048) that
subclass these; the discoverable ASE dataset-loader plugin lives in
ffast/plugins/loaders/ase.py.
"""
