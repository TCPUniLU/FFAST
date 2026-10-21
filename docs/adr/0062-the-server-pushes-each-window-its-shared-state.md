Status: Accepted 2026-10-21, built 2026-10-21 (branch `window-feed`, merged to `main` in `028aacb`). Decided after architecture review problem 5
(`docs/research/architecture-review-2026-10-14/05-window-state-feed.md`).

# The server pushes each window its shared state, from one window feed

## The problem

A browser window keeps its own copy of some shared server state. That state is:

- the dataset and model announcements;
- the metric catalog;
- the tab layout;
- the user-metrics list;
- "Used by" (who uses each metric);
- the inputs list (what a formula can use for the selected dataset and prediction).

When one of these changes, every window must get the new version. No module is
in charge of that. The sending is spread over five places, on the server and in
the browser:

1. `ServerSession.replay()` sends the dataset and model announcements and the
   catalog when a window connects.
2. `server.py` (`_on_dataset_loaded_meta`, `_on_model_loaded_meta`) builds the
   same announcements again, with a copy of the same code, to send them when
   something loads.
3. `app.js` asks for the tab layout and the user-metrics list after connecting.
4. `ServerSession._tell_metric_users()` sends the user-metrics list again after
   a colouring change, a view closing or a tab change. It is called from three
   places.
5. `app.js` `_onMetricsUpdated` asks for the catalog, the user-metrics list and
   sometimes the layout after a metric change. The server has just sent the
   user-metrics list on its own, so it arrives twice.

So each rule like "after a colouring change, send 'Used by' again" has to be
added by hand at each place that needs it. Several places were missed. Each
item below says how it was found.

- **The catalog's "Used by" goes stale** (read in the code). "Used by" is
  inside two messages: the catalog and the user-metrics list. A colouring or
  tab change sends only the user-metrics list. So "Used by" for built-in and
  project metrics stays old until the next metric change or reconnect.
- **A view opened with `start_from` copies a colouring silently** (read).
  `_on_open_view` copies the colouring of the view it starts from, but no one
  sends "Used by" again.
- **A closed window can keep a metric "in use"** (read). Sessions are kept in a
  `WeakSet` (`env.view_sessions`), and the server never removes a closed one.
  Each session holds itself through its handler table, so it stays until
  Python's cycle collector runs. Until then its views' colouring still counts,
  and a Delete can be wrongly refused (ADR 0057 rule 7).
- **The inputs list misses a newly saved metric** (read). The inputs list holds
  every registered metric (`build_input_catalog`). After a save, the browser
  doesn't ask for it again, so the new metric isn't offered as an input until
  the selected dataset changes.
- **"Used by" reads every tab file from disk, for every metric** (measured).
  `TabSources.metric_users` calls `entries()`, which reads and parses every
  user tab file. The catalog asks this once per metric, on the server's event
  loop. With 10 user tabs (copies of Basic Errors) and 50 metrics:

  | | no user tabs | 10 user tabs |
  |---|---|---|
  | build the catalog | 3.1 ms | 269 ms |
  | "Used by" for one metric | 0.1 ms | 5.2 ms |
  | "Used by" for all 45 metrics | 3.1 ms | 235 ms |

  Checking whether the folder changed (`os.scandir` plus `stat` of 10 files)
  takes 0.04 ms.

## The decision

### 1. One window feed builds every piece

A new module, `ffast/session/window_feed.py`, has one class, `WindowFeed`. There
is one per server, created in `server.py` next to the `ConnectionHub` and kept
as `env.window_feed`. It owns:

- **building** each piece, once, in one function each: dataset announcement,
  model announcement, metric catalog, tab layout, user-metrics list, metric
  users;
- **the replay**: everything a new window needs, in a fixed order;
- **sending again after a change**: which pieces a change touches (decision 4);
- **the list of open windows' sessions**, which "Used by" reads to find views
  coloured by a metric.

`ServerSession` and `server.py` stop building these messages themselves. They
call the feed.

### 2. The server pushes; the browser stops asking

- On connect, the replay sends every shared piece, in this order: each dataset
  announcement, each model announcement, `METRIC_CATALOG`, `TAB_LAYOUT`,
  `USER_METRICS`, `METRIC_USERS`. The catalog stays after the datasets and
  models, because the browser's `_onMetricCatalog` relies on that.
- After a change, the server sends the pieces that change touched (decision 4).
- The browser no longer sends `REQUEST_TAB_LAYOUT`, `REQUEST_USER_METRICS` or
  `REQUEST_METRIC_CATALOG`.
