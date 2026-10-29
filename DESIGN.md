# Design

The look of the browser client (`ffast/renderers/web/`). Who it is for and what it
must keep is in [PRODUCT.md](PRODUCT.md). The reasons, the options that were
rejected and what each step changed are in
[ADR 0063](docs/adr/0063-the-web-client-looks-like-a-journal-figure.md). The Qt
desktop client is not covered.

The idea: every screen is a figure. A screenshot of it can go into a paper or a
talk as it is.

## Tokens

All colours and sizes are CSS custom properties on `:root` in `index.html`. Plotly
reads the same properties (`theme.js`), so the plots and the page cannot drift
apart. Do not write a colour or a size into a rule; use a token. Dark is the
default.

| Token | Dark | Journal page | Use |
|---|---|---|---|
| `--ground` | `#121417` | `#ffffff` | app base |
| `--surface` | `#1a1d21` | `#ffffff` | panels, rail, sidebar, top bar |
| `--surface-2` | `#22262b` | `#f3f4f6` | inputs, buttons |
| `--surface-3` | `#2c3137` | `#e6e8eb` | hover |
| `--line` | `#3a4047` | `#cfd4da` | hairlines, borders, grid lines |
| `--ink` | `#f2f3f5` | `#111418` | strong text, active tab |
| `--ink-2` | `#c3c8cf` | `#2b3036` | default text |
| `--ink-3` | `#9aa1ab` | `#59616c` | muted text, captions, axis ticks |
| `--accent` | `#56b4e9` | `#0072b2` | active, focus ring, selection, series 1 |
| `--accent-ink` | `#0b1a24` | `#ffffff` | text on an `--accent` fill |
| `--sel` | `rgba(86,180,233,0.18)` | `rgba(0,114,178,0.12)` | selected row |
| `--peak` / `--peak-ink` | `#d55e00` / `#ff9a55` | `#d55e00` / `#a84600` | peak tick / label text |
| `--median` | `#2bbf93` | `#00795a` | median and reference lines |
| `--warn` | `#e69f00` | `#9a6a00` | "edited" marks, warnings |
| `--danger` | `#ff7a6e` | `#c0392b` | destructive buttons, errors |

Only these pairs may be used for text; each passes 4.5:1 (WCAG). Series colours are
Okabe–Ito (`--series`, eight values, colour-blind safe). The old names (`--bg1`,
`--text1`, `--hl2`…) are aliases and are being retired.

## Type

`--scale` is 1 (Compact, the default), 1.15 (Comfortable) or 1.4 (Large, for
talks); the Density setting sets it. Every size is `calc(N px * var(--scale))`.

| Token | px at scale 1 | Use |
|---|---|---|
| `--fs-xs` | 11 | small marks |
| `--fs-s` | 12 | captions, labels, tables |
| `--fs-m` | 13 | body |
| `--fs-l` | 15 | top bar title |
| `--fs-xl` | 19 | the playback gear |
| `--fs-xxl` | 22 | empty-state title |
| `--fs-plot` | 11 | Plotly text |
| `--letter` | 17 | panel letters |

Font: the system stack. Panel letters: Georgia, bold. Numbers in tables, status
and peak labels use tabular figures. Headers are sentence case, weight 600; there
is no uppercase text and no letter-spacing.

## Panels read as a figure

- Every panel has a **letter** (a, b, c…) in its top-left corner, in reading order
  (by row, then column). It is recomputed whenever the grid is laid out, so edited
  and user-made tabs get it. In a 3D panel the letter sits over the picture.
- A plot has a one-line **caption** as the last row of its card:
  `<b>letter</b> | text`. Tables and 3D panels have none. A "[unit]" in a caption
  that names an energy or force unit is clickable, like the axis title.
- Letters and captions belong to the panel kind, never to one tab (`figure.js`).

## Peaks

A timeline with **one series** marks its five highest peaks (`peaks.js`): a short
`--peak` tick and a label turned on its side, `73 · 0.442 eV/Å`. Clicking a label
opens that frame; a frame you opened stays marked (dimmed, with ✓) until the page
closes. The labels stand above the plot, in its top margin, so the plot grows by
the room they need. Each timeline has a **Peaks** checkbox to hide them. With
several series there are no labels.

## Journal page

The Journal page switch (top bar) redefines the Journal column of the tokens on
the figure area (`.analysis-grid`), so the panels print white. The rail, top bar
and sidebar stay dark, and the 3D view is not changed. Plots are drawn again when
it changes. It is a per-browser setting, like Density, and is never saved in a
session.

## Icons

One stroke (1.5 px), `currentColor`, 16×16, drawn in `icons.js` and nowhere else.
No character stands in for a button's picture. Every icon button has an
`aria-label`.

## Keyboard

Tabs, rail rows and section headers are keyboard-operable. Dialogs keep focus
inside them, close on Esc and focus the safe button; a button that deletes is
outlined in `--danger`. Shortcuts: ←/→ frame, Space play, Esc puts down a pick
tool, / searches the settings.
