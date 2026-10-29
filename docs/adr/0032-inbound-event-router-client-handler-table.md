# ADR 0032: Inbound Event Router — a client-side handler table for server→client messages

**Status:** Accepted / Implemented (2026-07-05)

The client's dispatch of incoming **RPC Channel** messages is a ~98-line `_listen` closure inside
`ServerConnection` (`cluster/connection.py`). It hard-codes special cases inline: reply-channel
lookup, the `CLIENT_ENV_SAFE` filter, `REMOTE_DATASET_META` logging, **Remote Task ID** namespacing
for `TASK_*` **Broadcast Events**, phantom-task registration on `TASK_CREATED`, and a hard-coded
2-second repaint delay on `TASK_DONE`. The server's mirror image of this problem was already fixed:
the **Server Session** dispatches through a built-once event→handler table
(`ffast/session/server_session.py`). The client never got the same treatment, so the two ends of the
same wire use two different dispatch idioms, and the client side is untestable without a live socket.

**Decision:** extract an Inbound Event Router (`cluster/inbound_router.py`, `InboundEventRouter`) — a
module with a built-once table mapping event name → named handler method, mirroring the Server
Session's shape. The listener loop collapses to unpack-and-route; task namespacing, phantom-task
registration, and the repaint delay become named handlers (`test_inbound_router.py`), each
unit-testable with a fake environment and no socket. The **Connection Manager** stops reaching into
`ServerConnection._listener` (was ~6 private-attribute touches: assign / add_done_callback / cancel
across three connect paths) and instead gets a `ListenerHandle` with an explicit lifecycle
(`wait_done()` / `cancel()`).

## Why

- The dispatch logic is the largest test-dark surface on the client transport path; the table is the
  test surface, exactly as it is server-side.
- Symmetry: one dispatch idiom on both ends of the RPC Channel lowers the cost of reading the
  protocol flow (connect → handshake → replay → UI populated).
- Locality: the reconnect/replay special cases concentrate in one module instead of a closure that
  also owns socket iteration.

## Consequences

- The reply-channel table (`_REPLY_CHANNELS` + PendingRequests correlator) stays as-is — it is
  already a deliberate, tested seam (see the PendingRequests extraction). The router handles the
  *non-reply* traffic that currently falls through to the inline `if` chain.
- Pairs naturally with ADR 0033 (typed Control messages): the router table and a single
  event-constants module want to be born together, but neither blocks the other.
