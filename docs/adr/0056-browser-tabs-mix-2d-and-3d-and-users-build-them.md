Status: Accepted — not yet implemented. Decided 2026-10-07 in a design interview.

# Browser tabs mix 2D and 3D panels, and users build their own tabs

The request: "I want the 3D view and other 2D plots to be able to exist in the
same tab, tabs to be very flexible about which 2D and 3D we add, and adding new
tabs to be possible."

What the browser client does today:

- **3D is a fixed tab.** The first tab holds the one 3D view (the "Loupe"). It
  cannot be edited or removed. To look at a structure behind a plot point you
  click the point, the page switches to that tab, and you switch back for the
  next point.
- **Analysis tabs come only from TOML.** The server builds them from
  `ffast/config/builtin_tabs/` plus a project's `[[visualization.tabs]]`
  (`merge_tabs`) and sends them as `TAB_LAYOUT`. Nothing in the browser can add,
  change or remove a tab.
- **The grid is fixed.** Columns are equal width, rows are at least 300 px, and
  panels are placed by `row`/`col`/span.
- **A plot click ignores the clicked curve.** It moves the 3D view to the clicked
  point's frame *number*. With ethanol curves on screen and aspirin in the 3D
  view, clicking ethanol point 412 moves aspirin to frame 412: a different
  molecule, with nothing on screen saying so.

## Decision

Browser only. The Qt desktop gets one small change (point 17).

### What a tab can hold

1. **A tab may mix 2D panels and 3D panels.** A 3D panel is a grid cell like any
   other panel. It is **linked** by default: it shows the main view, the one 3D
   view the rail drives, so every linked panel in every tab shows the same thing.
   The user can switch a 3D panel to **independent**.
2. **An independent panel has its own data, colouring and display.** Its frame
   and its camera follow the main view through two "link" checkboxes, ticked by
   default. Ticked, side-by-side comparison needs no effort: play, step and orbit
   once. Unticked, you can compare frame 10 with frame 500 of one trajectory.
3. **A linked frame means the same structure, not the same number.** Where both
   sides contain the configuration (a prediction and its dataset, a subset and
   its parent), the link matches the configuration. Otherwise it matches the
   frame number. When there is no counterpart, the panel greys out and says so
   ("No frame 1200 (ethanol has 300)") instead of showing a different structure.
4. **A plot click moves the 3D panel in this tab that shows the clicked curve's
   data,** or the main view if no panel there does. The frame is translated by
   rule 3. This also fixes the wrong-dataset jump described above: when the
   main view holds no frame with the clicked configuration (an unrelated
   dataset, or a subset that left it out), the main view and the rail switch
   to the clicked curve's dataset and prediction, so the structure on screen is
   always the one clicked.
5. **If the moved view is not in this tab, the page switches** to the most
   recently used tab with a linked 3D panel, as it does today.
6. **The fixed 3D tab becomes an ordinary built-in tab** named "3D", holding one
   linked 3D panel that fills it. It can be edited and renamed. The one rule:
   the last linked 3D panel cannot be removed ("At least one tab must show the
   main view"), so a plot click and the empty-state Load Dataset… button (ADR
   0055) always have somewhere to appear.
7. **An independent panel has its own dataset/prediction picker.** A new one
   starts on the first pair the tab's picker has selected that no other 3D panel
   in the tab shows. After that it keeps its choice when the tab's picker
   changes.
8. **One set of 3D controls per tab, acting on the focused panel.** Clicking a
   3D panel focuses it (outlined). The tab's settings sidebar, pick toolbar and
   playback strip act on that panel, and the sidebar title names it
   ("Settings — aspirin · MACE (independent)"). A tab without 3D panels shows
   none of these. ADR 0055's sidebar layout is unchanged. A linked panel's
   title reads "Settings — aspirin · MACE (main view)". With several 3D panels
   in a tab, the focused one is outlined; which one is focused is browser
   layout state, remembered per tab.

### Building and saving tabs

9. **User tabs are files on the server machine:** one TOML file per tab in
   `~/.ffast/tabs/`, in the same format as the built-in tabs. The server reads
   them with the built-in and project tabs; the browser asks it to save or delete
   one. An Export button gives the TOML, to paste into a project's `ffast.toml`
   or send to someone. A tab moves between built-in, project and user by copying
   a file.
10. **Editing a built-in or project tab saves a user tab that replaces it,**
    marked "edited", with "Reset to original", which deletes the user tab. When
    the original has changed since the edit, the mark says so. Any tab can be
    hidden from the tab bar; the hidden list is kept with the user tabs.
