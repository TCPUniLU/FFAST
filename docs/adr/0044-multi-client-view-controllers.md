Status: Accepted — implemented (all 4 phases done 2026-07-22)

# Multi-client independent view controllers

`ffast-server` is single-client today. Three facts in the transport enforce it,
traced 2026-07-15:

- **One shared outbound queue.** `_main` creates a single
  `outbound = asyncio.Queue(maxsize=200)` (`server.py:408`) and every
  connection's `send_loop` drains that *same* queue (`server.py:221`). Two
  connections' send-loops compete for each item, so a second client **splits the
  message stream** — neither renders correctly.
- **One shared `ServerSession`.** `_serve` builds a single `ServerSession(env,
  outbound)` (`server.py:273`) that owns *all* open views (`self.views`,
  `server_session.py:127`). View ids are a single global namespace.
- **One CONTROLLING client.** `registry.claim` grants CONTROLLING only to the
  first client and never hands it over (`registry.py` `claim`: `token_ok and not
  has_controlling`); the receive loop dispatches **only** the controlling
  client's messages (`server.py:208`), dropping everyone else's.

ADR 0043's browser pop-out worked *around* this with a BroadcastChannel
**satellite**: the popped tab holds no socket and mirrors the main tab's single
connection. That gives a second *view* but not a second *controller* — the
satellite cannot select its own dataset/prediction or drive a view the main tab
isn't already showing. A true second controller — its own object selection, its
own view (frame, camera, colouring) over its own connection — needs the server
to stop being single-client.

## Decision

**Separate shared data from per-connection view state.** The **Environment**
(datasets, models, cache, metrics) stays one shared instance per server process;
each WebSocket connection gets its own **client session** with its own outbound
queue and its own views. Every connection controls *its own* views. Environment
mutations (load/delete a dataset or model) are shared operations any controller
may invoke, and their results broadcast to all connections. This is a
shared-workspace model — two windows onto one running server — not multi-tenant
isolation.

Concretely:

1. **Per-connection outbound queue + client session.** Move `outbound` and the
   `ServerSession` from server-process scope (`_serve`, one instance) to
   connection scope (`_handler`, one per socket). Each session owns
   `self.outbound` and `self.views`. Replies that already flow through
   `self._emit` — scene snapshots/patches, `COMMAND_RESULT`, `METRIC_RESULT`,
   `DIR_LISTING`, probe responses — become **unicast** to the requesting
   connection for free, because the queue is now that connection's.

2. **A `ConnectionHub` for broadcast.** Object-level, shared-state events
   (`REMOTE_DATASET_META`, `REMOTE_MODEL_META`, `DELETE_OBJECT`,
   `METRIC_CATALOG`, `METRICS_UPDATED`) must reach **all** connections. The env
   event bus has no `eventUnsubscribe` (`events.py` exposes only
   `eventSubscribe`/`eventPush`), so a per-connection subscribe would leak and
   emit into closed queues. Instead keep the **single** global
   `env.eventSubscribe` wiring from `_main`, but point each sender at a
   `ConnectionHub` that packs once and fans the bytes out to every registered
   connection's queue. Connections register on connect and deregister on close;
   a full queue drops for that one slow client, never for the others.

3. **View ids are per-connection.** `self.views` lives on the per-connection
   session, so the browser's hardcoded `"view-0"` no longer collides across
   tabs — each connection has an independent view namespace. No global view
   registry.

4. **Role model: control is per-view, not global.** Replace the single-
   CONTROLLING gate with: every admitted connection may drive its own views and
   may perform shared Environment mutations. Dispatch stays serialized on the
   single asyncio loop, so concurrent loads/deletes are ordered, not racy,
   between `await` points. `READ_ONLY` is **retained as an explicit opt-in**
   viewer mode (inbound control dropped) for the ADR 0043 mirror case, but is no
   longer the fallback for the second client. Where `--token-hash` is set, the
   token gates **admission / permission to mutate shared data** ("may control"),
   not "is the one controller".

5. **Recovery window keys off the LAST client.** ADR 0024's window (keep a
   cluster job alive when the controlling client blips) now arms when the
   *last* connection drops without a graceful disconnect, not the first. The
   `has_controlling` check in `_recovery_window_task` becomes a
   client-count / `is_empty` check on the hub.

6. **Reconnect (ADR 0012) re-admits, it doesn't reclaim.** The token still
   authenticates a returning client, but there is no single role to reclaim; it
   re-admits the client as a controller and `ServerSession.replay()` pushes
   shared state to that connection's queue — already per-connection behaviour.