- The server **keeps** handling those three requests. Tests and older clients
  use them, and keeping them makes the change easy to undo. They answer only
  the window that asked.
- `REQUEST_METRIC_INPUTS` stays. It isn't asking for shared state: it tells the
  server which dataset and prediction the window is looking at. The server
  remembers that pair for each window and sends that window a fresh list after
  a metric change.

### 3. "Used by" is its own piece, `METRIC_USERS`

- A new server-to-client message, `METRIC_USERS`, with payload
  `{"users": {metric_id: ["tab Basic, panel MAE", ...]}}`. Its keys are sorted,
  and only metrics with at least one user are listed. It covers every
  registered metric whose id has no `__`, and every user-metric entry.
- `used_by` is removed from catalog entries (`build_metric_catalog`,
  `MetricCatalogEntry`) and from user-metric entries (`UserMetrics.entries`).
- The browser's Metrics pane takes "Used by" from `METRIC_USERS`.
- `UserMetrics.used_by(id)` stays as it is. The Delete check (rule 7) and the
  feed both use it.

**Why it must be separate:** when the catalog arrives, the browser redraws the
open analysis tab (`AnalysisManager.setMetricCatalog` → `_renderTab`) and
rebuilds the colour-by list. If "Used by" stayed in the catalog, every colouring
click in any window would redraw the plots in every window.

### 4. A piece is sent again whole, and only when it changed

- `WindowFeed.changed(*pieces)` rebuilds each named piece. It broadcasts the
  piece only if its packed bytes differ from the bytes it last broadcast for
  that piece. It always sends in the replay order.
- Without this check, sending the layout after every metric save would rebuild
  every analysis tab and 3D panel in every window, even when the layout didn't
  change. Today the browser avoids that with `hasUnresolvedPanels()`; the check
  replaces that.
- The replay and the answers to the three kept requests go to one window only.
  They don't count as "last broadcast".
- `changed` runs only on the server's event loop.

What each change sends:

| Change | Sent to every window after it |
|---|---|
| a user metric saved, deleted, or added from a session | `METRICS_UPDATED` (always, first), then `METRIC_CATALOG`, `TAB_LAYOUT`, `USER_METRICS`, `METRIC_USERS` (each only if changed), then each window's own `METRIC_INPUTS` |
| a tab saved, deleted, hidden or shown | `METRIC_CATALOG`, `TAB_LAYOUT`, `METRIC_USERS` (each only if changed) |
| a view's colouring changes; a view opens with a metric colouring copied by `start_from`; a view closes; a window closes | `METRIC_USERS` (only if changed) |
| a dataset loads or changes; a model loads | that object's announcement, as today |

The window that made a change still gets its own answer (`USER_METRIC_SAVED`,
`TAB_SAVED`) before the broadcasts, as today.

`METRICS_UPDATED` stays. On it, the browser forgets requests in flight and asks
each metric-coloured view for its colours again. It no longer asks for anything
else.

### 5. Tabs are kept in memory until their folder changes

`UserTabStore.tabs()` keeps the parsed tabs (and its `errors`) together with a
signature of the folder: the name, `st_mtime_ns` and `st_size` of each `*.toml`
file and of `state.json`. If the signature is unchanged, it returns the kept
tabs without reading any file. The store's own `write`, `delete`, `set_hidden`
and `_write_state` also throw the kept copy away. A tab file copied in by hand
is still found at the next read, as the class docstring promises.

### 6. A closed window is removed at once

- `WindowFeed.sessions` becomes a plain `set`.
- A new `ServerSession.close()` stops its Tries, clears its views, removes the
  session from the feed, and calls `changed(METRIC_USERS)` if one of its views
  was coloured by a metric.
- `server.py` calls `session.close()` where it calls `session.cancel_tries()`
  today.

## Considered options

- **The server only says "X changed" and the browser fetches X** (pull). This
  was rejected: it takes two trips per change, and every client needs its own
  fetch code for every piece.
- **Send only the changed entries** (deltas). Rejected: whole pieces are small
  (catalog 10–15 KiB, layout 12–58 KiB, measured), and one message per piece
  can't get out of step the way a stream of deltas can.
- **Keep "Used by" inside the catalog and make the browser skip the redraw when
  only "Used by" changed.** Rejected: the browser would have to know which
  catalog fields matter for drawing, and keep that list right as fields are
  added.
