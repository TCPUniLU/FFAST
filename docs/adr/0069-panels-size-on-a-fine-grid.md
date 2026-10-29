# A panel is sized by its own edges, on a fine grid that fills the window

Status: Accepted 2026-10-29. Replaces rule 12 of ADR 0056.

## The problem

In Edit mode a panel's size came from two controls. The thin bars between
columns or rows set `column_widths` and `row_heights`. The ◢ corner set how
many cells the panel covers (`colspan`, `rowspan`). People wanted "make this
panel bigger" and did not know which to use. In a one-column tab the corner
could only make a panel taller, so the panel looked stuck. Two small fixes
(1f16e10, bad2678) made both controls easier to find, but there were still two.

## The decision

A tab is laid out on a **fine, fixed grid**: 24 columns that always fill the
window width, and rows of 50 px. A panel covers some columns and some row
units. You drag the panel's own right edge, bottom edge or ◢ corner, and it
snaps to the grid. You drag its title bar to move it.

- **Always fills the window.** There are no fixed pixel widths and no
  column-width bars. A tab never scrolls sideways. Below the width where two
  columns of the smallest plot fit, panels stack in one column, as before.
- **Minimum size** 6 columns × 4 rows (a 3D panel 8 × 6). Three panels fit in a
  row (8 + 8 + 8); four would (6 each).
- **Overlaps push down.** A panel put on top of another pushes it down, just
  below. Then every panel floats up as far as it can ("compaction"), so a tab
  has no empty rows. Panels move up, never sideways.
- **The tab file** gains `grid = 24`. With it, `col`/`colspan` count grid
  columns and `row`/`rowspan` count 50 px units, and `column_widths` and
  `row_heights` are refused.

## Old tab files

A tab without `grid` is in the old form and means what it always meant. The
built-in tabs stay in the old form, because the Qt desktop still reads them.
The browser converts an old tab when it draws it, and when Edit mode opens it
(`toFineGrid` in `tab_edit.js`): each old column gets an equal share of the 24
columns (or a share in proportion to its `column_widths`), and each old row
gets `row height × 300 px / 50` units. A tab saved from Edit mode is always in
the new form. The Qt desktop ignores user tabs (ADR 0056 rule 17), so it never
sees a fine-grid tab.

## What this gives up

- A set pixel size. A tab can no longer say "this column is 400 px"; on a wide
  screen the panels get wider, on a narrow one narrower.
- Free placement. Compaction means a panel cannot be left with an empty row
  above it; a gap in the layout has to be made by a panel.
- Converted tabs look only about the same: widths and heights are rounded to
  the grid.
