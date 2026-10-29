# Panel grid: one way to size a panel (implementation plan)

Decided with the user on 2026-10-28. Written for an agent running Sonnet 5.5 at
medium effort. Do the parts **in order**. **Part 2 starts only after the user
has tried the prototype and answered the questions in Part 1, Task 3.**

## Answers (2026-10-29)

1. **24 columns.** Every "12" in Part 2 becomes 24: `grid = 24`, `toFineGrid`
   shares 24 columns, `repeat(24, ...)`, minimum panel 6 × 4 (a 3D panel 8 × 6),
   `colspan 12` for half the width in the Task 4 test.
2. **50 px rows.** The user was unsure; 50 px chosen (finer height control).
   Changing it later is one constant.
3. **Edges + corner.**
4. **Compaction on.** Panels only float up, never sideways. Three panels in one
   row work: 8 + 8 + 8 columns (the minimum of 6 even allows four).
5. **Always fill the window width.** No fixed pixel widths.

## Why

In Edit mode a panel's size is set by two separate things:

- **column widths and row heights**, which you change by dragging the thin bars
  between columns or rows (`column_widths`, `row_heights` in the tab file);
- **how many cells a panel covers**, which you change by dragging the panel's ◢
  corner (`colspan`, `rowspan`).

Users want "make this panel bigger" and do not know which of the two to use.
With one column, the corner can only make a panel taller, so the panel looks
stuck. Commits 1f16e10 and bad2678 made both controls easier to see, but there
are still two of them.

The usual design in dashboard editors is a **fine, fixed grid**: for example 12
narrow columns that always fill the width. A panel covers some of those columns
and some fixed-height row units. You drag the panel's own right edge, bottom
edge or corner, and it snaps to the grid. There are no column-width bars and no
empty columns.

## Rules for the whole job

1. **Do not touch** files you did not change: `CONTEXT.md`, `docs/adr/README.md`
   (except in Part 2, Task 1), and anything untracked (`to_fix*.md`,
   `docs/research/...`, `docs/adr/0059-*`, `docs/adr/0068-*`,
   `docs/implementation/2026-10-28-to-fix-v2.md`). The user edits them. Stage
   with `git add <exact paths>`, never `git add -A` or `git add .`.
2. The user may commit while you work. Run `git status --short` and
   `git log --oneline -3` before each commit. Never `git reset --hard`, never
   rebase, never amend a commit you did not just make.
3. Commit messages: the subject starts with `feat:`, `fix:` or `docs:`, written
   in plain words about what the user sees (see `git log --oneline -20` for the
   style). Body: 2–4 short lines saying what changed and why. **No
   `Co-Authored-By` line.** One commit per task.
4. Tests run with the project's virtual environment, never `uv run`:
   ```
   ~/.venvs/ffast_env/bin/python -m pytest <file> -q
   ```
   `-n 6` is on by default (set in `pyproject.toml`). Add `-n 0` only to debug
   one test. Browser tests (`tests/ffast/renderers/web/`) take minutes; while
   working, run only the tests named in the task.
5. After each Part 2 task, run the fast suite. It must stay green:
   ```
   ~/.venvs/ffast_env/bin/python -m pytest -m "not integration" -q
   ```
   Before the last commit of Part 2, also run the full browser suite, prefixed
   with `caffeinate -i`:
   `caffeinate -i ~/.venvs/ffast_env/bin/python -m pytest tests/ffast/renderers/web -q`.
6. Match the surrounding code: a short JSDoc comment above each new function,
   2-space indent in JS, the same naming style. Comments say *why*, briefly.
7. **The Qt desktop app is being retired. Do not change `UI/`.** It reads the
   built-in tabs with their old `row/col/rowspan/colspan`. Part 2 keeps those
   files readable by it (see Part 2, Task 2).
8. If the plan does not match the code (a line moved, a name differs), find the
   right place by searching for the quoted code, not the line number. If the
   plan is wrong about behaviour, **stop and report**. Do not invent a
   different design.

Paths are relative to the repo root. `static/` means
`ffast/renderers/web/static/`.

---

# Part 1: a throwaway prototype

The goal is a page the user can open and play with, to decide how the new grid
should feel. It does not touch product code and is not wired to the server.

## Task 1: build the prototype page

**File:** `docs/prototypes/panel-grid/index.html`, one self-contained file with
inline CSS and JS, no libraries, no build step. It must work when opened
directly from disk (`file://`), so do not use ES module imports.

**What it shows:** one mock FFAST tab in Edit mode, with dark colours copied
from the `:root` tokens in `static/index.html` (`--ground`, `--surface`,
`--line`, `--ink`, `--accent`, `--hl2` and so on). Start with these panels.
Each panel is a box with a title bar and a grey body; a 3D panel's body is dark.

