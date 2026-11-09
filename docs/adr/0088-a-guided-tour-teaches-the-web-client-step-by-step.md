# A guided tour teaches the web client step by step

Status: Proposed 2026-11-06. Not built. Decided from a clickable mockup
(https://claude.ai/artifact/1FFBDszXAZESySVmmSuD6j; a copy is in
`docs/research/guided-tour-2026-11-06/mockup.html`). Plan:
`docs/implementation/2026-11-06-guided-tour.md`. Client only: no server,
protocol or session-file change. Builds on ADR 0055 (layout, hint bar, action
list) and ADR 0050 (client modules).

## The problem

A new user who opens the web client sees "No dataset open" with two buttons,
Load Dataset… and Load Example. After loading, the hint bar shows three lines
of text, and the "?" buttons explain one control at a time. Nothing shows the
order of the work: load data, look at a frame, move through frames, see where
the model is wrong. Nothing points at the control to use for each of those.

This request came from the project owner on 2026-11-06, not from a measured
failure or a user report. So the first build is kept small: one tour, client
only, easy to remove.

## What was decided

**A tour is a list of steps. Each step lights up one part of the screen.**
Everything else is dimmed and cannot be clicked. A ring marks the lit area. A
box next to it shows "Step 3 of 7", a row of dots, a short title, one or two
sentences, and these buttons:

- **Skip tour**, always. Esc does the same.
- **Back**, on every step but the first.
- **Next** on a "look at this" step. A "do this" step has no Next. It ends when
  the user does the thing (clicks Force error, drags the slider), and until then
  the box says what it is waiting for ("Drag the slider").

Tab moves only between the box and the lit controls. Focus goes to the lit
control on a "do this" step, and to Next otherwise. When the tour ends, focus
returns to where it was.

**The user starts a tour. It never starts by itself.** There are two ways in:

- the File menu, after a separator, one item per tour ("Tour: First steps");
- a third button, **Take the Tour**, on the empty screen next to Load Dataset…
  and Load Example. It starts the tour marked as the default.

Each tour is an entry in the action list (ADR 0055), so the command palette
finds it too. Like any action, a tour can be unavailable with a reason, shown
on the greyed-out menu item. "First steps" is unavailable in a read-only
window, and when a dataset is open without a prediction (it needs either
nothing loaded, or a dataset and a prediction). Since nothing starts by itself,
there is no "tour already seen" state to store.

**Going Back can undo a step.** A step may have an undo action, which runs when
Back returns to it. Loading the example is the one step that uses it: Back from
step 2 deletes the example dataset and prediction, and step 1 waits for Load
Example again. Undo deletes only the fingerprints that this step loaded. The step
is only in the tour when nothing is loaded at the start, so undo never removes
data the user loaded. A "do this" step that is already done, for example after
Back, shows Next instead of waiting again.

**Tours are data, in `tours.js`. The runner that shows them is
`tour_runner.js`, and it knows nothing about FFAST.** Steps are defined once and
can be used by several tours. A tour is a name, a short description, and a list
of steps. Adding a guide means adding entries, not changing the runner.

| Step field | Meaning |
|---|---|
| `targets` | What to light up: CSS selectors, or functions that return an element (the 3D view has no fixed ID). Several are joined into one lit area. |
| `prepare` | Runs before the step is shown: switch to the tab with the 3D view, open a hidden sidebar. |
| `side` | Where the box goes: top, bottom, left, right, or inside the lit area. |
| `title`, `body` | The text. |
| `until` | The app event that ends a "do this" step. No `until` means a Next button. |
| `wait` | Text or a function, refreshed on every app event while waiting for `until`. |
| `links` | Documentation links (`label`, `href`), opened in a new tab so the tour stays in place. |
| `actions` | Buttons in the box: `{label, primary?, run, enabled?}`. `enabled()` refreshes on every app event; a waiting step uses an action once. |
| `noNext` | Hide Next, for a final Keep/Delete choice. |
| `done` | True when the action is already done. The step then shows Next. |
| `failOn` | An app event that stops the tour, with that event's message. |
| `skip` | Leave the step out when the tour starts (for example, the example is already loaded). |
| `undo` | Runs when Back returns to this step. It may return a promise; the box says "Undoing…" until it settles. |

