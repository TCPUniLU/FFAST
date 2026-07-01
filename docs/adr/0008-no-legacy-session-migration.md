# The new architecture does not migrate legacy saved Sessions

**Status:** Accepted / Implemented

The server-owned visualization and metric architecture may introduce a new Session format without readers for existing saved Sessions. Preserving legacy Session compatibility would add migration logic for UI-coupled state and caches that is not required; datasets and predictions remain importable through their normal source formats.
