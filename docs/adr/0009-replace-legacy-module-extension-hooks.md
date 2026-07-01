# New extension contracts replace legacy UI and Loupe module hooks

**Status:** Accepted / Implemented

FFAST does not guarantee compatibility for third-party extensions built around the current
`loadUI` and `loadLoupe` hooks. External extensions move to the new Metric, Pipeline Stage,
Visualization Configuration, and renderer contracts.

**Built-in modules** (bonds, force vectors, unit cell, view settings, camera, axes, export)
continue to use `loadLoupe(UIHandler, loupe)` until Parameter Schema auto-generated controls
are implemented. These are first-party modules that emit View Commands only; they are not
third-party extension callers. The `loadLoupe` hook remains the correct extension point for
built-in sidebar pane modules.

The two plugin architectures (legacy hooks vs. new contracts) will not be maintained
indefinitely for third-party callers.