- **Keep a "Used by" table and update it at each change.** Rejected: every
  change path would have to update it correctly, which is the same "add it at
  each call site" problem this ADR removes. Rebuilding the whole map from memory
  takes about 3 ms.
- **Keep the tabs until the server itself writes a tab** (no folder check).
  Rejected: a tab file copied in by hand would not show until a restart.
- **Delete the three request handlers.** Not now: tests and older clients use
  them. They can go in a later commit.

## What it gives up

- The desktop client now also receives `TAB_LAYOUT`, `USER_METRICS` and
  `METRIC_USERS` on connect, and skips them, as it already skips
  `METRIC_CATALOG` (`cluster/connection.py` drops events that aren't in
  `CLIENT_ENV_SAFE`). That is a few tens of KiB per connect. Because it never
  reads the catalog sent over the connection, removing `used_by` from it
  doesn't affect the desktop client.
- After a metric or tab change, the server rebuilds up to four pieces to find
  the one that changed: a few milliseconds.
- The feed keeps the last bytes it broadcast for each piece: under 100 KiB.
- A window whose outbound queue is full misses a broadcast, as today.

## Rules for building it

These are for whoever implements the steps, including a model working alone.

- **One commit per step**, so each can be reverted alone. Subjects start with
  `refactor:`, `fix:`, `feat:` or `docs:`, as each step says. Stage files by
  name (`git add <files>`), never `git add -u` or `-A`. No `Co-Authored-By`
  trailer for a model.
- **Work on a branch in its own worktree**, not in the main folder, where the
  user commits at the same time:
  `git worktree add ../FFAST-window-feed -b window-feed main`. In the worktree,
  put `PYTHONPATH=$PWD` in front of every test command, and check once that
  `PYTHONPATH=$PWD ~/.venvs/ffast_env/bin/python -c "import ffast;
  print(ffast.__file__)"` prints a path inside the worktree. Merge to `main`
  only when the user says so.
- **Shell is zsh**: quote globs (`--include='*.py'`).
- **Write the step's tests first**, see them fail, then make them pass.
- **Run tests with the project venv**, not `uv run`:
  - one file: `~/.venvs/ffast_env/bin/python -m pytest <file> -q`
  - quick run before each commit:
    `~/.venvs/ffast_env/bin/python -m pytest -m "not integration" -q` (about
    25 s; 1743 passed at 928809b)
  - steps 3 to 6 also need the browser suite:
    `caffeinate -i ~/.venvs/ffast_env/bin/python -m pytest tests/ffast/renderers/web -q`
    (about 7 minutes)
  - full suite after the last step: `caffeinate -i ~/.venvs/ffast_env/bin/python -m pytest -q`
    (about 10 minutes; 2058 passed at 928809b)
- **Unit tests for browser modules** follow
  `tests/ffast/renderers/web/test_web_pure_helpers.py`: a static file server
  over `ffast/renderers/web/static`, a blank page, and `import()` of the module
  inside `page.evaluate`. Tests against a real server and a real page follow
  `tests/ffast/renderers/web/test_web_runtime.py` (the `ffast_web_server`
  fixture).
- **Session tests** follow `tests/ffast/session/test_server_session_user_metrics.py`:
  a fake env, a `ServerSession(env, asyncio.Queue(), broadcast=list.append)`,
  and `unpack` of what lands in the queue and the list.
- **Behaviour does not change** except where this ADR says so. A step that
  seems to need another change stops and asks.
- **Line numbers drift**; find code by function name.

## Steps

### Step 1. The window feed builds every piece; no behaviour change (`refactor:`)

Files: new `ffast/session/window_feed.py`, `ffast/session/__init__.py` (export
`WindowFeed`), `ffast/session/server_session.py`, `server.py`, new
`tests/ffast/session/test_window_feed.py`,
`tests/ffast/session/test_server_session_user_metrics.py`
(`view_colouring_users`), `tests/ffast/test_atom_coloring.py`
(`_replay_metric_catalog`).

- `WindowFeed(env, broadcast)`. `env` may be `None` (a test builds a catalog
  with no env); read every env attribute with `getattr(env, name, None)`.
- Builders, each returning packed bytes:
  - `dataset_meta(dataset) -> bytes | None` (`None` when it has no
    fingerprint);
  - `model_meta(model_fp, model) -> bytes`;
  - `metric_catalog()`, `tab_layout()`, `user_metrics()`.

  Move the bodies of `_replay_state`, `_on_dataset_loaded_meta`,
  `_on_model_loaded_meta`, `_replay_metric_catalog`, `_tab_layout_message` and
  `_user_metrics_message` into them. The two copies of the dataset code become
  one, and so do the two copies of the model code.
