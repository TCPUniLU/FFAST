Status: Accepted

# Carve two modules and a name table out of the web client's FFastApp

`ffast/renderers/web/static/app.js` was 1,333 lines and 78 methods: connection
lifecycle, object rail, remote file browser, session save/load, PNG and subset
export, snapshot/patch intake, view state, picking with five tools, subsetting,
playback. ADR 0045 Phase 0 split the *files* (`ffast-viewer.js` is a 25-line
bootstrap, panes are extracted and callback-wired) but said nothing about seams
inside the remaining class.

**Decision:** extract the two clusters whose boundaries are genuinely clean, put
the wire-event names in one table, and fix a correlation bug the god-object was
hiding. Leave view state where it is.

## The bug this fixes

Session save and load reported completion by watching for the next `TASK_DONE`:

```js
conn.on('TASK_DONE',   (kw) => { console.debug('TASK_DONE', kw); this._resolveSessionOp(true); });
conn.on('TASK_FAILED', (kw) => { console.warn('TASK_FAILED', kw); this._resolveSessionOp(false); });
```

`kw` was discarded. `_resolveSessionOp` popped a single `_pendingSessionOp` slot,
so **any** task completing while a save was in flight resolved it — load a
dataset during a save and the status line read "Saved session to …" on the
dataset's completion, while the real save's result was never reported. The
comment on the guard (`// this task wasn't a save/load (e.g. a dataset load)`)
shows the ambiguity was known and accepted.

It could not be fixed client-side. `TASK_DONE` carries `args=(taskID)`, and the
client never learns which id its request produced: the server queues the task
internally and `TaskManager.queueTask` assigns the id later, so there is no id to
correlate against, and the task's `name` is never broadcast either.

So the server now sends `SESSION_SAVED` / `SESSION_LOADED` with
`{ok, path, error}` — the same shape and the same rationale as the existing
`SUBSET_EXPORTED`, which had already solved this problem for exports. The task
itself stays (`visual=True`, so both clients keep their Tasks-panel entry, with
the same names — the desktop panel shows those strings). The ack is emitted from
the task's worker thread via `loop.call_soon_threadsafe`, because
`asyncio.Queue` is not thread-safe.

A failure now also reaches the client with its reason. The replaced guess could
only ever report ok-or-not.

## What was extracted

**`remote_browser.js`** — the server-side file browser. The browser has no local
filesystem, so picking a file means walking the *server's* directories over
`LIST_DIR` and, for predictions, probing the chosen file's energy/force keys
before the load can be issued. That state machine (current directory, parent,
home, selection, dataset-vs-prediction mode) was five `_fb*` fields on
`FFastApp`.

**`session_ops.js`** — save session, load session, export subset, export PNG, and
the path prompt the first three share. All write server-side, which has no file
dialog reachable from a browser.

Both take explicit ports (`send`, `setStatus`, dataset lookups) rather than a
reference to the app, and each binds its own modal's controls.

**`events.js`** — every wire event name, previously ~30 bare string literals in
`app.js` plus three more in `metrics.js` and `connection.js`. Both failure modes
of a typo are silent: an outbound name the server does not know is dropped as an
unknown event (logged server-side, invisible in the browser), and an inbound
handler registered under a name the server never sends simply never fires. This
is kept separate from `protocol.js`, which describes message *payloads* as JSDoc
typedefs — those vanish at runtime, these values do not.

`app.js`: 1,333 → 1,026 lines. Zero bare wire-event strings left in the client.

## What was deliberately not touched

The architecture review that proposed this work also called for a `ViewSession`
module, on the grounds that view state was smeared across six methods with
"three inconsistent version conventions — `view_version: 0` hardcoded in one
send, omitted in another".

**That is not a defect.** There are two version *classes*, both correct:
version-gated scientific commands, and last-write-wins commands.
`ffast/visualization/view.py` exempts `SetCameraCommand` and `SetFrameCommand`
from the version check by design — frame playback and camera motion are excluded
from scientific undo history, and `_apply_set_frame` documents that a client
sending `view_version=0` forever must keep succeeding, because a version bump
there rejected every frame after the first as `STALE_VERSION`. The client's
`view_version: 0` matches that contract and says so in a comment; the camera send
omitting the field is the same rule.

So the version-stamping code was left alone. The only untidiness is that "the
server ignores this field" is expressed as a magic `0` rather than structurally,
which is not worth a module. Extracting `ViewSession` on the strength of a
misread would have churned the most renderer-entangled code in the client for no
correctness gain.

## Testing

The review claimed extraction would make "the pure kernels unit-testable", which
needed checking against ADR 0045's no-build, no-npm stance. It holds: the runtime
tests already use `page.evaluate` to read app internals, and the same mechanism
imports an ES module into a blank page over a plain `http.server` origin (ES
modules cannot be imported from `file://`). No JS toolchain is introduced.

- `test_web_pure_helpers.py` — 19 tests over `joinPath`, `canLoad`, `keyOptions`,
  `sessionStatus`, `exportStatus`, `safeName`. One browser launch evaluates every
  case; results are asserted individually. Sync fixture with `asyncio.run`
  inside, because pytest-asyncio's auto mode gives each test its own loop and a
  module-scoped async fixture cannot outlive it.
- `test_event_constants.py` — 6 tests parsing `events.js` and checking every name
  against `control.CLIENT_TO_SERVER`, `REPLY_EVENTS`, `SERVER_TO_CLIENT` and the
  announcement set, that each constant equals its value, and that no bare wire
  strings remain anywhere in the client.
- `test_server_session.py` — 4 tests: both acks carry the expanded absolute path,
  a failure acks `ok: false` with its reason, and the task names the desktop
  panel displays are unchanged.

The existing `test_web_save_and_load_session_restores_dataset` already asserted
"Saved session" / "Loaded session" in the status line, so it now covers the ack
path end to end; its comment describing the `TASK_DONE` mechanism was corrected.

1193 pass.

## Consequences

`SESSION_SAVED` / `SESSION_LOADED` join `SUBSET_EXPORTED` and `TAB_LAYOUT` as
dedicated one-shot announcements: emitted by their handler, not the generic
broadcast loop, so they stay out of `SERVER_TO_CLIENT` and out of `REPLY_EVENTS`
(no `cluster.connection` correlator entry). The desktop client ignores them and
keeps using the Tasks panel, which is why the task was left in place.

`app.js` is still a 1,026-line class holding view state, picking, playback, the
object rail and snapshot intake. This ADR does not claim it is no longer a god
object — it claims the two clusters with clean boundaries are out, and that the
third the review asked for was proposed on a misreading.
