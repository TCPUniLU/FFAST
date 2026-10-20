# 2. The browser's main 3D view is not an object

**Status:** decided 2026-10-20 as ADR 0060 (proposed), not built · **Strength:** strong · **Dependencies:** browser JavaScript,
in-process; tests can use a fake connection

## In one sentence

In the browser client, each independent 3D panel has an `IndependentView` object
holding its state. The main view's state is instead 13 separate fields on the
app object. So almost every 3D action has an "if independent … else main …"
branch, and the code that opens a view, moves its frame and camera, and tracks
its version is written twice.

## Background

- **Main view**: the one 3D view the rail (the dataset list) drives. Linked 3D
  panels show it.
- **Independent 3D panel**: a panel with its own view, data and settings
  (ADR 0056).
- **Focused 3D panel**: the panel that the sidebar, pick toolbar and playback
  strip act on.
- **Version**: each server view has a version number. The browser stamps every
  command with it, so replies that arrive out of order can be detected.

## What it looks like in the code (Read)

`ffast/renderers/web/static/app.js:1780-1791`, `_sendViewCommand`:

```js
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

The same split appears in:

- `_switchFocusedView` (`app.js:1036-1056`)
- `_focusedFrame`
- `_syncPlayback` (`app.js:2220-2232`)
- `_sendSetFrame`

The 13 main-view fields, with how many times `app.js` uses each (Ran: grep
counts):

| Field | Uses |
|---|---|
| `_currentDatasetFp` | 53 |
| `_currentModelFp` | 22 |
| `_currentViewId` | 18 |
| `_lastOpenedDatasetFp` | 16 |
| `_mainFrame` | 11 |
| `_frameOnSnapshot` | 8 |
| `_datasetsViewId` | 8 |
| `_viewVersion` | 6 |
| `_openedModelFp` | 5 |
| `_viewIdCounter` | 4 |
| `_cameraThrottle` | 4 |
| `_cameraSend` | 4 |
| `_mainAtomScale` | 3 |

That is about 160 uses in total. An exploring agent counted about 28
"main or independent" branch sites in `app.js` and 3 more in
`independent_panels.js`. (Unchecked count; the examples above are Read.)

`app.js` is 2372 lines. ADR 0050 had brought it down to 1026. About +390 of
the growth came from independent panels, +310 from ADR 0055's layout, and
+190 from 3D panels in tabs and focus. (Unchecked)

## What goes wrong

**acb0303** (Read: commit message). Picks from the previous dataset were not
cleared on the server, because the "clear picks" request went out after the
"open view" request. The fix reorders `_currentDatasetFp` around
`_clearPicks`. Ordering fixes like this are what happens when "the current
view" is several loose fields that have to be updated in the right order.

The second contributor's eight "multiple view behavior" commits went into this
same code.

**Probable mismatches** (Unchecked, not run):

- **"Colour atoms by this"** (Metrics pane) checks the rail's dataset, but sends
  the colouring to the focused view.
- **Try's bad-frame links** call `_setFrame` → `_playbackView()`. If an
  independent panel with its own frame has focus, that panel moves instead of
  the rail's dataset.
- **After METRICS_UPDATED**, only the focused view is recoloured.
- **Different controls act on different views:**
  - the Camera section's ortho and preset buttons use one renderer of the active
    panel;
  - the gizmo and background use every renderer of the view;
  - playback uses `_playbackView`;
  - a plot click chooses its own panel;
  - the Metrics pane uses the rail's dataset.

## Why it matters for testing

- **The pure modules don't break.** The ones extracted earlier (`frame_links`,
  `tab_rules`, `metric_logic`, `layout_state`) have needed no bug fixes.
  (Unchecked)
- **The bugs live in app.js routing state.** That state can only be reached
  through the roughly 90 browser tests that each start a server; the full web
  suite takes about 7 minutes.
- **The tests reach into internals.** They touch about 14 private members of
  the app. (Unchecked count)

So the place where testing is easy and the place where bugs happen are not the
same place.

## The architecture problem

A view module already exists (`IndependentView`, `views3d.js:248-266`), but the
main view bypasses it. By the deletion test, giving the main view the same
object would make the roughly 28 branches disappear. The complexity would
vanish, not just move somewhere else.

## Direction (not a design yet)

The main view becomes one more instance of the same view module. The app holds
"the focused view" and acts on it without asking which kind it is. Linking
(frame and camera following) stays in the browser, as ADR 0056 decided.

## Decided 2026-10-20

1. **One view object per dataset.** The main view is a family of server views,
   one `view-<n>` per dataset (`_datasetsViewId`, `app.js:1601-1606`). Each
   gets its own view object, kept until its dataset is deleted; "the main
   view" is a pointer to one of them, which the rail moves. The lookup table
   and `_viewIdCounter` go. A dataset switch becomes: the old object clears
   its picks, the pointer moves, the new object opens. This replaces the
   trick in `_openView` that sets `_currentDatasetFp` back to the old dataset
   so "clear picks" reaches the old view (`app.js:1596-1599`, the acb0303
   fix). A frame subset's object finds its parent's object to start from its
   look. Rejected: one object re-pointed at each dataset, since it would hold
   the same lookup table inside and keep the ordering problem.
2. **Each view object carries its own sidebar settings.** Today each of the
   seven panes (colour, camera, display, bonds, extract, export, align) keeps
   its own store, keyed by dataset fingerprint for a main view and by
   `ind:<tab>#<n>` for an independent panel, plus `_dsSettings` and
   `_viewForces` in the app; every switch saves all seven under the old key
   and loads all seven under the new (`app.js:1638-1697`). Instead the
   settings live in the view, and the panes read and write the focused
   view's. Deleting a view deletes its settings (today done by hand,
   `app.js:124-125`, `app.js:1471`). The panes still fill their controls on a
   switch. Cost: all seven panes change, most of them the other
   contributor's "multiple view behavior" work. Rejected: a single key rule
   with the stores kept, as smaller but leaving the stores to keep in step.
