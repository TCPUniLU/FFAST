# 5. What a browser window receives is decided in five places

**Status:** decided, see ADR 0062 · **Strength:** worth exploring · **Dependencies:** a fake
outbound queue and the real hub, both usable in tests

## In one sentence

A browser window needs several kinds of data to stay current: datasets, models,
the metric catalog, tabs, user metrics and the input list. That data is sent
from five different places on the server and the client. So rules like
"re-send the 'Used by' list after a colouring change" get added one call site
at a time, and are easy to miss.

## Background

- **Server session**: the object on the server representing one connection.
  Since ADR 0044 there is one per browser connection (`server.py:251`).
  `CONTEXT.md` and the docstring of `ffast/session/server_session.py` still say
  "one per server process"; that is out of date.
- **Replay**: what the server pushes to a window right after it connects.
- **Broadcast**: a message sent to every connected window.

## The five senders (Read unless marked)

1. **`ServerSession.replay()`** (`ffast/session/server_session.py:348-358`)
   pushes the dataset and model announcements (`REMOTE_DATASET_META`,
   `REMOTE_MODEL_META`) and the metric catalog.
2. **`server.py:640-695`** builds the same dataset and model announcements
   again, to broadcast them when something loads. Compare it with
   `server_session.py:371-414`: the same code, written twice.
3. **`app.js` on connect** (around `:1278-1281`, Unchecked line numbers) asks
   for the tab layout, the user-metrics list and the input list.
4. **`_tell_metric_users()`** re-sends the user-metrics list after a colouring
   change, a view closing, or a tab change. It is called from 3 sites
   (`server_session.py:951, 985, 1271`).
5. **`app.js` `_onMetricsUpdated`** (`:2103-2108`) runs when a metric changes:
   - it asks for the catalog and the user list again, and maybe the tab
     layout;
   - then it recolours.

   The server already broadcasts the user list right after `METRICS_UPDATED`
   (`server_session.py:1336-1337`), so the list arrives twice.

## Dead code (Read)

`_replay_views` (`server_session.py:437-453`) is meant to send a scene snapshot
of each open view on reconnect. It never runs, for three reasons:

- each connection gets a new session, which has no views;
- replay runs before any view opens;
- no client sends `REQUEST_STATE_SYNC`; only tests do.

## What goes wrong (Unchecked unless marked)

dbca85f fixed one case: the "Used by" list was not re-sent after colouring and
tab changes. Other cases of the same kind:

- **The catalog's "Used by" goes stale.** "Used by" is computed for two
  messages, the catalog and the user-metrics list, but only the user list is
  re-sent. So the catalog's "Used by" text for built-in and project metrics
  goes stale.
- **A closed window can keep a metric "in use".**
  - Its colouring may still count until Python's garbage collector runs. Sessions
    are held in a `WeakSet`, but each session is in a reference cycle through its
    own handler table.
  - That could wrongly refuse a Delete.
- **`start_from` copies colouring silently.** Opening a view with `start_from`
  copies its colouring without re-sending the list.
- **"Used by" reads from disk.** Each computation reads every tab file, once per
  metric, on the event loop.

## The architecture problem

No module owns "the state a window holds". By the deletion test, a single feed
module would absorb:

- the replay;
- both announcement builders;
- the three `_tell_metric_users` sites;
- half of `_onMetricsUpdated`.

The complexity would concentrate in one place instead of vanishing, so the
module would earn its place.

## Direction (not a design yet)

One server module lists every piece of shared state a window holds and builds
each piece once. It sends all of it on connect, and re-sends the pieces a change
touches.

## Questions to settle before building

1. **Push or pull?** Today it is a mix. Should the server push everything, so
   the client stops re-asking? Or should the server only say "X changed" and
   let the client fetch it?
2. **Dead code.** Delete `_replay_views` and `REQUEST_STATE_SYNC` now, as a
   small separate commit?
3. **"Used by".** Should it be kept in memory and updated on change, instead of
   read from disk every time?
4. **CONTEXT.md.** Fix "Server session" to say one per connection.

## Files

- `ffast/session/server_session.py` (`replay`, `_replay_state`,
  `_replay_metric_catalog`, `_replay_views`, `_tell_metric_users`,
  `view_colouring_users`)
- `server.py` (`_on_dataset_loaded_meta`, `_on_model_loaded_meta`, the
  `view_sessions` setup around :603-614)
- `ffast/metrics/catalog.py`, `ffast/config/user_metrics.py` (`entries`),
  `ffast/config/user_tabs.py` (`metric_users`)
- `ffast/renderers/web/static/app.js`

## Related

- ADR 0044 (multi-client view controllers), ADR 0057 (rule 7, "Used by")
- Commit dbca85f
