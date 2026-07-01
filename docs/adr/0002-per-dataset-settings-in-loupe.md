# Per-dataset settings stored in Settings, not per-module state dicts

**Status:** Accepted / Implemented

Loupe alignment settings (`kabschAlign`, `originCenterOfMass`, `alignAtoms`) need independent state per dataset — enabling Kabsch for one dataset must not affect another. We added `markAsPerDataset(key)` to the `Settings` class, with `saveForDataset` / `restoreForDataset` called in `Loupe.onDatasetSelected`. Modules declare intent with one line; Loupe owns the save/restore lifecycle.

The alternative was per-module monkey-patching of `onDatasetSelected`. Rejected because patch order matters (broken chain = silent data loss), each module would duplicate the save/restore pattern, and cross-module mutual exclusivity (Kabsch vs COM vs alignAtoms) becomes hard to reason about when restores happen in separate patches.
