Status: Proposed 2026-11-13. Comes before ADR 0098 (the desktop client is
removed), which deletes the desktop's own request matching only after this is
built.

# Every reply names the request it answers

## The problem

The browser sends a request and waits for the server's answer. Today it often
cannot tell which answer belongs to which request:

- **Loads.** While a load waits, *any* task that finishes ends it as
  "already loaded" (`app.js` `TASK_DONE` handler, `remote_browser.js`
  `onTaskDone`). The real dataset then arrives unmatched, and the units the
  user typed for it are not applied. Load answers are also matched by kind
  only, so a second window's load can settle the first window's.
- **Save queues.** Tab saves and user-metric saves are matched first come,
  first served (`tab_ops.js`, `metric_ops.js`). One missing answer shifts every
  later one onto the wrong request.
- **Single slots.** Run with FFAST, Score, Sub and the probes keep one "the
  request I am waiting for" field. A second click before the first answer
  overwrites it.

Metric requests, Try, Check, energy previews and 3D views already carry their
own IDs and match correctly.

The browser also never notices that its connection is gone. After the
handshake it stops listening for a close (`connection.js`), and `send()` on a
closed socket silently does nothing. The page keeps saying "Connected", clicks
do nothing, and a waiting request waits forever.

The desktop client had both: a table of waiting requests keyed by reply and
key, and a "Connection lost" message. That code goes with the desktop
(ADR 0098); its ideas move here.

## Decision

### 1. One set of rules for requests and replies

- **Every message the browser sends carries `id`**, a number unique within the
  page's connection. The envelope becomes `{event, args, kwargs, id}`. The
  timing log (ADR 0072) uses the same number instead of its own.
- **Every message that answers a request carries `reply_to: <id>`.**
  Progress messages carry it too.
- **Each request gets exactly one final reply**, marked `final: true`.
  Progress comes before it. After the final reply the request is over.
- **Errors have one shape**: the final reply carries `error: "text"` and no
  result. A reply without `error` succeeded. Today's ad-hoc forms
  (`{ok, error}`, a separate `LOAD_FAILED`, `{ok, text, error}`) move to this.
- **A message without `reply_to` answers nothing.** Announcements and
  broadcast events (dataset and model metadata, tasks, catalog, tab layout)
  stay as they are and still go to every window.
- **No time limits.** A request fails only when the connection drops (rule 3).
  A slow cluster load is not a failure.

A test reads the server's handler table and the browser's event list and checks
that every request has a final reply and every reply event carries `reply_to`.

### 2. Loads answer the window that asked

When a load starts, the server tells the asking window, in a reply to that
request, the task number it started. "Already loaded" becomes the final reply
itself, not a guess from the next finished task. A failed load is the final
reply with `error`. The dataset and model metadata announcements still go to
every window; the asking window links them to its request through the task
number.

### 3. A dropped connection is noticed and recovered

- The browser listens for the connection closing at all times. On a close it
  shows a "Disconnected" banner, disables the controls that need the server,
  and fails every waiting request with the error "connection lost".
- It reconnects by itself, waiting 1 s, 2 s, 4 s and so on, up to 30 s
  between tries. The banner has a "Reconnect now" button. After reconnecting
  the page takes the server's replay, as after a page reload.
- **A heartbeat finds a stalled socket.** Laptop sleep or a Wi-Fi switch can
  stall a socket without closing it, and the browser then waits minutes before
  it notices. Every 15 s the page sends `HEARTBEAT`; the server answers at once
  from its receive loop, not behind slow handlers. A page that hears nothing
  at all for 60 s (any message counts) treats the connection as lost. 60 s
  matches the server's own limit for a silent page, and leaves room for one
  large message on a slow tunnel. A server that does not offer the
  `heartbeat` feature gets none. This is not a time limit on requests: a
  slow load still answers heartbeats.
- On a cluster, a retry first asks the launcher whether it still holds the
  SSH tunnel. If not, the job or the tunnel is gone: the page stops retrying
  and the banner points to the Cluster dialog. The page does not ask the
  launcher while connected: a dying tunnel closes the page's socket at once,
  and a stalled one looks alive to the launcher too.

### 4. Two ideas kept from the desktop

- Two probes of the **same file with different types** do not overlap: the
  second waits for the first to end.
- An **identical request already in flight** is joined, not sent twice.

### 5. Built in steps, each one shippable

1. The `id` / `reply_to` / `final` / `error` rules in the connection layer,
   the table of waiting requests, failing them on a drop, the banner and
   reconnect, and the rules test.
2. Loads (decision 2). This removes the proven bug.
3. Tab and user-metric save queues.
4. The rest: probes, directory listing, Run with FFAST, Score, Sub, subset
   export, sessions, practice runs. Existing per-message IDs (`request_id`,
   `try_id`, `check_id`, `preview_id`) keep working and can fold into `id`
   later.

## Considered options

- **A published request/reply format, MessagePack-RPC**
  (`[0, id, method, params]` → `[1, id, error, result]`). Its rules are fixed
  and documented and it uses the MessagePack FFAST already sends. Rejected:
  every message and every test that builds raw messages changes form at once,
  and the format allows one reply per request, so the progress replies FFAST
  needs (length probe, Try, export) would need a home-made rule anyway. The
  rules above borrow its parts that matter: an ID on every request, one error
  shape, one final reply.
- **Socket.IO** for the connection, which brings acknowledgements, heartbeats
  and reconnect. Rejected: it replaces the connection layer under the server,
  the cluster tunnel path and every raw-socket test, and adds a vendored
  library, for features that are about a hundred lines here.
- **Fixing only the loose cases**, each with its own ID name. Rejected: no
  shared table of waiting requests, so a dropped connection could not fail
  them all, and each new message would invent its rules again.

## Consequences

- The new fields are additions. A handler moves to the rules on its own; an
  unmoved one keeps working as today until step 4 ends.
- The browser and the server must come from the same checkout. They already
  do: the cluster bootstrap installs the server from the local checkout.