11. **Adding a panel offers ready-made panels first** (every panel in any
    built-in or project tab, plus "3D panel"), **then "Custom panel…"**: choose a
    panel kind, then a metric per role from the metric catalog, filtered by
    shape. Any panel can be opened in this builder. For now the builder offers
    only metrics the server already has; compiling a new transform on a running
    server is later work.
12. **Arranging is by dragging.** In Edit mode you drag panels to move them and
    drag their edges to change spans, and you drag the dividers between columns
    and rows. The tab file gains relative `column_widths` and `row_heights`. Rows
    with set heights share the window's height; a tab without them scrolls as
    today. A list may be longer than the panels reach, for columns or rows
    left empty, never shorter; a shorter one is a config error, like any
    other.
13. **A tab file stores layout and starting settings, never data.** For an
    independent panel it stores linked/independent, the two link ticks, and its
    starting colouring, display, bonds and force arrows. It never stores which
    dataset or prediction is shown (rule 7 picks it each time) or the camera. A
    linked panel stores only its place. If a starting colour metric is missing on
    the server, the panel uses element colours and says why.
14. **"+" offers Empty tab, Copy of current tab, and Copy of… any tab.** A
    Tab settings dialog sets the name, the column count and the tab controls
    (today energy shift and the element picker).
15. **Tabs change only in Edit mode.** A ✎ button enters it. Only there can you
    add, remove, move or resize panels, use the builder, change tab settings, or
    "Use current 3D settings as start". Save writes the file; Cancel drops the
    edits. Outside Edit mode nothing you do is written, so exploring (recolouring,
    toggling bonds) never rewrites the tab.
16. **A save reaches every connected window** (ADR 0044), the same way a newly
    loaded dataset does. A window in Edit mode on the same tab is warned on Save:
    "Compare was changed in another window — overwrite or discard your edits?"
17. **The desktop, for now, ignores user tabs.** For a project tab that contains
    3D panels it draws the 2D panels, puts a grey "3D panel — shown in the
    browser only" box in each 3D panel's place, and ignores `column_widths` and
    `row_heights`. A tab made only of 3D panels, the built-in "3D" tab among
    them, it skips: it would be nothing but grey boxes, and the desktop has its
    own 3D window.

Defaults taken without a separate decision: the "⧉ New Tab" pop-out acts on the
focused panel; 3D panels in tabs you are not looking at stop drawing; renaming a
tab carries its Panel Display Overrides (ADR 0029, keyed by tab name) along.

User tabs are kept like this (`ffast/config/user_tabs.py`). Each tab file
holds exactly the shared format; a file named after the tab, `compare.toml`.
Which tab a user tab replaces, the original's fingerprint at edit time, the
hidden list and the order of new user tabs are app state in a side file,
`~/.ffast/tabs/state.json`, so a tab file can be copied into a project as it
is. New user tabs follow the built-in and project tabs in the order they were
made. A name another tab already has is refused, so tab names stay unique. A
tab may only use metrics the running server already has; anything else is
refused at Save. Export gives `[[visualization.tabs]]` TOML for a project's
`ffast.toml`; dropped into `~/.ffast/tabs/` as it is, it loads as a user tab
too. The browser saves, deletes, hides and exports over `SAVE_TAB`,
`DELETE_TAB`, `HIDE_TAB` and `EXPORT_TAB`; a read-only window may only export.
Export, Reset to original, Delete, Hide and the hidden tabs to show again are
in a ⋯ menu at the right end of the tab bar, acting on the tab on screen.

Edit mode looks like this. ✎, + and ⋯ sit at the right end of the tab
bar. While a tab is edited, an edit bar (+ Add panel, Tab settings…, Cancel,
Save) takes the place of its controls row, and each panel gets a handle strip
laid over it, the plots staying live underneath: drag ⠿ to move it, drag the
corner ◢ to change its span, ⚙ opens it in the builder, ✕ removes it.
Dropping a panel on another swaps the two; each keeps its size where it fits
and shrinks where it does not. Growing a panel over others moves them down,
each to the first free place at or below its row, keeping its size. Panels sharing a scroll strip move and resize
as one. Dragging the dividers between columns or rows sets `column_widths` or
`row_heights`; Tab settings has "Rows share the window height", and unticking
it drops `row_heights` so the tab scrolls again. "+" opens Tab settings
(name, columns, controls) before the new tab opens in Edit mode, and the tab
exists only once saved. The builder's panel kinds, their roles and the metric
shapes each role takes mirror the desktop's panel kinds (`tab_edit.js`
`KIND_ROLES`; a test keeps them in step).

