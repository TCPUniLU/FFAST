# Environment decomposed into composed managers with an injected prediction source

The `Environment` god object (~2200 lines) is split into single-responsibility
collaborators wired by composition, not inheritance: `DataCache` (pure
key→entity store), `ModelRegistry`, `DatasetRegistry`, `DataService` (the
coordinator owning cache-key resolution, the generation queue, and metrics),
`RemoteSessionManager` (pure transport), and `SessionPersistence`. Crucially,
*where predictions/metrics are computed* is an injected `PredictionSource`
(`InProcessSource` vs `RemoteSource`) rather than an `if self.remoteSession`
branch — so the same code runs as either a server (computes in-process) or a
connected client (delegates to the server). This is the seam that makes the
planned "edit the system on the client, compute energy/forces on the server"
flow a wiring choice, not a rewrite.

## Status

accepted

## Considered Options

- **Mixin classes** (split methods across `*Mixin` files, one `Environment`
  inheriting all). Rejected: shrinks files but leaves coupling total and
  *invisible* — a mixin still reads `self.cache`, `self.tm`, `self.datasets`
  defined elsewhere. Fixes cognitive load only; makes coupling opacity worse.
- **Facade** (keep all `Environment.getX` methods as one-line delegations to
  managers). Rejected as the destination — keeps ~40 forwarding methods forever
  and hides the real structure. Retained only as a *throwaway* facade during
  migration so the ~250 existing call sites keep working step-by-step; deleted
  in the final commit ("Break").
- **Cache holds back-references to the registries**, and **remote holds
  back-references to the registries.** Rejected: rebuilds the dependency cycle
  in multiple files — the god object in pieces. Instead: `DataCache` is a
  dependency-free leaf, key→object resolution lives only in `DataService`, and
  remote stays a transport leaf that *shouts events* (registries listen) and is
  *read* only through the injected source. All dependency arrows point one way.

## Outcome (implemented)

`Environment` (2199 lines) is now a thin coordinator (~890 lines) that composes:
`DataCache`, `ModelRegistry`, `DatasetRegistry`, `DataService` (datatypes +
cache-key resolution + generation queue + in-process metrics), `RemoteSessionManager`,
`SessionPersistence`, and an injected `PredictionSource` (`RemoteSource`/`InProcessSource`).
The Break sweep migrated ~160 call sites to the honest API (`env.models.get`,
`env.data.getData`, `env.remote.connectDirect`, `env.persistence.save`, …) and the
facades were deleted. `Environment` retains only genuine cross-cutting work:
loading orchestration (`loadDataset`/`loadModel`/`loadPrepredictedDataset`),
object resolution spanning registries (`getObject`/`getKeyFromPath`/`deleteObject`),
task dispatch (`newTask`), the server-routing dispatchers
(`requestDatasetLoad`/`requestSessionSave`/…), and `lookForGhosts`. Test doubles
gained sub-objects via a shared `tests/ffast/_env_facets.py` helper. 661 tests
pass; headless metric + save/load round-trips verified.

## Consequences

- Call sites move from `env.getModel(fp)` to `env.models.get(fp)` etc.
  (~250 sites). The migration keeps the app runnable each step behind a
  temporary facade, removed last.
- The cache becomes a dumb store; anything that needs to turn a cache key back
  into live model/dataset objects must go through `DataService`, which is the
  single place allowed to know about cache + registries + datatypes together.
- Managers are constructed with explicit dependencies, so each file's coupling
  is readable at a glance and unit-testable in isolation — except `DataService`,
  which is the deliberately-concentrated coordinator.
