# ADR 0033: Complete the typed Control messages and centralize event names

**Status:** Accepted / Implemented (2026-07-05)

ADR 0006 decided that Control messages on the **RPC Channel** are typed against the **Protocol
Schema** (Pydantic models, msgpack on the wire). In practice the implementation stopped early: only
~6 of ~23 Control message kinds have models (`ffast/protocol/messages.py` — `DatasetMeta`,
`DatasetKeysResponse`, `MetricResultMessage`, the catalog messages). The rest travel as raw dicts:
**Server Session** handlers unpack 5–20 hand-written kwargs and validate nothing, and event names
exist as ~19 scattered string literals across `cluster/connection.py`, `client/connection_manager.py`,
`UI/mainMenu.py`, and the server. No test can assert that the client's send sites and the server's
handler table agree, because there is no single source of truth to agree on.

**Decision:** finish what ADR 0006 started. `ffast/protocol/control.py` is the one event-name
constants module (client→server Control messages, their server→client replies, the handshake, and
the Array message names) — every send/handle site imports from it instead of writing a string
literal. Fifteen of the client→server Control messages that had no payload model gained one in
`ffast/protocol/messages.py` (`LoadDatasetRequest`, `OpenViewRequest`, `RequestMetricRequest`, …);
`VIEW_COMMAND` and `HELLO` were already typed elsewhere (`ffast/visualization/commands.py`,
`ffast/visualization/protocol.py`) and needed only the constant. `ServerSession.dispatch` validates
the resolved payload against the route's model as a **gate** — a malformed message is dropped with
the event named in the log — but still calls the handler with the original resolved dict, never the
validated one, so presence-sensitive fields (e.g. `OpenViewRequest.prediction_ref`, where absence
and explicit `null` mean different things) keep their exact pre-validation behavior.
`tests/ffast/test_control_events.py` asserts `ServerSession._handlers` keys == `control.CLIENT_TO_SERVER`
and `cluster.connection._REPLY_CHANNELS` keys == `control.REPLY_EVENTS`. **Array messages**
(`SUBDATASET_ARRAYS`, `PREDICTION_ARRAYS`) stay untyped by design, per the existing RPC Channel
classification — only their event names moved into `control.py`.

**Deferred, on purpose:** the nine server→client **Broadcast Events** (`TASK_*`, `*_LOADED`,
`*_DELETED`, `DATA_UPDATED`) stay catalogued-not-typed in `ffast/protocol/notifications.py` — they
carry an identifier, not a structured payload, and `TASK_PROGRESS` alone has ~30 emitters with
varying keyword fields, so there is no single producer shape to type. This was already the case
before this ADR (see the legacy-thinning plan, Slice 3 -- an internal working document, not published in this repo) and is unchanged by it.

## Why

- The Protocol Schema is the deepest available seam in the system — every client/server interaction
  crosses it — and today it types a quarter of the traffic.
- Scattered string literals mean adding or renaming an event is an N-file grep with no failing test
  when a site is missed.
- Handler bodies shrink to validated-model → environment-call delegates, which also shrinks the
  Server Session (see ADR 0032's symmetric client side).

## Consequences

- Mechanical but wide: every send site and handler is touched once. Best done event-by-event, not as
  one big-bang commit; the constants module can land first and is independently valuable.
- Does not change the wire encoding (msgpack) or message shapes — only where validation happens and
  where names live.
