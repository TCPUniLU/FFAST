# ADR 0004: Dedicated Prediction-Only Array Channel for Remote Prediction Loading

**Status:** Accepted / Implemented  
**Date:** 2026-05-27

## Context

When a user loads a Remote Prediction (a cluster-side pre-computed E/F file attached to an already-loaded remote dataset), the client needs to receive the prediction arrays. Two approaches exist:

**A — Cache-invalidate + re-fetch:** Delete `session._array_cache[dataset_fp]`, call the existing `taskFetchRemoteDataset` again. The server re-sends geometry (R, z), forces (F), and predictions together.

**B — Prediction-only channel:** New `REQUEST_PREDICTION_ARRAYS(dataset_fp, model_fp)` → `PREDICTION_ARRAYS` response carrying only `energy` / `forces` for that model+dataset pair. Geometry arrays already on the client are not re-sent.

The `SUBDATASET_ARRAYS` response for a large dataset (e.g. 10 k geometries x 100 atoms) can exceed 200 MB. Prediction arrays for the same dataset are typically < 10 MB (energies: 8 bytes x N; forces: 24 bytes x N x natoms).

## Decision

Option B — a dedicated `REQUEST_PREDICTION_ARRAYS` / `PREDICTION_ARRAYS` RPC pair.

## Rationale

- Re-sending geometry purely to piggyback prediction arrays wastes bandwidth proportional to dataset size; the penalty grows with the datasets most likely to live on a cluster
- A prediction-only channel makes the two concerns explicit: geometry transfer (one-time, expensive) vs. prediction transfer (on-demand, cheap)
- The listener pattern already used for `SUBDATASET_ARRAYS` (pending-Future resolved by the listener loop) extends cleanly — same structure, new event name
- Option A would also require either re-populating the `CachedRemoteDataset` with data it already has, or special-casing the populate path — added complexity for no gain

## Consequences

- Two new RPC events added: `REQUEST_PREDICTION_ARRAYS` (client→server) and `PREDICTION_ARRAYS` (server→client)
- `RemoteSession` gains a `_pending_prediction_requests` dict (same shape as `_pending_array_requests`) and a `request_prediction_arrays(dataset_fp, model_fp)` coroutine
- `_onRemoteModelMeta` branches: proxy dataset → existing `taskFetchRemoteDataset`; populated dataset → new `taskFetchPredictionArrays(ds_fp, model_fp)`
- Geometry arrays and prediction arrays are now fetched via separate channels; a future optimisation could pipeline them but the current design keeps them cleanly separate
