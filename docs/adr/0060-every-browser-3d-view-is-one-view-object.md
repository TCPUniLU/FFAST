Status: Accepted, built 2026-10-21 (see "As built" at the end). Decided 2026-10-20, after architecture review problem 2
(`docs/research/architecture-review-2026-10-14/02-main-view-object.md`, whose
"Decided" section records the five answers).

# Every 3D view in the browser is one view object

## The problem

In the browser client, each independent 3D panel has an `IndependentView`
object (`ffast/renderers/web/static/views3d.js`) that holds its server view id,
version, frame, camera timer and data. The main view has no object. Its state
is loose fields on the app (`app.js`): `_currentViewId`, `_viewVersion`,
`_mainFrame`, `_datasetsViewId`, `_viewIdCounter`, `_lastOpenedDatasetFp`,
`_openedModelFp`, `_frameOnSnapshot`, `_framedViews`, `_cameraThrottle`,
`_cameraSend`, `_mainAtomScale`. So code that acts on "a view" asks first which
kind it is:

```js
// app.js, _sendViewCommand
if (view) {
  if (!view.isOpen) return;
  this._conn.send(OUT.VIEW_COMMAND, { view_id: view.viewId, view_version: view.version, ...fields });
  view.version++;
} else {
  if (!this._currentViewId) return;
  this._conn.send(OUT.VIEW_COMMAND, { view_id: this._currentViewId, view_version: this._viewVersion, ...fields });
  this._viewVersion++;
}
```

Counted on 2026-10-20: these fields, together with the null-means-main
`_focusedView`, `_mainView`, `_playbackView()`, `isIndependentViewId` and the
per-view settings maps, are used 153 times in `app.js`, in about 40
functions; `_openView` alone uses them 16 times and `_onSceneSnapshot` 14.

What has gone wrong, or reads wrong, in that code:

- **acb0303.** Picks of the dataset left were not cleared on the server,
  because "clear picks" was sent after "open view". The fix sets
  `_currentDatasetFp` back to the old dataset for one call so the clear
  reaches the old view (`_openView`). The "current view" is several fields that
  must change in the right order.
- **The Metrics pane mixes two views (Read).** With the rail on dataset A and a
  focused independent panel showing B: Try computes on A
  (`getContext: () => ({ datasetFp: this._currentDatasetFp, ... })`); a
  bad-frame link in its result calls `_setFrame`, which moves B's panel when its
  frame is its own (`_playbackView`); "Colour atoms by this" checks A and
  colours B.
- **After a metric changes, only the focused view is recoloured (Read).**
  `METRICS_UPDATED` → `colorBy.recolour()` → resends the colour source to the
  focused view. Other views coloured by that metric keep old colours.
- **A late snapshot from a main view no longer shown is drawn (Read, not
  reproduced).** `_onSceneSnapshot` takes every scene whose id is not `ind-<n>`
  as the main view's, applies it to the main renderers and takes its version.
  Switching A → B quickly could draw A's late answer over B.
- **Sidebar settings are kept in nine places.** Each of the seven panes
  (colour, camera, display, bonds, extract, export, align) keeps its own maps,
  and the app keeps `_dsSettings` and `_viewForces`. Keys are a dataset
  fingerprint for the main view and `ind:<tab>#<row>,<col>` for an independent
  panel. Every focus or dataset switch saves all nine under the old key and
  loads them under the new one (`_saveDatasetSettings`,
  `_restoreDatasetSettings`). `pickSettings` is saved and never read.

The bugs are all in routing state that only the ~90 server-backed browser tests
reach (about 7 minutes). The pure modules carved out earlier (`frame_links`,
`tab_rules`, `metric_logic`, `layout_state`) have needed no fixes.

## The decision

### 1. One view object per server view

A new module `ffast/renderers/web/static/views.js` holds a `View` class: what
the browser knows about one server view. It imports nothing that draws, so a
test can build it without a canvas.