- `replay() -> list[tuple[bytes, str]]`: the messages a new window gets, each
  with a label for the log, in today's order (datasets, models, catalog). A
  piece that fails to build is logged as a warning and left out, as today.
  `ServerSession.replay()` sends each with `_emit_or_drop`.
- `sessions`: a `weakref.WeakSet` for now (step 6 changes it). `attach(session)`
  adds to it; `view_colouring_users(metric_id)` is the body of today's
  module-level `view_colouring_users`, reading `self.sessions`. Delete the
  module-level function and `env.view_sessions`.
- `ServerSession.__init__`: `self.feed = getattr(env, "window_feed", None) or
  WindowFeed(env, self._broadcast)`, then `self.feed.attach(self)`.
- `server.py`: create `env.window_feed = WindowFeed(env, hub.broadcast)` right
  after `hub = ConnectionHub()`. Move the `usage_providers` lines below it, with
  `env.window_feed.view_colouring_users` as the second provider. The
  `DATASET_LOADED` / `SUBDATASET_INDICES_CHANGED` / `DATASET_STATE_CHANGED` and
  `MODEL_LOADED` handlers call the feed's builders and `hub.broadcast` the
  result.
- Tests (`test_window_feed.py`):
  - with a fake env holding one dataset and one model, `replay()` gives
    `REMOTE_DATASET_META`, `REMOTE_MODEL_META`, `METRIC_CATALOG`, in that order;
  - `dataset_meta` of a dataset gives the same bytes that the replay holds for
    it;
  - `view_colouring_users` finds a view of an attached session coloured by
    `metric:<id>`, and nothing for another id.

### Step 2. Tabs are kept in memory until their folder changes (`fix:`)

Files: `ffast/config/user_tabs.py` (`UserTabStore`), `tests/ffast/test_user_tabs.py`
(or the existing test file for `UserTabStore`; find it with
`git grep -l UserTabStore -- tests`).

- Implement decision 5. Keep the signature, the tab list and the errors in one
  attribute, for example `self._kept = (signature, tabs, errors)`. Return a new
  list each time, so a caller can't change the kept one.
- Tests:
  - two calls to `tabs()` with no change parse each file once (wrap
    `UserTabStore._read` with a counter through `monkeypatch`);
  - a tab file copied into the folder by hand appears at the next call;
  - a file edited by hand (other content) is read again;
  - `write`, `delete` and `set_hidden` are seen at the next call.
- Measure before and after with a throwaway script in `temp/` (gitignored).
  The script builds `server._build_user_metrics(builtin_ids, metrics_dir)` and
  `server._build_tab_sources(None, tabs_dir)` over a folder of 10 copies of
  `ffast/config/builtin_tabs/01_basic_errors.toml`, each with another `name`.
  It then times `build_metric_catalog(default_registry, um)`. Expected: about
  269 ms before, about 3 ms after. Put both numbers in the commit message.

### Step 3. "Used by" is its own message (`feat:`)

Files: `ffast/protocol/control.py` (`METRIC_USERS`, with a comment next to
`USER_METRICS`), `ffast/renderers/web/static/events.js` (`IN.METRIC_USERS`),
`ffast/metrics/catalog.py`, `ffast/protocol/messages.py`
(`MetricCatalogEntry.used_by` removed), `ffast/config/user_metrics.py`
(`entries`), `ffast/session/window_feed.py`, `ffast/session/server_session.py`,
`ffast/renderers/web/static/app.js`, `ffast/renderers/web/static/panes/metrics.js`,
`ffast/renderers/web/static/metric_logic.js` (`groupMetrics`), `protocol.js`
(add a `MetricUsersKwargs` typedef). Tests:
`tests/ffast/session/test_server_session_user_metrics.py`,
`tests/ffast/test_user_metrics.py`, `tests/ffast/renderers/web/test_metric_logic.py`,
`tests/ffast/session/test_window_feed.py`.

- `WindowFeed.metric_users()`: decision 3. With no `env.user_metrics`, the
  payload is `{"users": {}}`.
- Replay: add `METRIC_USERS` after the catalog.
- `_tell_metric_users` broadcasts `self.feed.metric_users()` instead of the
  user-metrics list. `_change_metrics` broadcasts `METRIC_USERS` after
  `USER_METRICS`, because a save can change which metrics use another.
