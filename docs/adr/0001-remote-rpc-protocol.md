# ADR 0001: WebSocket + msgpack for Remote RPC Protocol

**Status:** Accepted  
**Date:** 2026-05-20

## Context

FFAST remote mode splits the application: Environment + compute runs on a cluster node (`ffast-server`), Qt GUI runs locally (`ffast-client`). These two processes need a bidirectional communication channel over an SSH tunnel.

Candidates evaluated:
- Raw TCP + custom protocol
- WebSocket + msgpack
- gRPC + protobuf
- ZeroMQ

Key requirements:
1. Bidirectional — server must push events (TASK_PROGRESS, DATA_UPDATED) to client unprompted
2. Async-native — must integrate with qasync (asyncio + Qt event loop)
3. Efficient binary transfer — numpy arrays up to ~240MB (forces for large datasets)
4. Simple SSH tunneling — standard port-forward, no exotic transport

## Decision

WebSocket (`websockets` library) with `msgpack` + `msgpack-numpy` serialization, tunneled over SSH port-forward.

## Rationale

- WebSocket is natively bidirectional — no separate pub/sub and request/reply sockets (unlike ZeroMQ, unlike Jupyter kernel protocol)
- `websockets` is async-native and integrates cleanly with qasync
- `msgpack-numpy` handles numpy dtype/shape metadata with zero extra code
- SSH port-forward is standard on all clusters — no firewall exceptions, auth solved for free
- gRPC rejected: protobuf schema overhead not justified for internal protocol; streaming large arrays requires careful flow control
- ZeroMQ rejected: unidirectional per socket requires multiple sockets for bidirectional flow; extra dependency without clear benefit over WebSocket

## Consequences

- `ffast-server` and `ffast-client` both depend on `websockets` and `msgpack-numpy`
- All events crossing the wire must be serializable via msgpack (numpy arrays: yes; Qt objects: no — must be converted at the boundary)
- SSH tunnel must be established before WebSocket connection — connection lifecycle tied to SSH process
- Future: if intra-cluster latency becomes a bottleneck, Apache Arrow / pyarrow zero-copy transfer is a drop-in upgrade for array messages