### Web client follows for free

Once the server is multi-client, the pop-out stops needing the BroadcastChannel
relay: the popped tab connects as its **own** client (loupe-only layout), gets
state-replay of the shared datasets, selects one, opens *its own* `view-0`, and
controls frame/camera/prediction independently. The satellite path (ADR 0043)
can be retired, or kept as an offline "mirror" mode for a server that is still
single-client — the two are distinguishable by whether the pop-out opens a
socket. Recommended: pop-out opens a live controller when the server advertises
multi-client in `HELLO_ACK`, else falls back to the satellite mirror.

## Why

- **The role system already anticipated this.** `ClientRole` and the registry
  exist; only the transport (one queue) and session ownership (one views dict)
  stayed single-client. This change finishes the intent rather than inventing a
  new model.
- **Shared cache is a feature, not a cost.** Two controllers viewing the same
  dataset share the fingerprint-keyed cache — a metric computed for one is
  instantly live for the other; no duplication (ADR on Cache Key).
- **It matches the desktop mental model.** Qt opens multiple Loupe windows over
  one in-process Environment (`UIHandler.newLoupe`). Per-connection views over a
  shared Environment is the same shape across the socket.

## Consequences

- **Desktop + cluster reconnect must be re-verified.** The desktop is one
  connection with its views (unaffected in the common case), but the recovery-
  window trigger (last-client) and the token/reconnect meaning (re-admit, not
  reclaim) change ADR 0024/0012 behaviour on the cluster path — the one place
  this must be exercised end-to-end before merge, since a regression there
  kills or orphans SLURM jobs.
- **Concurrent Environment mutation.** Two controllers loading/deleting at once
  are serialized by the event loop between `await`s; a load that awaits
  mid-mutation could interleave with another. `_on_load_dataset` /
  `_on_load_model` need an audit (or a per-Environment mutation lock) before
  concurrent control is trusted.
- **Deletion races.** One controller deletes an object another is viewing. The
  broadcast `DELETE_OBJECT` must make the other view degrade gracefully (clear
  the view / drop to element colours), never crash mid-scene-build.
- **Not isolation.** Every connection sees every loaded object. Two *users*
  would share a workspace. Per-connection Environments (true multi-tenant
  isolation) are explicitly **out of scope** and a larger, separate change.