3. **The Metrics pane acts on the focused view, like the rest of the
   sidebar.** Try computes on the focused view's dataset, its bad-frame links
   move that view, and "Colour atoms by this" checks and colours that view.
   Today Try uses the rail's dataset (`app.js:823`) while its links move the
   playback view (`app.js:2208-2211`), and "Colour by" checks the rail but
   colours the focused view. A Try result names the dataset it ran on, since
   a change of focus leaves it describing another view. Rejected: the rail's
   dataset (colouring a view not looked at seems to do nothing), and a split
   (two rules). Separately, after METRICS_UPDATED every view coloured by a
   metric is recoloured, not only the focused one (`app.js:2102-2107`).
4. **Timing: no need to wait.** The other contributor commits slowly. Tell
   them before starting, since their alignment fix (e3320e4, picks kept per
   view) is in the same switching code, and ask them to push anything
   pending.
5. **Tests: both kinds.** Unit tests on the view object through
   `page.evaluate`, with a fake connection that records what it would send,
   and no server, as `test_metric_logic.py` and `test_web_pure_helpers.py`
   already do; written first, so they pin down what the object must do. The
   object's renderers (`ViewRenderers`, three.js) are faked or kept outside
   it. Plus a few server-backed browser tests for main-versus-independent
   cases, such as Try with the rail on one dataset and a focused independent
   panel on another. Existing browser tests that read the app's private
   fields go through the view object instead.

## Files

- `ffast/renderers/web/static/app.js`
- `ffast/renderers/web/static/independent_panels.js`
- `ffast/renderers/web/static/views3d.js` (`IndependentView` :248-266,
  `ViewRenderers` :103-115)
- `ffast/renderers/web/static/panes/metrics.js`

## Related

- ADR 0050 (web client seams), 0055 (browser layout), 0056 (mixed 2D/3D tabs)
- `CONTEXT.md`: Main view, 3D panel (linked, independent), Focused 3D panel