The runner is one small module with no library. On every animation frame it
finds the targets, measures them, places four shade rectangles around them, and
places the box. Measuring every frame means the lit area follows resizes and
panels that open or close without extra code.

**App events are the contract between the app and the tours.** `app.js` sends a
short list of named events: `example-loaded`, `example-load-failed`,
`dataset-deleted`, `frame-changed`, `quick-style-applied`. A new tour adds the
events it needs to this list. Tours only listen.
They never call into the app, except through `undo`, which uses the existing
delete request (`DELETE_OBJECT`).

**A tour stops instead of getting stuck.** If a step's target is missing or has
no size, the tour ends with a message naming the step ("Tour stopped: 'Move
through frames' needs something that is not on screen."). If the control a "do
this" step waits for is disabled, the tour ends with the control's own reason
(its title, such as "Select a prediction first"). If loading the example
fails, the tour ends with the server's reason. One known case is a server
installed without the repository, which has no examples
(`ServerSession._on_load_example`).

**The first tour, "First steps", has 7 steps.** It runs on the example pair in
`examples/data` (100 small molecules from SPICE with MACE forces):

| # | Lit area | What it says | Ends when |
|---|---|---|---|
| 1 | Load Example | A small example: a dataset and one model's predictions for it. | Example loaded (skipped if already loaded; undone by Back) |
| 2 | 3D view | One frame: the atoms of one geometry. Drag to rotate, scroll to zoom. | Next |
| 3 | Frame slider | Each frame is one geometry with its reference energy and forces. | Slider moved |
| 4 | Datasets and Predictions lists | Datasets hold the reference; predictions are a model's answers for the same frames. | Next |
| 5 | Quick style ▸ Force error | Colours each atom by how far the model's force is from the reference. | Force error applied |
| 6 | 3D view and frame slider | How to read the colours and the colour bar; drag the slider to compare frames. | Next |
| 7 | Hint bar | Suggested next steps; tours are in File ▸ Tours. | Done (skipped if the hint bar was dismissed) |

Only this tour is built now. The mockup's second tour, "Playback", existed only
to show that steps can be shared between tours.

**Tests.** The runner is tested on its own, on a plain test page. One browser
test per tour clicks through every step on the example, including one Back that
undoes step 1. Another loads the example and checks that every target of every
tour finds an element, so a renamed control breaks a test, not the tour. (A
check against `index.html` alone would not work: some targets, such as the
Quick style buttons, are made by script.)

## Alternatives that were live

- **Start the tour automatically on the first visit.** This finds more new
  users. Rejected because many users open FFAST to look at their own data
  straight away. A tour that starts by itself is in their way, and it would need
  stored "seen" state.
- **Dim but leave everything clickable.** This is friendlier to experienced
  users. Rejected because one click elsewhere (closing a panel, loading another
  file) can remove the next step's target mid-tour.
- **Make loading the example one-way, with no Back past it.** This is simpler.
  Rejected because Back should work the same on every step, and the example can
  be deleted like any other data.
- **A ready-made JavaScript tour library.** It would give a tested overlay and
  positioning. Rejected for now: it adds a dependency to a client that has
  almost none, and still needs the FFAST-specific parts (app events, undo,
  stopping when a target is missing). The runner is small enough to own.
- **Put the steps in the hint bar.** This uses no new UI. Rejected because the
  hint bar cannot point at a control or wait for an action.

## What this gives up

- The tour needs the example pair. A server installed without the repository
  cannot run "First steps". It stops at step 1 with the server's reason.
- Element IDs become part of a contract. Renaming a control means updating
  `tours.js`, and the targets test enforces that.
- Undo on step 1 deletes the example on the server, so other windows on the same
  session (ADR 0044) see it disappear as well. Accepted, because only data that
  the tour itself loaded is deleted.
- While a tour runs, the rest of the client is locked. A user who wants to try
  something else must skip the tour first.
