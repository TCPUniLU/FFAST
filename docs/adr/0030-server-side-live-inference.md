# ADR 0030: Server-side live inference for real models

**Status:** Accepted / Implemented (flip landed 2026-07-02)

> The `onModelLoad` flip and the server guards are in place. The
> latency/correctness measurement (primary case: local managed server) and the
> predicting ModelLoaders themselves are **work in progress** — tracked as the open
> questions below, not blockers on the routing decision.

## Context

Loading a real model (not a prediction file) and generating predictions currently
runs **in-process** in the desktop client. The menu handler
(`UI/mainMenu.py` `onModelLoad`) calls `env.taskLoadModel(path, typ)` unconditionally,
even when a **Server Connection** exists.

The server path is already built end-to-end but parked:

- `client/environment.py` `requestModelLoad` routes a `LOAD_MODEL` event to the
  server when a session exists, else falls back to in-process `taskLoadModel`.
- `ffast/session/server_session.py` `_on_load_model` runs `taskLoadModel` server-side;
  `_send_prediction_arrays` materializes prediction arrays on demand for the
  server-owned metric channel (Stage 4a).
- The client consumes those arrays via `generateMetric` /
  `_fetchPredictionArraysSync`, receiving a ghost proxy through `REMOTE_MODEL_META`.

It was deprioritized as a **precaution** — a suspected latency/correctness regression
versus the in-process path was never actually measured.

Consequences of leaving it parked:
- **Remote real-model inference is unreachable from the UI.** In REMOTE mode the
  unconditional `taskLoadModel` loads a real model into the *local* client, not the
  cluster. Remote workflows are limited to prediction files (ADR 0004).
- The server-side inference + `_send_prediction_arrays` code path has no live user,
  so it silently rots.

## Decision

Real-model inference is **always server-owned**. Flip `onModelLoad` to call
`env.requestModelLoad(path, typ)`; the model loads and predicts inside the
`ffast-server` process in every mode — including local desktop, where the target is
the managed **Local Server Session** (ADR 0027) on the same machine. In-process
`taskLoadModel` survives only as a degenerate fallback for the pre-connection /
connection-down window, not as a user-selectable mode.

This mirrors how prediction files already work: a **Prediction** from a real Model
now takes the same server-side route as one loaded from a file, differing only in that
the server runs inference instead of reading arrays from disk.

Gate the flip on a **latency/correctness measurement** whose *primary* case is the
**local managed server** path (the common case after the flip), with the remote cluster
as the secondary win. In-process is the baseline being compared against, not a path
that must keep parity as a shipping mode.

## Consequences

- Local model inference moves out of the GUI process into the local server subprocess;
  arrays return over the local socket. This is the case the measurement must clear.
- Remote real-model inference becomes reachable from the UI (predictions run on the
  cluster where the model + data live), closing the current gap where a remote model
  load silently ran in the *local* client.
- The `_send_prediction_arrays` path gets a live user, closing its rot risk — while the
  in-process path becomes the new rot risk (only exercised in the connect-window gap).
- Requires the measurement gate before merge.
- The client no longer distinguishes a real Model from a file-backed one: both arrive
  as a **GhostModel** (`GhostModelLoader` from `REMOTE_MODEL_META`). The real-vs-file
  distinction moves entirely server-side. A real-model GhostModel can request fresh
  predictions (server re-runs inference); a file-backed one cannot.

## Model backends live in the server import closure

Server-owned inference puts the concrete predicting **ModelLoader** backends
(MACE, sGDML, Nequip, SchNet, SpookyNet — each pulling torch + its framework) into
the `ffast-server` runtime closure (ADR 0026: membership = server import closure). The
predicting loaders are **work in progress**; the design intent is that they run on the
headless server, local or remote.

Two hard rules and their guards:

- **ModelLoaders must never import Qt** — now and in the future — so they load in a
  headless server. Confirmed Qt-free today; base `modelLoaders/loader.py` had a dead
  top-level `import torch` (removed) so the base class and the **GhostModel** proxy
  import cleanly on a server with no ML backend installed.
- **Missing/broken backends warn, they do not crash.** Heavy imports stay lazy inside
  the concrete loaders; `Environment.loadModel` wraps instantiation + `initialise` and
  turns an `ImportError` (or any backend failure) into a clear warning and an aborted
  single load — on both the local and remote server (both run the same
  `HeadlessEnvironment.loadModel`). On-demand prediction generation is already guarded
  in `_on_request_prediction_arrays`. A backend module that fails to import at
  `loadModules` discovery time is simply not registered (skip-on-import-fail).

## Open questions

- What is the acceptance threshold for the latency/correctness measurement (primary
  case: local managed server)?
- Past the ADR 0024 recovery-window expiry: is in-flight/completed inference discarded
  with the **Server Session**, or is there a case for persisting it further?

Resolved: inference in flight when the **Server Connection** drops **continues
server-side**; results are cached and delivered to the client on reconnect via
state-replay within the ADR 0024 recovery window. A transient blip never kills an
expensive (cluster GPU) run — this is a core reason inference is server-owned rather
than in-process.

Resolved: the client `GhostModel` reuses the server's fingerprint from
`REMOTE_MODEL_META`, so the **Cache Key** model slot and **Prediction Array Key**
`model_fp` agree across the boundary by construction — no dual-fingerprint risk.