```js
export class View {
  viewId;            // 'view-<n>' (main) or 'ind-<n>' (independent)
  datasetFp;         // what it shows
  modelFp;           // the prediction its last OPEN_VIEW carried, or null
  version = 0;       // stamped on every VIEW_COMMAND, resynced from replies
  frame = 0;         // its frame, as last set or reported
  openedPair = '';   // `dataset|prediction` of its last OPEN_VIEW; '' = not open
  framed = false;    // its first snapshot fitted the atoms
  frameOnSnapshot = null;   // a frame to show once its next snapshot is in
  cameraThrottle = null;    // pending SET_CAMERA timer
  cameraSend = null;        // and the send it will make
  settings;          // its sidebar settings (section 2)
  renderers;         // the ViewRenderers that draw it
  get isOpen() { return !!this.openedPair; }
  get isMain() { ... }      // viewId starts with 'view-'
  /** The VIEW_COMMAND payload for `fields`, stamped with the version, which
   * then advances; null when the view is not open. */
  command(fields) { ... }
}
```

`IndependentView` (stays in `views3d.js`) extends `View` and keeps only what is
its own: `key`, `spec`, `panel`, `links`, `applyingScene`, `started`, `note`,
`missing`.

**The main view is one `View` per dataset**, because the server already keeps
one `view-<n>` per dataset and the browser reuses it when the user comes back.
A `MainViews` collection in `views.js` owns them:

```js
export class MainViews {
  constructor({ renderers })      // the one ViewRenderers all linked panels share
  ensure(datasetFp)               // that dataset's View, made on first use as view-<n>
  get(datasetFp)                  // or undefined
  byViewId(viewId)                // or null
  drop(datasetFp)                 // forget it; returns the View (caller sends CLOSE_VIEW)
  clear()                         // a disconnect
  current                         // the View the main view shows now, or null
  point(view)                     // make `view` current
}
```

Every main `View` shares the one `ViewRenderers` (`_mainView` today), since
every linked panel draws whichever main view is current. A view's number comes
from a counter that never goes back, so a dataset deleted and loaded again gets
a new server view, as today.

**The rail's selection stays where it is.** `_currentDatasetFp` and
`_currentModelFp` remain the rail's choice. They drive the analysis tabs too,
and can change while no 3D panel is shown (no view opens then).
`this._main.current` is what the main view last opened.

Where today's fields go:

| Today, in `app.js` | After |
|---|---|
| `_datasetsViewId`, `_viewIdCounter` | inside `MainViews` |
| `_currentViewId` | `this._main.current?.viewId` |
| `_viewVersion` | `view.version` |
| `_lastOpenedDatasetFp` | `this._main.current?.datasetFp` |
| `_openedModelFp` | `this._main.current?.modelFp` |
| `_mainFrame` | `this._main.current?.frame ?? 0` |
| `_frameOnSnapshot` | `view.frameOnSnapshot` |
| `_framedViews` | `view.framed` |
| `_cameraThrottle`, `_cameraSend` | `view.cameraThrottle`, `view.cameraSend` |
| `_mainAtomScale`, `IndependentView.atomScale` | `view.settings.display.atomSize` |
| `_focusedView` (null meant main) | `_focusedIndependent` (null = main), and `_focused()` returning a `View` either way |

A dataset switch becomes explicit steps, in this order: the old view clears its
picks (the command goes to it by name); the pointer moves; the new view opens.
No field is set back temporarily.