- Browser:
  - `app.js`: `conn.on(IN.METRIC_USERS, (kw) => this._panes.metrics.setMetricUsers(kw.users))`.
  - `panes/metrics.js`: a `metricUsers` object (`{}` at start and in
    `disconnected()`); `setMetricUsers(map)` stores it and calls `refresh()`;
    both `groupMetrics` calls pass it.
  - `groupMetrics(catalog, userMetrics, users = {})` sets
    `used_by: users[id] ?? []` on every entry. Set it after the `...u` spread
    so nothing overrides it.
- Tests:
  - the replay ends with `METRIC_USERS`, and its payload lists a user metric
    that another user metric uses (move the check now at
    `cat["ffast.force_component_mae"]["used_by"]` here);
  - catalog entries and user-metric entries have no `used_by` key;
  - the three tests under `# ── "Used by" stays current (rule 7)` expect
    `METRIC_USERS` where they now expect `USER_METRICS`;
  - `groupMetrics` with a users map fills `used_by` for a built-in and a user
    metric, and gives `[]` for an unused one.

### Step 4. The server pushes every piece, and sends a piece again only when it changed (`feat:`)

Files: `ffast/session/window_feed.py`, `ffast/session/server_session.py`,
`tests/ffast/session/test_window_feed.py`, the session test files that expect
today's broadcasts (`test_server_session_user_metrics.py`,
`test_server_session_tabs.py`).

- Replay order from decision 2: datasets, models, `METRIC_CATALOG`,
  `TAB_LAYOUT`, `USER_METRICS`, `METRIC_USERS`.
- `WindowFeed.changed(*pieces)`: decision 4. Keep the last broadcast bytes in a
  `dict` keyed by event name. Pieces are named by their event constants
  (`control.METRIC_CATALOG`, ...).
- Use the table in decision 4:
  - `_change_metrics`: after `USER_METRIC_SAVED` to the window that asked,
    broadcast `METRICS_UPDATED`, then `self.feed.changed(METRIC_CATALOG,
    TAB_LAYOUT, USER_METRICS, METRIC_USERS)`, then `await
    self.feed.resend_inputs()`.
  - `_change_tabs`: after `TAB_SAVED`, `self.feed.changed(METRIC_CATALOG,
    TAB_LAYOUT, METRIC_USERS)`.
  - Delete `_tell_metric_users`. Its callers (`_on_close_view`, the colouring
    check in `_on_view_command`) call `self.feed.changed(METRIC_USERS)`.
- Inputs for each window:
  - `ServerSession._inputs_for`: `None`, or the last `(dataset_fp, model_fp)`
    the window asked for.
  - `async def send_inputs()` builds and emits `METRIC_INPUTS` for that pair,
    and does nothing when it is `None`.
  - `_on_request_metric_inputs` stores the pair, then awaits `send_inputs()`.
  - `WindowFeed.resend_inputs()` awaits `send_inputs()` of every attached
    session (`asyncio.gather` over a `list()` copy of the set).
- The three kept request handlers answer with
  `self._emit(self.feed.metric_catalog())`, `self.feed.tab_layout()` and
  `self.feed.user_metrics()`.
- Tests:
  - the replay order above;
  - `changed(X)` broadcasts X once, a second `changed(X)` with nothing changed
    broadcasts nothing, and a real change broadcasts it again;
  - a metric save: the broadcasts start with `METRICS_UPDATED` and hold the
    catalog, the user-metrics list and `METRIC_USERS`. A session that asked for
    inputs gets `METRIC_INPUTS` on its own queue; a session that never asked
    gets none;
  - a tab hide: the window that asked gets `TAB_SAVED`, and the broadcasts hold
    `TAB_LAYOUT`;
  - the colouring tests keep their meaning: a command that changes the
    colouring broadcasts `METRIC_USERS`, and one that leaves it as it was
    broadcasts nothing. (`_dispatch_with_view` builds a new session, and so a
    new feed, for each call.)

### Step 5. The browser stops asking (`refactor:`)

Files: `ffast/renderers/web/static/app.js`, `ffast/renderers/web/static/analysis.js`
(or wherever `hasUnresolvedPanels` is defined), `ffast/renderers/web/static/metric_ops.js`,
a new test in `tests/ffast/renderers/web/test_web_runtime.py`.

