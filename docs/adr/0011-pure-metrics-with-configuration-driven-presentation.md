# Metrics are pure calculations with configuration-driven presentation

**Status:** Accepted / Implemented

FFAST Metrics will be deterministic registered Python calculations over declared numeric inputs, independent of Environment, Dataset, UI, and renderer objects. Stable namespaced IDs, extensible shapes and units, dependency graphs, implementation-aware cache identities, and isolated failures define the scientific contract; TOML configuration decides whether a Metric appears as atom coloring, filtering, plots, labels, tables, vectors, or export. This lets researchers add normal metrics with one Python function and configuration rather than editing calculation, Loupe, color-bar, and control code separately.