A snapshot, patch or command result finds its view by id in either collection.
One for a main view that is not current updates that view's version and is not
drawn. One for no known view (a closed view's late answer) is dropped.

### 2. Each view carries its sidebar settings

`view.settings` is one plain object. A new module
`ffast/renderers/web/static/view_settings.js` exports `defaultSettings()`,
`copySettings(settings)` (a deep copy) and `startSettings(start, { modelFp,
catalog })` (an independent panel's starting look, from `start_settings.js`'s
`startCommands(...).settings`, today `_presetIndependent`). The shape, with
today's defaults:

```js
{
  colorBy: { source: 'element', prediction: null, colormap: 'viridis', norm: 'None' },
  camera:  { ortho: false, gizmo: false, background: '#000000' },
  display: { atomSize: 1, hide: '', highlight: '', unitCell: true },
  bonds:   { width: 100, color: DEFAULT_COLOR, bondType: 'Dynamic', fixedIndices: [] },
  extract: { indices: '' },
  export:  { background: '#000000' },
  align:   { kabsch: false, heavyOnly: true, atomAlign: false, idxInput: '', indices: [] },
  forces:  { show: false, modelKey: null, length: DEFAULT_LOOK.forces.length,
             normalised: DEFAULT_LOOK.forces.normalised, onlyForces: false,
             filterEnabled: false, atomIndices: [] },
  view:    { originCenterOfMass: true, videoFPS: 30, videoSkipFrames: 0 },
}
```

`colorBy.source` is the source string (`'element'`, `'displacement'`,
`'metric:<id>'`) and `colorBy.prediction` a fingerprint. Today the pane stores
the labels its drop-downs show. Labels depend on the metric catalog and the
loaded predictions; the source and the fingerprint do not.

**Panes show the focused view's settings and write into them as they change.**
Each pane's `saveState`, `loadState`, `presetState`, `lookOf` and its maps
are replaced by one `show(state)`. It fills the pane's controls from `state`
and keeps `state`. From then on, every change to a control writes the new value
into `state` before the pane calls its callback. This includes changes made by
code (`setPickedIndices`, `clearInput`, `chooseMetric`, `setPrediction`,
`setBackground`, `enableAtomAlignMode`). A focus switch is then one call per
pane with the new view's settings, and there is nothing to save on leaving.

`show` fires the same callbacks `loadState` fires today, and no others. The
camera pane's `loadState` dispatches `change`/`input` events, so the shared
renderers take the view's ortho, gizmo and background. The app's switch keeps
calling `_applyForceVectorsState`. Removing those resends is not part of this
decision.

The settings go wherever their view goes. Deleting a dataset drops its main
view, and the view's settings with it. A layout dropping an independent panel
drops its view. The hand-written deletes from `_dsSettings` and `_viewForces`
go.

A frame subset still starts with its parent's look: when `MainViews.ensure`
makes the view of a dataset whose parent already has one, it gives it
`copySettings(parent.settings)`. That is what `_lookParentOf` does today, done
once when the subset's view is made. An atom subset does not inherit, as today.

An independent view's settings are `null` until it gets its starting look,
at the same two moments as today: when it is first focused
(`_switchFocusedView`) and when its view starts (`_startIndependent`).

### 3. The sidebar, the Metrics pane included, acts on the focused view

`_focused()` returns the focused independent panel's view, or else
`this._main.current`. Every "the view the sidebar acts on" reads it:
commands, picks, playback, colouring, the panel title.

The Metrics pane follows the same rule:

- **Try** computes on `_focused()`'s dataset and prediction. Its result names
  them, for example "12 values, all good — on aspirin · MACE", because a
  later change of focus leaves it describing another view.
- **A bad-frame link** in Try's result shows that frame of the dataset Try ran
  on, through the plot-click path (`_onPlotPoint({ datasetFp, modelFp, frame
  })`), which already picks the 3D panel that shows that data. One
  consequence: if the focused panel follows the main view's frame and shows a
  dataset the main view does not hold, the main view (and the rail) moves to
  that dataset, as a plot click does.
- **"Colour atoms by this"** checks and colours `_focused()`.
- **The pane's inputs list** is asked for again when focus moves to a view with
  other data (`metricsPane.contextChanged()`), as it is when the rail changes.

After `METRICS_UPDATED`, every open view whose `settings.colorBy.source` is a
metric gets its colour source sent again, not only the focused one.

### What stays as it is

- The server and its protocol. No Python changes.
- Frame and camera linking stay in `IndependentPanels`
  (`independent_panels.js`), as ADR 0056 decided. Its `main()` port reads
  `this._main.current`.
- Picks stay app-level (`_picked`) and are cleared on every switch, as today.
- Which renderer a Camera-section button uses (ortho and presets: the active
  panel's renderer; gizmo and background: every renderer of the view) is not
  decided here.

## Considered options

- **One main-view object re-pointed at each dataset.** It would hold the
  dataset → view-id table inside it, and the save-old, clear-old, open-new
  order would move into it rather than go. Rejected.
- **Keep the panes' stores, one key rule.** Every view answers
  `settingsKey` and the panes stay as they are. Smallest, and it leaves the
  other contributor's pane code untouched. It keeps nine stores to keep in
  step and the save step on every switch. Rejected in favour of settings on
  the view.
- **Metrics pane on the rail's dataset.** Try and the 2D plots would always
  agree, but colouring the main view while the user looks at an independent
  panel seems to do nothing. **Split (Try on the rail, colouring on the focused
  view):** two rules to remember. Both rejected.
- **Tests only through the server-backed browser suite.** Slow (about 7
  minutes) and tied to private fields this decision renames. Rejected for
  unit tests on `views.js`, `view_settings.js` and the panes, plus a few
  server-backed tests for the routing cases.

## What it gives up

- All seven panes are rewritten. Most of their per-view state code is the
  other contributor's "multiple view behavior" work (1e8caca..c0e7b37,
  e3320e4). Tell them before step 4.
- The Metrics pane changes what it computes on when focus moves; a Try result
  shown on screen can describe a view that is no longer focused (it says
  which).
- A bad-frame link can move the rail, as a plot click can.
- Tests that read `_mainFrame`, `_viewVersion` or `_mainView` through
  `window.ffastApp` are rewritten to read the view objects.

## Rules for building it

These are for whoever implements the steps, including a model working alone.

- **One commit per step** (or per lettered sub-step), so each can be reverted
  alone. Subjects start with `feat:`, `fix:` or `docs:`; a step that only
  moves code without changing what the user sees is `feat:`. Stage files by
  name (`git add <files>`), never `git add -u` or `-A`. No `Co-Authored-By`
  trailer for a model.
- **Shell is zsh**: quote globs in grep options (`--include='*.js'`).
- **Work on a branch in its own worktree**, not in the main folder, where ADR
  0058's step 6 is in progress:
  `git worktree add ../FFAST-views -b views main`. Merge to `main` when a step
  is green. The venv's editable install points at the main folder, so in the
  worktree run every test command with `PYTHONPATH=$PWD` in front, and check
  once that `PYTHONPATH=$PWD ~/.venvs/ffast_env/bin/python -c "import ffast;
  print(ffast.__file__)"` prints a path inside the worktree.
- **Behaviour does not change** except where this ADR says so (sections 1–3:
  the late-snapshot rule, the Metrics pane, recolouring every view). A step
  that seems to need another change stops and asks.
- **Write the step's tests first**, see them fail, then make them pass.
- **Run tests with the project venv**, not `uv run`:
  - one file: `~/.venvs/ffast_env/bin/python -m pytest <file> -q`
  - the browser suite: `caffeinate -i ~/.venvs/ffast_env/bin/python -m pytest
    tests/ffast/renderers/web -q` (about 7 minutes; all tests there are marked
    `integration`, so the quick `-m "not integration"` run skips them)
  - before each commit: the browser suite, and the quick run
    `~/.venvs/ffast_env/bin/python -m pytest -m "not integration" -q`
    (about 25 s).
- **Unit tests for browser modules** follow
  `tests/ffast/renderers/web/test_web_pure_helpers.py`: a static file server
  over `ffast/renderers/web/static`, a blank page, `import()` of the module
  inside `page.evaluate`, all cases in one batch, each asserted on its own.
  A fake connection is an object whose `send(event, kwargs)` pushes
  `[event, kwargs]` onto an array.
- **Server-backed tests** reuse the helpers in
  `tests/ffast/renderers/web/test_web_runtime.py` (`_spawn_server`,
  `_open_app`, `_open_compare`, `_focus_3d`, `_INDEPENDENT`). To see what the
  app sends, wrap `window.ffastApp._conn.send` in `page.evaluate` and record
  its arguments.
- **Do not edit** `ffast/renderers/web/static/vendor/`, any Python file, or the
  protocol (`events.js`, `protocol.js`).
- Line numbers drift; find code by function name.

## Steps

### Step 1. `views.js`: the `View` and `MainViews` classes, tested alone

Nothing in the app uses them yet.

- Create `ffast/renderers/web/static/views.js` with `View` and `MainViews` as in
  section 1. `View`'s constructor takes `{ viewId, datasetFp = null, modelFp =
  null, renderers = null, settings = null }`. `command(fields)` returns `{
  view_id, view_version, ...fields }` and advances `version`, or returns `null`
  when not open. `MainViews.ensure` names views `view-0`, `view-1`, … in order
  of first use.
- Make `IndependentView` (`views3d.js`) extend `View`, removing the fields
  `View` now has (`viewId`, `datasetFp`, `modelFp`, `version`, `frame`,
  `openedPair`, `cameraThrottle`, `isOpen`). Its `frame` starts at `null`, as
  today: pass it after `super()`. `independent_panels.js` keeps working
  unchanged.
- Tests first, in a new `tests/ffast/renderers/web/test_web_views.py`:
  - `ensure('A')` twice gives the same object, `view-0`; `ensure('B')` gives
    `view-1`.
  - `point(v)` makes `current` that view; `clear()` empties it and `current`
    is `null`.
  - `command()` on a view never opened returns `null`; after `openedPair =
    'A|'`, two calls carry `view_version` 0 then 1.
  - `byViewId('view-1')` finds B's view; `byViewId('ind-3')` and unknown ids
    give `null`.
  - `drop('A')` returns A's view; then `byViewId('view-0')` is `null` and
    `ensure('A')` makes `view-2`.
  - Every main view's `renderers` is the one passed to `MainViews`.
- Done when: the new tests pass and the browser suite is green.

### Step 2. The main view's state moves into `MainViews`

- In `App`, create `this._main = new MainViews({ renderers: this._mainView })`
  in `_initViews`.
- Replace every use of `_datasetsViewId`, `_viewIdCounter`, `_currentViewId`,
  `_viewVersion`, `_lastOpenedDatasetFp`, `_openedModelFp`, `_mainFrame`,
  `_frameOnSnapshot`, `_framedViews`, `_cameraThrottle` and `_cameraSend` as in
  the table in section 1. The `framed()` port of `IndependentPanels` goes; it
  sets `view.framed` itself.
- Rewrite `_openView` in the explicit order of section 1. `_clearPicks(view)`
  takes the view to clear (default `this._focused()`), and
  `_sendSetSelection` passes it on to `_sendViewCommand`. The trick of setting
  `_currentDatasetFp` back for one call is deleted.
- `_onSceneSnapshot`, `_onScenePatch` and `_onCommandResult` find the view
  with `this._independent.byViewId(id) ?? this._main.byViewId(id)`. A scene
  with no `view_id` belongs to `this._main.current`. The late-snapshot rule of
  section 1 applies.
- `_onDatasetDeleted` uses `this._main.drop(fp)` and sends `CLOSE_VIEW` for
  the view it returns. `_emptyMainView` and the disconnect use `point(null)`
  and `clear()`.
- Main-view settings still live in `_dsSettings` and the panes, keyed by
  dataset fingerprint, until step 4.
- Tests first (server-backed, new tests in `test_web_runtime.py`):
  - A → B → A: the third `OPEN_VIEW` reuses A's view id, and the
    `SET_SELECTION` that clears A's picks names A's view and is sent before
    B's `OPEN_VIEW`.
  - A snapshot replayed for a main view that is not current (send A's last
    scene to `ffastApp._onSceneSnapshot` while B is shown) leaves B drawn
    (`ffastApp._mainView.scene`) and sets A's view's version.
  - The existing tests that read `_mainFrame` or `_viewVersion` read
    `_main.current.frame` / `.version` instead.
- Done when: `grep -nE "_datasetsViewId|_viewIdCounter|_currentViewId|_viewVersion|_lastOpenedDatasetFp|_openedModelFp|_mainFrame|_frameOnSnapshot|_framedViews|_cameraThrottle|_cameraSend" ffast/renderers/web/static/*.js`
  finds nothing, and the browser suite is green.

### Step 3. One focused view

- Rename `_focusedView` to `_focusedIndependent` and add `_focused()`
  (section 3).
- Remove the main-or-independent branches that only chose between the two
  kinds of state: `_sendViewCommand`, `_focusedDatasetFp`, `_focusedModelFp`,
  `_focusedRenderers`, `_focusedFrame`, `_syncPlayback`, `_sendSetCamera`
  (one `_sendSetCamera(view, cam)` for both kinds, replacing the camera throttle
  in `IndependentPanels._onCamera` too), `_trackCameraCOM` (takes a view),
  `_onScenePatch`, `_onCommandResult`. `isIndependentViewId` is no longer used
  in `app.js`.
- Branches that are real differences stay: only the main view is followed by
  linked panels (`_sendMainFrame` → `syncLinkedFrames`), and only an
  independent panel has a Panel section.
- Tests first: a unit test that `View.command` is the only place a version is
  stamped (the fake connection sees consecutive versions for a mix of commands
  to two views); then the browser suite.
- Done when: `grep -n "isIndependentViewId" ffast/renderers/web/static/app.js`
  finds nothing and the browser suite is green.

### Step 4. Settings move into the view

One commit for 4a, then one per pane, then 4i. Order: the easy panes first,
colour last.

- **4a. The settings object and the app's own settings.** Create
  `view_settings.js` (section 2). Main views get `defaultSettings()` from
  `MainViews.ensure`, or `copySettings` of a frame subset's parent; pass
  `parentOf: (fp) => ...` (today `_lookParentOf`) to `MainViews`. Move
  `_originCenterOfMass`, `_forcesState`, `_viewForces` and the FPS/skip values
  into `view.settings.view` and `view.settings.forces`: the pane callbacks and
  the `fps-input`/`skip-input` listeners write into `this._focused().settings`.
  Delete `pickSettings` (never read). `_dsSettings` and `_viewForces` go. The
  seven panes still use their maps, keyed by `view.settingsKey` (a main view's
  is its dataset fingerprint).
- **4b–4h. One pane per commit**: `display` (its `atomSize` becomes a number;
  the renderers' `atomScale` reads `view.settings.display.atomSize`, and
  `_mainAtomScale` and `IndependentView.atomScale` go), `camera`, `export`,
  `extract`, `bonds`, `align`, `colorBy` (stores source and fingerprint, maps
  them to labels when it shows them, and shows again when the catalog or the
  model list arrives). Each pane gets `show(state)` with write-through and
  loses its maps and its `saveState`/`loadState`/`presetState`/`lookOf`. The
  app calls `pane.show(view.settings.<pane>)` where it called `loadState`, and
  reads `view.settings` where it called `lookOf` (`_currentStart`).
- **4i. The switch.** `_saveDatasetSettings` goes. `_restoreDatasetSettings`
  becomes `_showSettings(view)`: every pane's `show`, the COM tick, the
  FPS/skip boxes, and `_applyForceVectorsState`, as today. `_presetIndependent`
  becomes `view.settings = startSettings(...)`. `settingsKey` and
  `_lookParentOf`'s use in restoring go.
- Tests first, for each pane (unit, as `panes/extract.js` already is in
  `test_web_pure_helpers.py`): build the pane on a fresh `<div>`, `show(s)`,
  check the controls show `s`; change a control (set `.value`, dispatch
  `input`/`change`), check `s` changed; `show(t)` then change, check `s` did
  not. For 4a: `copySettings` is deep (changing the copy's
  `bonds.fixedIndices` leaves the original), and a frame subset's new main
  view starts from its parent's settings, an atom subset's from the
  defaults. Server-backed, after 4i: colour A by an element map and atom size
  2, switch to B, back to A: A shows both again. Focus an independent panel,
  change its bond width, focus the main view: the main view's bond width is
  unchanged.
- Done when: `grep -rnE "saveState|loadState|presetState|lookOf|_dsSettings|_viewForces|_mainAtomScale|settingsKey" --include='*.js' --exclude-dir=vendor ffast/renderers/web/static`
  finds nothing, and the browser suite is green.

### Step 5. The Metrics pane acts on the focused view

- `getContext` returns `_focused()`'s `datasetFp` and `modelFp`. The
  pane keeps the context a Try ran on with its result.
- `tryHeadline` (`metric_logic.js`) takes the pair's name and ends its text with
  `— on <name>` (unit test in `test_metric_logic.py`).
- `onGoToFrame(frame)` becomes `_onPlotPoint({ datasetFp, modelFp, frame })`
  with the Try's dataset and prediction.
- `_colourAtomsBy` checks `_focusedDatasetFp()`.
- `_switchFocusedView`, and a change of the focused independent panel's data
  (Panel section), call `this._panes.metrics.contextChanged()`.
- Tests first (server-backed, with `_open_compare`): rail on A, independent
  panel on B focused. Try sends `TRY_METRIC` with B's `dataset_fp`; its
  headline names B. "Colour atoms by this" sends `SET_PARAMETER` to the
  `ind-` view. A bad-frame link moves B's panel when its frame is its own.
- Done when: these pass and the browser suite is green.

### Step 6. Every view coloured by a metric is recoloured

- `_onMetricsUpdated` sends `SET_PARAMETER ffast.atom_color source` to every
  open view (main views and independent) whose `settings.colorBy.source`
  starts with `metric:`. `colorBy.recolour()` goes.
- Test first (server-backed): two views coloured by a user metric, edit the
  metric, both views get the new `source` command.

### Step 7. Documents

- `CONTEXT.md`, **Main view**: "the view the rail drives; on the server, one
  view per dataset, reused when the user comes back to it".
- This ADR's status and "as built" notes; the brief's status; the row in
  `docs/adr/README.md`.

## As built (2026-10-21)

All seven steps are built, one commit each except as noted.

- **Step 4 commits.** 4a is its own commit; the display, camera, export,
  extract, bonds and align panes (4b-4g) went in one commit, and the colour
  pane with the switch (4h-4i) in another, because the app side of each pane
  was too tangled to split into working states. Each pane has unit tests in
  `test_web_panes.py`.
- **Rail versus open view.** `_focusedDatasetFp()` / `_focusedModelFp()` still
  answer the rail's choice for the main view, not `main.current`. They differ
  only while no 3D panel is shown, and the Metrics pane and the sidebar title
  rely on the rail's answer there. Step 3 therefore did not remove them.
- **Try's headline** adds ` — on <name>` only for a finished Try (all good or
  bad values), not for an error or a stop.
- **Export background.** Showing a view's camera settings no longer copies the
  camera's background into the Export pane; each keeps its own value.
- **Tests.** The browser suite was run in four parallel shards of the same
  tests (about 4 minutes) instead of one 8-minute run.

