# The server owns visualization state and renderer-neutral scenes

**Status:** Accepted / Implemented

FFAST will make `ffast-server` the source of truth for Visualization Views, scientific selections, edits, parameters, metrics, and renderer-neutral Render Scenes. Qt/Vispy and future web clients render standard primitives and send typed commands back to the server; they do not own independent scientific state. Local desktop mode will ultimately use a managed local server and the same protocol as remote mode, accepting process and protocol complexity to prevent local/remote/backend behavior from drifting.