| Title | Kind | Start place (12-column grid, 50 px row units) |
|---|---|---|
| 3D panel | 3d | x 0, y 0, w 6, h 8 |
| Energy MAE distribution | density | x 6, y 0, w 6, h 6 |
| Forces RMSE timeline | timeline | x 6, y 6, w 6, h 6 |
| Energy MAE / RMSE tables | table | x 0, y 8, w 6, h 4 |

`x` and `w` count columns; `y` and `h` count row units.

**Behaviour:**

1. **Grid.** The grid fills the page width. Column width =
   (page width − gaps) / column count, so it changes when the window is
   resized. Rows have a fixed unit height. While a drag is in progress, faint
   grid lines show every column and row unit.
2. **Resize.** Each panel has three handles: the right edge (cursor `ew-resize`),
   the bottom edge (`ns-resize`) and the bottom-right corner (`nwse-resize`).
   The corner is drawn as a visible ◢ mark. Edges highlight on hover. Dragging
   shows a ghost box that snaps to whole columns and row units; letting go
   applies it. Minimum size: 3 columns × 4 row units (a 3D panel: 4 × 6).
   Maximum width: the whole grid.
3. **Move.** Dragging the title bar moves the panel. The ghost snaps to the
   grid. A panel cannot be dropped partly outside the grid on the left or right.
4. **Collisions.** A panel that would overlap another pushes the other one
   down, just below it. Do this repeatedly until nothing overlaps.
5. **Compaction (a switch, on by default).** After every change, each panel
   moves up as far as it can without overlapping, in order of `y`, then `x`.
   This removes empty rows. With the switch off, panels stay where they are put.
6. **Settings bar** at the top, so the user can compare choices:
   - Columns: 12 or 24.
   - Row unit: 50 px or 100 px.
   - Handles: "edges + corner" or "corner only".
   - Compaction: on or off.
   - A "Reset" button that restores the start layout.
   When the column count changes, scale each panel's `x` and `w` by the same
   factor so the layout looks the same. When the row unit changes, keep `y`
   and `h` in pixels as close as possible, rounding to whole units.
7. **Saved form.** A read-only box under the grid shows the layout as it would
   be saved, updated live, for example:
   ```
   columns = 12, row_unit = 50
   3D panel                 x=0 y=0 w=6 h=8
   Energy MAE distribution  x=6 y=0 w=6 h=6
   ```
8. **Narrow window.** Below 800 px page width, show panels in one column in
   reading order (`y`, then `x`), full width. Turn editing off and show a note:
   "Too narrow to edit; widen the window." This matches today's narrow mode in
   `static/analysis.js` (`_sizeGrid`).

Use pointer events with `setPointerCapture`, as `static/edit_mode.js` does in
`_dragOnto`. Keep the code readable; it is about 400–600 lines.

**Check:** open the page with Playwright from a short throwaway script (not a
committed test). Drag the 3D panel's right edge from 6 to 8 columns and confirm
the Saved form shows `w=8` and the density panel moved down below it. Take one
screenshot. Delete the script afterwards.

## Task 2: commit it

`git add docs/prototypes/panel-grid/index.html`, then commit with
`docs: a throwaway prototype of a one-control panel grid`.

## Task 3: hand over to the user, then stop

Tell the user how to open it:
`open docs/prototypes/panel-grid/index.html`. Then ask these questions,
**one line each**, and wait for the answers. Do not start Part 2.

1. 12 or 24 columns?
2. Row unit 50 px or 100 px?
3. Edges + corner, or corner only?
4. Compaction on (panels float up, no empty rows) or off?
5. Should a set layout keep fixed pixel widths, as today (ADR 0056 rule 12:
   "a column width of 1 is 400 px"), or always fill the window width, as in
   the prototype?

Write the answers at the top of this file, under a heading
`## Answers (date)`, and commit that with `docs:`.

---

# Part 2: build it into FFAST

Do this part only once the answers are recorded. The defaults below assume
**12 columns, 50 px rows, edges + corner, compaction on, fill the window
width**. If an answer differs, change the numbers accordingly. If an answer
changes the design itself (for example "keep fixed pixel widths"), **stop and
report** before coding.

## Facts about today's code

- **Tab file format** (pydantic, `ffast/config/models.py`): `PanelConfig` has
  `row`, `col`, `rowspan`, `colspan`. `AnalysisTabConfig` has `column_widths`
  and `row_heights`, and the validator `_sizes_fit_the_grid`.
- **Saving user tabs:** `ffast/config/user_tabs.py` (`_authoring` writes whole
  sizes as integers). `ffast/config/tabs.py` (around line 122) sends the tab to
  the browser.
