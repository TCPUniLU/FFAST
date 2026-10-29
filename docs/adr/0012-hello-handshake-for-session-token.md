# ADR 0012: HELLO/HELLO_ACK handshake for session token and capability exchange

**Status:** Accepted  
**Date:** 2026-06-11

## Context

M5 adds a `SessionToken` so only the authorised client can claim the CONTROLLING role.
The token plaintext must reach the server before any state-changing commands are accepted.
Three options were evaluated: URL query parameter, WebSocket HTTP-upgrade header, and a
first binary message.

## Decision

After the existing `ping`/`pong` liveness exchange, the client sends a binary msgpack
`HELLO` message containing `ClientCapabilities` (protocol version, renderer type,
supported codecs, feature list, `session_token`). The server replies with a binary
`HELLO_ACK` containing `ServerCapabilities` (negotiated codecs, enabled features,
and the assigned `ClientRole`). State replay follows.

## Rationale

`ClientCapabilities` and `ServerCapabilities` already exist in
`ffast/visualization/protocol.py` and were designed for this exchange — using them here
costs nothing and removes a design gap.

URL parameter was rejected: the token appears in process-level WebSocket logs and browser
history, and it bypasses the existing capability-negotiation models entirely.

HTTP upgrade headers were rejected: the `websockets` library supports them, but they are
invisible to the application-layer protocol and would require out-of-band documentation.
The HELLO message is self-describing and visible in the same message trace as all other
protocol traffic.

Backward compatibility: clients that do not send HELLO (existing remote-mode Qt client
before it is updated) are assigned `READ_ONLY` automatically and continue to function as
observers.

## Consequences

- `ClientCapabilities` gains `session_token: str | None = None`
- `ServerCapabilities` gains `role: ClientRole`
- `server.py _handler()` waits for a HELLO message with a short timeout after pong;
  absence of HELLO within the timeout assigns READ_ONLY and proceeds
- Both local (`connect_direct`) and remote (`connect_to_cluster`) callers must send HELLO
