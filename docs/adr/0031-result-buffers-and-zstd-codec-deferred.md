# ADR 0031: Result Buffers and the zstd codec stay dormant

**Status:** Accepted (2026-07-02)

## Context

The **Result Buffer** service (`ffast/visualization/buffers.py`) is a complete,
content-addressed, chunked, resumable transfer store with a two-value **Buffer Codec**
(`none` | `zstd`). Capability negotiation for it exists too
(`ffast/visualization/protocol.py`: `_KNOWN_FEATURES` lists `zstd_buffers` /
`result_buffers`). None of it is on the live path:

- `_SUPPORTED_CODECS = ["raw"]` — the server advertises only `raw`; `negotiate()`
  intersects with the client and always lands on `raw`.
- No live code instantiates `BufferService` / `ResultBuffer` / `BufferTransfer`. Actual
  array transfer goes through `ffast/protocol/rpc.py` `pack_arrays` /
  `pack_prediction_arrays` (msgpack), never the buffer path.

So both the Result Buffer service *and* its zstd codec are dormant. A recurring
temptation is to "activate zstd, wire `pack()`, perf-test." Two findings retire that.

### 1. Compression is already handled, on every path

The `websockets` library enables **permessage-deflate by default**. Neither
`websockets.serve` (`server.py`) nor `websockets.connect` (`cluster/connection.py`)
passes `compression=None`, so every RPC message — local *and* remote — is
deflate-compressed at the WebSocket layer. (The `ssh -L` tunnel does *not* use `-C`;
compression comes from permessage-deflate, not SSH — correcting an earlier assumption.)
Layering zstd on top of deflate is double compression: deflate cannot shrink
already-compressed bytes, so we'd pay zstd CPU for ~zero gain. And float arrays
compress poorly under any generic codec anyway — zstd's edge over deflate is speed,
not ratio.

### 2. The architecture rarely ships bulk arrays

Verified against a live MeluXina session (2026-07-02): a remote dataset load ships only
`REMOTE_DATASET_META` (a lazy proxy — zero arrays until Loupe opens); predictions are
computed **server-side** and only Metric Results return; reconnect replays metadata plus
server-side metric regen, not bulk arrays. There is no measured bulk-transfer pain for
the Result Buffer service to solve.

## Decision

Keep the Result Buffer service and the zstd codec **dormant**. Do not add `zstd` to
`_SUPPORTED_CODECS`, do not route live transfers through `BufferService`. Compression is
the transport's job (permessage-deflate); the lazy-proxy + server-side-metric design
means the bulk-array transfer the buffer service optimizes rarely happens.

## Revisit trigger

Activate only when profiling shows a concrete problem this actually solves. If it comes,
the justification will be the buffer service's **real** value — **content-addressed
dedup + resumable chunking on the remote path** (don't re-ship an array over the WAN;
resume a big transfer after a tunnel drop) — *not* compression. And if deflate CPU ever
becomes the measured bottleneck on large transfers, the move is to **replace** it with a
dtype-aware scheme (byte-shuffle + zstd, permessage-deflate off), never to stack zstd on
deflate.

## Consequences

- The buffer + codec code stays as tested-but-unused infrastructure. A future reader sees
  zstd wired but never activated — this ADR explains why, so it isn't "fixed" by mistake.
- No perf work undertaken speculatively.