- **Backpressure is now per-client.** A slow client fills only its own queue;
  the hub drops that client's broadcast slot with a warning instead of stalling
  or starving the others (the current single queue couples everyone's fate).

## Considered alternatives

- **Keep BroadcastChannel satellite only (ADR 0043).** Rejected for this need:
  it is a mirror, structurally incapable of independent object/view selection.
  Kept as the offline/older-server fallback, not the answer to "independent
  controller".
- **SharedWorker multiplexing one socket across tabs.** Still one connection →
  one controller server-side; every tab shares one view state. Solves "N tabs,
  one control surface", not "N independent controllers". Wrong layer.
- **Per-connection Environments (full isolation).** Rejected as scope and as
  waste: it duplicates datasets/cache per connection, defeats the shared-cache
  win, and no current use case needs tenant isolation. Revisit only if a
  multi-user hosted mode appears.
- **Multiple CONTROLLING clients over the *shared* single view.** Rejected: two
  controllers fighting over one global view id (whose frame wins?) is a race by
  construction. Per-connection views remove the contention instead of arbitrating
  it.

## Implementation phases

1. **Transport split (no behaviour change for one client). — DONE 2026-07-15.**
   Per-connection `outbound` + per-connection `ServerSession`; new
   `ConnectionHub` (`ffast/session/hub.py`); the `_main` env-event senders now
   `hub.broadcast(...)` (broadcast) while `ServerSession._emit` stays
   per-connection (unicast). `_serve` builds no server-scoped session; `_handler`
   creates the queue+session and registers/deregisters with the hub. Verified:
   `tests/ffast/session/test_connection_hub.py` (5 tests, socket-free seam);
   full unit suite 1005 pass; a two-client end-to-end check (both connect at
   once, each receives its own `METRIC_CATALOG` replay — the stream no longer
   splits). Role/recovery logic deliberately untouched — a second client is
   still `READ_ONLY`, but multi-connect is now *safe* (no stream corruption).
2. **Role model. — DONE 2026-07-22.** `ConnectionRegistry.claim` grants
   CONTROLLING to every valid-token connection (no longer gated on
   `not has_controlling`); `READ_ONLY` is an explicit opt-in via a new
   `read_only` HELLO field, independent of the token. The single dispatch
   gate is replaced by `server._may_dispatch`: CONTROLLING dispatches
   everything, READ_ONLY still dispatches read/query events (its own
   `OPEN_VIEW`, `REQUEST_METRIC`, ...) so it gets scenes/results, but the new
   `control.MUTATING_CLIENT_EVENTS` set (`LOAD_*`, `DELETE_OBJECT`,
   `VIEW_COMMAND`, `SAVE_SESSION`, `EXPORT_SUBSET`, ...) is dropped for it.
   The recovery window keys off `hub.is_empty` (last connection, checked at
   both arm-time in `_handler` and expiry-time in `_recovery_window_task`)
   instead of `registry.has_controlling`. Verified:
   `tests/ffast/test_multi_client_role_model.py`,
   `tests/ffast/test_session_registry.py` (updated).
3. **Concurrency hardening. — DONE 2026-07-22.** `Environment.mutation_lock`
   (a plain `threading.Lock`, since loads run via `asyncio.to_thread`)
   serializes the registry-mutating tail of `loadModel`/`loadDataset`/
   `loadPrepredictedDataset`/`deleteObject` — the file-I/O-heavy parts stay
   unlocked. `_on_view_command`/`_on_open_view` catch an unexpected scene-
   rebuild exception (e.g. a colour-by metric erroring on a just-deleted
   model — `build_scene`'s existing `ds is None` fallback doesn't cover
   every path) and degrade to a bare/empty scene rather than dropping the
   reply entirely. Verified: `tests/ffast/test_environment_concurrency.py`,
   delete-race tests in `tests/ffast/session/test_server_session.py`.
4. **Web client. — DONE 2026-07-22.** `negotiate()` always advertises a
   `multi_client` feature in `HELLO_ACK`. The pop-out (`app.js
   _openPopout`) opens its own live connection (`?mode=loupe-live`) when the
   opener's connection reports `multiClient` — auto-connecting, hiding
   chrome via the existing `body.loupe-only` CSS, and auto-selecting the
   opener's dataset/prediction from state replay via `_onDatasetMeta`/
   `_onModelMeta` — falling back to the `mode=loupe` BroadcastChannel
   satellite otherwise. Fixed a latent bug found by this: `_connect()` set
   `this._conn` *after* `await conn.connect()`, but `connect()` dispatches
   buffered replay messages (which can trigger the auto-open-view path)
   *before* its promise resolves, so `_openView()`'s `this._conn` guard
   always failed for a pop-out onto a server with an already-loaded dataset;
   `this._conn` is now set immediately after construction. Verified:
   `tests/ffast/session/test_connection_hub.py` (two-connection SET_FRAME
   isolation), `tests/ffast/renderers/web/test_web_runtime.py::test_web_popout_opens_independent_live_controller`
   (real two-tab Playwright run — independent frames, both rendering).

## Tests

- A server test with **two** in-process client queues: each opens its own view
  on the same shared dataset, drives `SET_FRAME` independently, and asserts each
  queue receives only its own view's scenes plus the shared object broadcasts —
  no stream splitting.
- A broadcast test: loading a dataset on connection A enqueues
  `REMOTE_DATASET_META` on **both** A and B; a metric computed for A's view is
  served from cache for B without recompute.
- A delete-race test: deleting an object A is viewing degrades A's next scene
  build gracefully (no exception) and broadcasts the delete to B.
- Recovery-window test: the window arms on last-client disconnect, not first.
- Two-tab Playwright test: pop-out opens an independent controller that scrubs
  to a different frame than the main tab, both rendering.

## Refs

ADR 0010 (server-owned visualization state), ADR 0012 (HELLO handshake, token
reconnect), ADR 0024 (cluster reconnect lifecycle, recovery window), ADR 0032
(inbound event router), ADR 0033 (typed control messages), ADR 0043 (browser
MVP + BroadcastChannel satellite this supersedes for independent control). Code:
`server.py` (`_serve`, `_handler`, `_main` env-event senders, `outbound`,
`_recovery_window_task`), `ffast/session/server_session.py` (`ServerSession`,
`self.views`, `_emit`/`_emit_or_drop`, `replay`), `ffast/session/registry.py`
(`ConnectionRegistry.claim`), `events.py` (`eventSubscribe`/`eventPush` — no
unsubscribe), `ffast/renderers/web/static/ffast-viewer.js` (pop-out /
`LoupeSatelliteApp`).