- **Browser drawing:** `static/analysis.js`. `placeCell()` sets `gridColumn` and
  `gridRow` from `col/colspan/row/rowspan`. `_sizeGrid()` builds
  `gridTemplateColumns` from `column_widths` and switches to narrow mode.
  `_sizeRows()` sets row heights, including extra room for peak labels
  (`_extraHeight`). A `scroll_group` folds several panels into one horizontal
  strip at the first member's place.
- **Editing logic:** `static/tab_edit.js` holds pure functions on a draft tab
  (`movePanel`, `resizePanel`, `setColumns`, `emptyColumns`, `removeColumn`, …).
  `static/edit_mode.js` holds the gestures (◢ corner, divider bars, "✕ Empty
  column" buttons) and the Tab settings dialog (with its Columns field).
- **Protocol types:** `static/protocol.js` (around lines 233–258).
- **Defaults:** `static/tab_rules.js` (around line 25).
- **Built-in tabs:** `ffast/config/builtin_tabs/*.toml` use a 2-column grid
  (`colspan = 2` means full width).
- **Tests:** `tests/ffast/renderers/web/test_web_pure_helpers.py` (`te_*` cases
  test `tab_edit.js`), `tests/ffast/renderers/web/test_web_runtime.py`
  (search `edit_mode`, `edit-resize`, `edit-divider`, `edit-drop`),
  `tests/ffast/test_user_tabs.py`, `tests/ffast/test_analysis_tabs_config.py`.
- **ADR 0056 rule 12** (`docs/adr/0056-*.md`, around line 90) describes today's
  dividers and fixed sizes. It will be replaced.

## Task 1: write the decision record (ADR 0069)

File `docs/adr/0069-panels-size-on-a-fine-grid.md`. Copy the heading format of
`docs/adr/0067-*.md`. Content: the problem (two controls), the decision (the
recorded answers), what happens to old tab files (Task 2), and that it replaces
ADR 0056 rule 12. Add one line for it to `docs/adr/README.md`, matching the
other lines (this is the only allowed edit to that file). Commit with `docs:`.

## Task 2: the tab file gains a fine grid, and old tabs still load

**Format.** A tab may set `grid = 12`. With `grid` set:
- `col` and `colspan` count grid columns (0–11, 1–12);
- `row` and `rowspan` count row units (50 px each);
- `column_widths` and `row_heights` are not allowed (the validator rejects
  them, with a clear message).

Without `grid`, a tab is in the **old form** and means exactly what it means
today. Do not rewrite the built-in TOML files; the desktop still reads them.

**Conversion (old → fine), one pure function in `static/tab_edit.js`**,
`toFineGrid(tab)`:
- n = number of old columns (`columnsOf(tab)`). Each old column becomes
  `12 / n` fine columns, rounded so that the columns add up to 12 (for example
  n = 5 → widths 3, 2, 3, 2, 2). If `column_widths` is set, share the 12
  columns in proportion to the widths instead, with at least 1 each.
- Each old row becomes `round(height_px / 50)` units, where height_px is
  `row_heights[i] * 300`, or 300 px when not set (6 units).
- Panels keep their order and their `scroll_group`.
- Returns a new tab with `grid: 12`, without `column_widths` and `row_heights`.

The browser converts **when drawing** an old-form tab (so drawing uses one
code path) and **when entering Edit mode** (so a saved tab is always in the new
form). Built-in and project tabs on disk are not changed. Saving an edited
built-in tab already writes a user tab that replaces it (ADR 0056 rule 10), so
that copy is in the new form.

**Python:** add `grid: int | None = None` to `AnalysisTabConfig`. Extend
`_sizes_fit_the_grid`: with `grid` set, reject `column_widths`/`row_heights`
and reject any panel with `col + colspan > grid`. Keep `_authoring` as is. The
desktop ignores `grid`; it will draw a fine-grid user tab wrongly, which is
accepted because the desktop already ignores user tabs (ADR 0056 rule 17).
Check that this is still true by searching `UI/` for where user tabs are read;
if the desktop does read them, **stop and report**.

**Tests:**
- `tests/ffast/test_analysis_tabs_config.py`: a fine-grid tab loads; a
  fine-grid tab with `column_widths` is rejected; a panel past column 12 is
  rejected; an old-form tab still loads unchanged.
- `test_web_pure_helpers.py`: `toFineGrid` on the `TE()` fixture (2 columns →
  6 + 6), on 5 columns (3, 2, 3, 2, 2), on `column_widths: [2, 1]` (8 + 4), and
  on `row_heights: [1, 0.5]` (6 + 3 units).

Commit: `feat: a tab can lay out its panels on a fine 12-column grid`.

## Task 3: draw the fine grid

In `static/analysis.js`, for a tab with `grid` set (after conversion every tab
has it):
- `_sizeGrid`: `gridTemplateColumns = repeat(12, minmax(0, 1fr))`.
  Keep narrow mode: below `2 * COLUMN_MIN + GRID_GAP` px, show one column in
  reading order, as today.
- Rows: `grid-auto-rows: 50px` for the fine grid. A panel's height is
  `rowspan` units plus the gaps between them. Keep the peak-label extra room
  from `_sizeRows`: add it to the rows the panel covers, as today, but count in
  units.
- `placeCell` needs no change (it already uses `col/colspan/row/rowspan`).
- The built-in "3D" tab (one panel, whole height) must still fill the height.
  Search `_layoutGrid`'s comment for "single cell gives it the whole height"
  and keep that behaviour.

**Tests:** run every test in `test_web_runtime.py` whose name contains `tab`,
`grid`, `panel`, `narrow` or `row` (use `-k`). Update expected pixel sizes only
when the new grid explains the change; write in the test's docstring why.

Commit: `feat: tabs are drawn on a 12-column grid that fills the window`.

## Task 4: edit with the panel's own edges

In `static/tab_edit.js`:
- Replace `resizePanel(tab, index, rowspan, colspan)` with
  `resizePanel(tab, index, { w, h })`: clamp to the minimum (3 × 4; a 3D panel
  4 × 6) and to `grid - col`. Then push overlapping panels down and compact
  (see the prototype, Behaviour 4 and 5). Keep scroll strips moving as one unit
  (`unitOf`).
- `movePanel(tab, index, row, col)`: the same push-down and compaction. Drop
  the swap rule.
- Add `compact(tab)`.
- Delete what the fine grid makes unnecessary: `setColumns`, `fitWidths`,
  `dragTrack`, `emptyColumns`, `emptyRows`, `removeColumn`, `removeRow`,
  `MAX_COLUMNS`, and the column/row parts of `syncRows`. Search for each name
  before deleting it and remove every use.

In `static/edit_mode.js`:
- Each panel gets three handles: right edge, bottom edge, ◢ corner (styles in
  `static/index.html`, next to `.edit-resize`). Drag shows the ghost
  (`_dragOnto` already does this); the ghost snaps to the fine grid.
- Remove the divider bars (`_dividers`, `_dragDivider`, `.edit-divider`), the
  "✕ Empty column/row" buttons (`.edit-drop`), and the Columns field of the Tab
  settings dialog (`_tabSettings`). Keep `cornersInView` and apply it to the
  edge handles too, so they stay reachable when the tab scrolls.
- While dragging, show faint grid lines (one background on the grid,
  for example `repeating-linear-gradient`), as in the prototype.
- New tab (`emptyTab`): `grid: 12`, no `column_widths`.

**Tests:**
- Replace the `te_*` cases about columns, dividers and empty tracks with cases
  for `resizePanel`, `movePanel` and `compact` on the fine grid (push down,
  compaction, minimum size, a scroll strip moving as one).
- In `test_web_runtime.py`, rewrite these to drag the new handles:
  `test_web_edit_mode_plots_follow_their_cells_while_dragging`,
  `test_web_edit_mode_resize_corner_stays_in_view`,
  `test_web_edit_mode_corner_adds_a_column_and_empty_ones_go`. Add one test:
  in a converted one-column tab (`column_widths = [1.5]`, two 3D panels),
  dragging the first panel's right edge to half the width gives `colspan` 6,
  and Save writes `grid = 12` with no `column_widths`.
- Search the tests for `column_widths`, `row_heights`, `edit-divider`,
  `tab-settings` and `data-field=columns`; update every hit that tests removed
  behaviour.

Commit: `feat: a panel is resized by dragging its own edges`.

## Task 5: update the documents

- `docs/adr/0056-*.md`: under rule 12, add one line: "Replaced by ADR 0069."
  Do not rewrite the rule.
- `static/edit_mode.js` file comment (top of the file): describe the new
  handles; remove the dividers.
- `static/tab_edit.js` file comment: describe the fine grid.
- The `AnalysisTabConfig` docstring in `ffast/config/models.py`: describe
  `grid` and say that `column_widths`/`row_heights` are the old form.
- Delete `docs/prototypes/panel-grid/` (`git rm`). It was throwaway.

Run the full browser suite (Rule 5), then commit:
`docs: describe the fine panel grid and drop the prototype`.

## Done when

- A panel is resized only by its own edges or corner, and moved by its title.
- No column-width bars, no empty-column buttons, no Columns field.
- Old tab files (built-in, project, the user's `~/.ffast/tabs/3d-compare.toml`)
  still open and look about the same. Editing and saving one writes `grid = 12`.
- Fast suite and full browser suite are green.

Report back with the commit list, any test whose expected sizes you changed
(and why), and anything you stopped on.