- After connecting, `_connect` no longer sends `REQUEST_TAB_LAYOUT` or calls
  `this._metricOps.requestList()`. It still calls
  `this._panes.metrics.contextChanged()`, which asks for the inputs. Change the
  comment there to say that the catalog, layout and lists come with the connect
  replay (ADR 0062).
- `_onMetricsUpdated` keeps `forgetInFlight()` and the recolour loop, and
  sends nothing else. Update its doc comment.
- Delete `hasUnresolvedPanels` and `requestList` if nothing else calls them
  (`git grep`). Keep the `OUT.REQUEST_*` names in `events.js`; the server still
  knows them.
- Test against a real server: record what the page sends with
  `page.on("websocket", lambda ws: ws.on("framesent", record))`, and decode
  each binary frame with `ffast.protocol.rpc.unpack` (skip text frames, such as
  `"ping"`).
  - Connect, then wait until the analysis tab bar shows "Basic Errors".
  - Save a user metric from a second client (`_connect_headless_client`), then
    wait until the page's Metrics pane lists it (find the list in
    `panes/metrics.js`).
  - The page sent no `REQUEST_TAB_LAYOUT`, `REQUEST_USER_METRICS` or
    `REQUEST_METRIC_CATALOG`.

### Step 6. A copied or closed colouring updates "Used by" (`fix:`)

Files: `ffast/session/window_feed.py`, `ffast/session/server_session.py`,
`server.py`, `tests/ffast/session/test_window_feed.py`.

- `_on_open_view`: when `start_from` copied a colouring whose source starts
  with `metric:`, call `self.feed.changed(METRIC_USERS)` after sending the
  snapshot.
- Decision 6: `sessions` becomes a `set`, plus `WindowFeed.detach(session)`,
  `ServerSession.close()`, and `server.py` calling `session.close()` in the
  connection's `finally` block instead of `session.cancel_tries()`.
- Tests:
  - opening a view with `start_from` a metric-coloured view broadcasts
    `METRIC_USERS`;
  - two sessions on one feed: after the one with a metric-coloured view calls
    `close()`, it is out of `feed.sessions`, `METRIC_USERS` is broadcast without
    that metric, and `user_metrics.used_by(<id>)` no longer names the view (so
    a Delete is allowed).

### Step 7. Documents (`docs:`)

- This ADR: change the status line to built, and add an "As built" section
  listing the commits and any change from the plan.
- `docs/adr/README.md`: in the row under "Client and server protocol", change
  "(accepted)" to "(built)".
- `CONTEXT.md`: add a "Window feed" entry next to "Server session": the one
  server-side owner of what every window holds; it builds each piece, replays
  them on connect, and sends a piece again only when it changed. In "Server
  session", say that the replay comes from the window feed.
- `ffast/protocol/control.py`: the comments on `METRIC_CATALOG`, `TAB_LAYOUT`
  and `USER_METRICS` say they are pushed on connect and after a change, and
  also answer the kept requests.
- `docs/research/architecture-review-2026-10-14/05-window-state-feed.md`:
  status line `decided, see ADR 0062`.

## As built

Seven commits on branch `window-feed`, one per step:

1. `98ac876` window feed builds every piece.
2. `4dfdfb0` tabs kept in memory until their folder changes.
3. `347fca1` `METRIC_USERS`.
4. `f197b3d` the server pushes every piece; `changed()` sends only what changed.
5. `794dac4` the page stops asking.
6. `23da64f` a copied or closed colouring updates "Used by"; a closed window leaves the feed.
7. `994c92e` `CONTEXT.md` and `control.py` comments.

One fix after review:

- `8a7dbb2` a connection that ends during the handshake leaves the hub and the
  feed (the feed's plain `set` would otherwise hold its session for good); on
  close, the connection leaves the hub before its session closes, so its last
  `METRIC_USERS` goes only to the windows still open.

Changes from the plan:

- **Step 2 timing:** the catalog over 10 user tabs went from about 265 ms to
  about 8 ms, not about 3 ms. Each of the 45 "Used by" lookups still lists the
  folder and stats its files.
- **Step 4:** a window's inputs are resent with `drop_if_full`, so one window
  with a full queue cannot stall a metric save. `send_inputs` is on the session.
- **Step 6:** a view opened with `start_from` inside one session is already
  counted through its source view, so the extra `METRIC_USERS` is usually
  dropped by the "only if changed" check.
- **Browser test (step 5):** it also waits for the server to be ready first.