In a tab file a 3D panel is `kind = "3d"`, with no `metrics`. The built-in
"3D" tab is `ffast/config/builtin_tabs/00_3d.toml`, first in the bar.

To match configurations (rule 3), each dataset's announcement
(`REMOTE_DATASET_META`) names its `parent` and, for a frame subset,
`parent_frames`: frame i of the subset is frame `parent_frames[i]` of the
parent. An atom subset keeps every frame of its parent.

### Subsets from plots (decided during the step 6 trial)

The browser's **Sub** box works as the desktop's does. Ticking it on a
timeline, scatter or distribution plot makes a subset of each series the plot
draws: the configurations inside the plot's visible range. It follows every
zoom and pan, and unticking hides it. On a distribution the subset is the
configurations whose value falls in the visible range; on a force scatter,
the configurations of the points in view. The browser sends the plot's view in
`DECLARE_SUBSET` (`view`, or `active = false` to hide); the server works out the
frames (`ffast/session/subbing.py`, the desktop's rules) and announces the
subset again whenever its frames change or it is hidden. This replaces the
browser's earlier drag-a-box subbing. A subset is named after its plot.

## Alternatives rejected

- **One shared 3D view only (no independent panels).** Cheapest, but it rules out
  the reason to put two 3D panels side by side: reference and prediction, each
  coloured by its own force error.
- **Independent panels with everything separate, frame and camera included.**
  Comparing frame 120 would mean moving both panels and orbiting both cameras by
  hand.
- **Linking frames by frame number only.** A subset's frame 0 is its parent's
  configuration 1000; linking by number would show two different molecules side
  by side with no warning.
- **Independent panels taking their data from the tab's picker by position**
  ("panel 2 = second selected"). Unticking one prediction shifts every panel.
- **Controls inside every 3D panel**, as a sidebar or as a drawer over the
  molecule. A 500 px panel loses half its width to controls, or the molecule is
  covered: the clutter and the floating panels ADR 0055 removed.
- **Keeping the fixed 3D tab.** It would be the one tab that follows different
  rules, and you could not put a plot under the large 3D view.
- **User tabs in browser storage.** Lost with site data, tied to one browser,
  not shareable. It also does not survive a relaunch today (see below).
- **Writing user tabs into the project's `ffast.toml`.** The app would rewrite a
  hand-written file and lose its comments, which ADR 0029 forbids, and there is
  no file to write when there is no project.
- **Duplicate-only editing of built-in tabs.** It leaves two near-identical tabs
  in the bar.
- **Saving every change at once with undo.** A moment of "colour by element"
  while exploring, or an accidental drag, would become the tab's saved start.
- **Storing the shown dataset or prediction in the tab file.** The tab would
  break for anyone else, or after the dataset is reloaded.

## What this gives up

- **The fixed, always-first 3D tab.** Users who relied on its position will find
  a "3D" tab that can be moved or edited.
- **New server surface.** Saving, deleting and broadcasting user tabs are new
  protocol messages, and `column_widths`, `row_heights` and the 3D panel kind are
  new tab-file fields. Tab files are meant to be shared, so these fields are
  hard to take back once people have them.
- **Laptop and cluster tabs differ.** User tabs live on the server's machine.
  Export and copy is the bridge.
- **Server cost per independent panel.** Each one is its own visualization view,
  so the server builds a scene for each.
- **The desktop shows empty boxes** for 3D panels in shared project tabs.
- **One more mode.** Changing a tab takes the ✎ click; that is the price of
  exploring safely.

## Found while deciding: browser storage is lost on every launch

The launcher picks a new free web port each launch
(`ffast/renderers/web/launcher.py`, `pick_free_port`). Browser storage belongs
to the page's address, port included, so ADR 0055's layout state (open section,
hidden sidebar, recent servers, dismissed hints) starts empty every time. This
decision does not depend on it, because user tabs live on the server, but it is
fixed first, as its own commit, so that layout state is remembered as ADR 0055
intended.

## Build order

One commit per step, so each can be reverted alone:

1. Fixed default web port, so browser storage survives a relaunch.
2. 3D panel kind in the grid; the fixed 3D tab becomes the built-in "3D" tab with
   one linked panel; the last-linked-panel rule.
3. Focused panel with per-tab controls; plot-click routing (rules 4 and 5).
4. `column_widths` and `row_heights`.
5. Server-side user tabs: read, save, delete, broadcast, replace, hide, export.
6. Edit mode: the "+" menu, arranging by drag, the panel list and builder,
   Save/Cancel, the conflict warning.
7. Independent panels: own view and picker, frame and camera links, matching by
   configuration, starting settings.
8. Desktop placeholder for 3D panels.
