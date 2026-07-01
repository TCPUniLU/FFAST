# ADR 0013: Explicit GRACEFUL_DISCONNECT event for managed-server shutdown

**Status:** Accepted  
**Date:** 2026-06-11

## Context

M5 adds a recovery window: a managed local `ffast-server` stays alive for a configurable
period after the controlling client disconnects unexpectedly, so the client can reconnect
and restore its session. The server must distinguish a deliberate client shutdown (stop
immediately) from an unexpected disconnect (start countdown).

WebSocket close codes were evaluated as the signal: a clean close frame (code 1000)
would mean graceful; anything else would mean unexpected.

## Decision

The client sends an explicit `GRACEFUL_DISCONNECT` msgpack event to the server before
closing the WebSocket. The server sets a `graceful` flag when it receives this event.
On handler exit: if `graceful`, the server stops immediately (or after saving a snapshot
if running in managed mode). If not `graceful` and the disconnecting client held
CONTROLLING, the server starts the recovery-window countdown.

## Rationale

WebSocket close codes are unreliable as a shutdown signal. A network partition, a
`kill -9`, and a clean `socket.close()` can all produce the same close code or no close
frame at all — the distinction between "user quit" and "laptop lid closed" is not
recoverable from transport signals alone.

An explicit event is unambiguous: the client either sent `GRACEFUL_DISCONNECT` or it
did not. It also composes cleanly with the HELLO handshake — both are deliberate
protocol actions with clear semantics, not inferences from transport state.

## Consequences

- `GRACEFUL_DISCONNECT` is added to the set of client→server event names
- `server.py _handler()` tracks a `graceful: bool` flag per connection
- Clients that crash or lose connectivity automatically trigger the recovery window
  without any special handling — absence of `GRACEFUL_DISCONNECT` is the trigger
- The recovery window only applies when `--recovery-window N` (N > 0) is passed to
  `ffast-server`; standalone CLI use (`python server.py`) defaults to 0 (disabled)
