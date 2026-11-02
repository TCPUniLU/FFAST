# Metric requests run beside other messages

Status: Accepted 2026-11-02. Revisits the first alternative of ADR 0073,
which asked for a cluster log first; this ADR is that log.

## The problem

MeluXina, 2026-11-02, `shard_03` (1,773,605 structures, ~100 atoms each),
one prediction. From the page's timing log (ADR 0072) and the server log:

- "Colour atoms by force error" took 57 s on the server. Opening
  "Basic Errors" then sent 14 metric requests; force metrics took 45–70 s
  each.
- Slider moves and metric-box requests sent meanwhile waited 38–46 s each
  before the server even read them. None of them uses the metric worker.
- Once 16 messages waited unread, websockets stopped reading the socket,
  never saw the page's keepalive pongs, and dropped the page. Fixed
  separately (43adb7f): messages now wait in the server's own queue.

So the wait was work that does not use the worker, stuck behind work that
does: the case ADR 0073 said to revisit.

## The decision

1. **`REQUEST_METRIC` is handled in the background** (`server.py`,
   `handle_loop`). It starts when its turn in the queue comes, so requests
   still start in the order sent, but the next message does not wait for it.
   Its reply carries its own request id (ADR 0061), so finishing out of order
   is fine.
2. **Every other message keeps strict order**: view commands, loads,
   subsets, tab changes. Their effects build on the message before.
3. **The same metric asked for twice while it computes is computed once**
   (`_compute_once` in `ffast/session/server_session.py`, by cache key, over
   every connection). Two panels or two windows share the result.
4. **When a page leaves, its background requests stop waiting.** A metric
   already computing finishes in its thread and lands in the cache, so a
   reconnect gets it at once.

## Consequences

- Metrics still run one at a time on the one worker; this only stops
  other messages waiting for them. Their speed is a separate fix (e42e75b:
  large results come back through shared memory, 4.0 s → 0.43 s for a
  425 MB force difference).
- A subset from a plot selection (`DECLARE_SUBSET`) computes its metrics in
  the same thread pool, so it can still wait for metrics queued there.
- A second request that shares a failed computation gets "failed" without
  the reason; the first request takes the reason (ADR 0066).

## Alternatives

- **Every read-only message in the background** (`CLIENT_TO_SERVER` minus
  `MUTATING_CLIENT_EVENTS`). The others are fast; only metrics were measured
  slow, and each one moved out of order is one more ordering to reason about.
- **Keep strict order.** Clicks stay stuck behind minute-long metrics on
  large datasets.
