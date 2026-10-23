Status: Accepted 2026-10-22, built 2026-10-22 (branch `figure`). Proposed 2026-10-21.
Decided after the design critique of the web client
(score 20/40) and the direction round that followed. The critique, the sketches
and the design brief are not in git (`.impeccable/` is ignored); they live only in
the main checkout:

- critique: `/Users/acharkin/GitHub/FFAST/.impeccable/critique/2026-10-21T20-22-34Z__ffast-renderers-web-static-index-html.md`
- sketches the user approved, in `/Users/acharkin/GitHub/FFAST/.impeccable/mocks/decision/`:
  `fp-analysis.html`, `fp-3d.html`, `fp-journal.html` (static HTML, 1440×900;
  open them with Playwright or a browser; their data is invented)

Everywhere below, a sketch named by file name means that folder.

# The web client looks like a journal figure, and its timelines label their worst frames

## The problem

The critique found five problems in the browser client
(`ffast/renderers/web/static/`):

1. **Numbers without units or precision.** Axes said "Energy MAE (energy)"; tables
   rounded 0.0134 to "0.01". Fixed separately (branch
   `worktree-agent-aeefd8ebb4778e630`: units are dropped when unknown, then shown
   as an editable `[unit]`; 3 significant figures). This ADR builds on that branch.
2. **Keyboard and screen readers cannot reach the main controls.** Tabs, rail rows
   and sidebar section headers are click-only `div`s. There are no shortcuts.
3. **Dialogs default to the risky choice.** `askDialog` focuses its last button,
   which is Delete. The file browser ignores Esc. No dialog keeps focus inside it.
4. **Error colour depends on hue alone.** `force_error` runs blue → green → yellow
   → red, which colour-blind users cannot read. The colour bar sits in a
   collapsible sidebar section, away from the molecule.
5. **Weak contrast, stale status, breaks at 1024 px.** Muted text is 2.8–3.9:1
   (minimum 4.5:1). "Loading x…" stays after a dataset loads. At 1024 px the
   plot grid scrolls sideways.

The look is also generic: the CSS says it copies "Discord-grey surfaces, teal
accent". Every size is 1.4× an older scale (body 18.2 px), and every header is in
capitals. Nothing in the look says what FFAST is for.

`PRODUCT.md` (repo root) records who the users are and what the look must keep:
dark stays the default, and screenshots must work in papers and talks.

## The decision

### 1. The screen is a figure

Every panel in a tab gets a **letter** (a, b, c…) in its top-left corner and a
one-line **caption** under it. The letters follow screen reading order. A light
**Journal page** mode turns the panel area into a white printed figure for
screenshots. This attaches to panel kinds, never to one tab, so user-made and
edited tabs get it too.

### 2. Timelines label their worst frames

A timeline with one series marks its 5 highest peaks with a short vermillion tick
and a label turned on its side: `73 · 0.442 eV/Å` (or `73 · 0.442 [unit]` when the
unit is unknown). Clicking a label opens that frame, as clicking the curve does
today. A frame you have opened stays marked (dimmed, with ✓) for the rest of the
page's life.

### 3. One set of colour and size tokens

All colours and sizes come from CSS custom properties on `:root`. Plotly reads the
same properties, so the plots and the page can never drift apart, and Journal page
works by swapping the properties. Values are fixed in **Tokens** below; every
pair used for text passes 4.5:1 (checked).

### 4. Compact by default, larger on request

Default type is compact (13 px body). A **Density** setting (Compact, Comfortable,
Large) scales every size by 1, 1.15 or 1.4. Large equals today's size, for talks.
Stored per browser in `ffast.layout` (`layout_state.js`), never in a session file.

### 5. Error colour reads without hue

A new `cividis` colormap (blue-grey → yellow, safe for colour blindness and
readable in greyscale) becomes what the **Force error** quick style uses. The
`force_error` map stays in the list so old sessions still load. The colour bar
moves onto the 3D view, bottom-left.

### 6. Keyboard, dialogs and small fixes

Tabs, rail rows and section headers become keyboard-operable. Four shortcuts. Every
dialog keeps focus inside it, closes on Esc, and focuses the safe button. Status
says when a dataset is ready. The grid drops to one column when narrow.

## Considered options

- **Chart Paper** (spectrum-printout grid, peak picking as the whole identity). The
  dice assigned it; the user preferred Figure Plate and kept its peak labels.
- **Star Atlas** (error as dot size, navy ground). Readable without colour, but
  chemists do not recognise it.
- **Graphite Console** (today's look, tidied). Rejected: reads as developer tooling.
- **Keep the current look, fix only the critique items.** Rejected by the user,
  who asked for a distinct identity.

## What it gives up

- **No "Copy figure" button.** The sketches show one; copying a multi-plot figure
  to the clipboard needs a canvas composite and is left out. Export PNG (3D) stays.
- **Atoms are not ringed by error** as in `fp-3d.html`; they are filled by the
  colormap, as today. Rings need a second mesh per atom.
- **Journal page does not change the 3D background.** The existing Publication
  quick style does that; the two are separate on purpose (one is per-browser UI,
  the other a shared view setting).
- **Peak labels only on single-series timelines.** With several series the labels
  would overlap.
- **The Qt desktop client is not restyled.**

## Tokens

Write these in the first `:root` block of `index.html`, replacing today's palette.
Keep the old names as aliases (`--bg1: var(--ground);` …) so nothing breaks in
step 1; later steps may use either name.

| Token | Dark (default) | Journal page | Use |
|---|---|---|---|
| `--ground` | `#121417` | `#ffffff` | app base, 3D-less panel ground |
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
| `--peak` | `#d55e00` | `#d55e00` | peak tick |
| `--peak-ink` | `#ff9a55` | `#a84600` | peak label text |
| `--median` | `#2bbf93` | `#00795a` | median and reference lines |
| `--warn` | `#e69f00` | `#9a6a00` | "edited" marks, warnings |
| `--danger` | `#ff7a6e` | `#c0392b` | destructive buttons, errors |

Contrast checked (WCAG): `--ink-3` on `--surface-3` 5.0:1, `--accent` on
`--surface-3` 5.7:1, `--peak-ink` 6.3:1, `--danger` 5.2:1; Journal `--ink-3` 6.3:1,
`--accent` 5.2:1, `--peak-ink` 5.9:1. Only these pairs are allowed for text.

Series colours (Okabe–Ito order, replaces `SERIES_COLORS` in `panels.js`):
`#56b4e9, #e69f00, #2bbf93, #f0e442, #cc79a7, #d55e00, #8fc3ff, #c3c8cf`.
Journal page: `#0072b2, #e69f00, #009e73, #b8a900, #cc79a7, #d55e00, #56b4e9, #59616c`.

**Sizes.** `--scale` is 1 (Compact), 1.15 (Comfortable), 1.4 (Large).

| Token | Value | Replaces today's px |
|---|---|---|
| `--fs-xs` | `calc(11px * var(--scale))` | 11.2, 12.6 |
| `--fs-s` | `calc(12px * var(--scale))` | 14, 15.4 |
| `--fs-m` | `calc(13px * var(--scale))` | 13, 16.8, 18.2 (body) |
| `--fs-l` | `calc(15px * var(--scale))` | 19.6, `#topbar h1` |
| `--fs-xl` | `calc(19px * var(--scale))` | 26.6 (`#playback-gear`) |
| `--fs-xxl` | `calc(22px * var(--scale))` | 25.2 (`#overlay .big`) |
| `--fs-plot` | `calc(11px * var(--scale))` | Plotly font size |
| `--letter` | `calc(17px * var(--scale))` | panel letters |

Font: `--font` stays the system stack. Panel letters use
`Georgia, "Times New Roman", serif`, bold. Numbers in tables, status and peak
labels use `font-variant-numeric: tabular-nums`.

Remove every `text-transform: uppercase` and its `letter-spacing` (tabs, section
titles, sub-toggle, analysis-control labels, dialog titles, pick-strip tool,
panel-list heads). Headers get `font-weight: 600` instead.

## Rules for building it

These are for whoever implements the steps, including a model working alone.

- **Start from the units branch.** Step 0 merges it. Do not start step 1 before.
- **One commit per step**, so each can be reverted alone. Subjects start with
  `feat:`, `fix:`, `refactor:` or `docs:` as each step says. Stage files by name
  (`git add <files>`), never `git add -u` or `-A`. No `Co-Authored-By` trailer.
- **Work on a branch in its own worktree**:
  `git worktree add ../FFAST-figure -b figure main`. Put `PYTHONPATH=$PWD` in front
  of every test command there, and check once that
  `PYTHONPATH=$PWD ~/.venvs/ffast_env/bin/python -c "import ffast; print(ffast.__file__)"`
  prints a path inside the worktree. Merge to `main` only when the user says so.
- **Shell is zsh**: quote globs.
- **Tests**, with the project venv, not `uv run`:
  - one file: `~/.venvs/ffast_env/bin/python -m pytest <file> -q`
  - before each commit: `~/.venvs/ffast_env/bin/python -m pytest -m "not integration" -q`
  - every step also runs the browser suite:
    `caffeinate -i ~/.venvs/ffast_env/bin/python -m pytest tests/ffast/renderers/web -q`
    (about 10 minutes)
  - full suite after the last step: `caffeinate -i ~/.venvs/ffast_env/bin/python -m pytest -q`
- **Unit tests for browser modules** follow `tests/ffast/renderers/web/test_web_pure_helpers.py`
  (static server, blank page, `import()` inside `page.evaluate`). Tests against a
  real page follow `test_web_runtime.py` (the `ffast_web_server` fixture).
- **Keep every id, class, `data-*` attribute and visible label the tests select
  on** (`test_web_runtime.py` uses `.obj-row`, `.tab`, `.pane-header`,
  `#sidebar-toggle`, `#colorbar`, `.hint-n`, `.sub-toggle`, `[data-title]`…). Add
  classes; do not rename. When a step changes a visible text a test asserts (a
  glyph, "Settings ▸"), update that assertion in the same commit and say so in the
  commit body.
- **Look at the result.** After each visual step, run the app
  (`~/.venvs/ffast_env/bin/ffast-web --no-browser --web-port 8790`, then open
  `http://127.0.0.1:8790/?port=<ws port from its log>`), load
  `examples/data/dataset.xyz` and `prediction.xyz`, and take Playwright
  screenshots at 1440×900 and 1024×768. Compare with the sketches named in the
  step. Stop the server afterwards.
- **No design decisions.** Every colour, size and label is in this ADR. If
  something is not covered, use the nearest token and write the question in the
  commit body. A step that seems to need a behaviour change not listed here stops
  and asks.
- **Line numbers drift**; find code by function name.

## Steps

### Step 0. Merge the units branch (no commit of its own)

`git merge worktree-agent-aeefd8ebb4778e630` into `figure`. It holds 5c62f7a (no
dimension tag printed as a unit), 3740dd8 (3 significant figures) and 3470b1a
(editable per-dataset units, `SET_DATASET_UNIT`). It is based on 3132aa4; a trial
merge into `main` at 92daa7b had no conflicts (`git merge-tree`). If `main` has
moved and a conflict appears, keep both sides. Run the quick suite and the browser
suite; both must pass before step 1.

What steps 4 and 5 reuse from it (`units.js`, `dialogs.js`):
- `withUnit(text, unit)` gives `"text (eV)"`, `"text [unit]"` or `"text"`; use it
  for every label that carries a unit (captions, peak labels use `displayUnit`).
- `displayUnit(unit)` gives `"eV"` or `"[unit]"`; `UNIT_PLACEHOLDER` is `"[unit]"`.
- `editableDimension(unit)` says whether a label's unit can be named; the class
  `unit-editable` marks such labels, and a click on one opens `unitPopover(...)`
  (see how `panes/colorby.js` wires `#colorbar-label`). Peak labels are Plotly
  annotations and are not clickable for units; the axis title already is.

### Step 1. Tokens, sizes and sentence case (`feat:`)

Files: `ffast/renderers/web/static/index.html` (CSS only).

- Replace the `:root` palette with the **Tokens** table (dark column) plus aliases
  for every old name (`--bg1`→`--ground`, `--bg2`→`--surface`, `--bg3`→`--line`
  for borders, `--bg4`→`--surface-3`, `--text1`→`--ink-2`, `--text2`→`--ink`,
  `--text-hover`→`--ink`, `--hl1`→`--line`, `--hl2`→`--accent`,
  `--muted`→`--ink-3`, `--success`→`--accent`). Define the missing `--text` used by
  `.elem-btn.active.following` as `var(--ink-2)`.
- Add `--scale: 1` and the size tokens. Replace every px `font-size` per the table.
  `body` gets `font-size: var(--fs-m)`.
- Buttons and inputs: background `--surface-2`, text `--ink-2`, hover
  `--surface-3`. A filled active state (`button:active`, `.pick-tool-btn.active`,
  `.elem-btn.active`) uses `--accent` with text `--accent-ink`, never white.
- Placeholder text: `::placeholder { color: var(--ink-3); }`.
- Remove every `text-transform: uppercase` and its `letter-spacing`; set
  `font-weight: 600` on those headers.
- Browser surfaces: `::selection { background: var(--sel); color: var(--ink); }`,
  `caret-color: var(--accent)` on inputs, scrollbar thumb `--surface-3`, hover
  `--accent`, focus ring `:focus-visible { outline: 2px solid var(--accent);
  outline-offset: 1px; }` on buttons, inputs, selects, `[tabindex]`.
- `#overlay` text uses `--ink-3`.
- `.obj-row.selected`: `background: var(--sel)`, no coloured `border-left`; the
  name turns `--ink` and `font-weight: 600`. Remove the 2px border-left rule.
- Look: compare with `fp-analysis.html` for colours, case and size.
- Tests: none new. `test_web_input_boxes_are_dark` and
  `test_web_text_controls_expand_instead_of_clipping` must still pass.

### Step 2. Plots read the tokens (`feat:`)

Files: new `ffast/renderers/web/static/theme.js`, `panels.js`,
`tests/ffast/renderers/web/test_web_pure_helpers.py`.

- `theme.js` exports `plotTheme()`: reads `getComputedStyle(document.documentElement)`
  for `--surface`, `--line`, `--ink-2`, `--ink-3`, `--fs-plot` (parse px), `--font`,
  `--peak`, `--peak-ink`, `--median`, and the series list from a new token
  `--series` (a comma-separated list in `:root`, dark and Journal values above).
  Returns a plain object. With no `document` (unit tests), it returns the dark
  values as constants.
- `baseLayout()` uses it: `font: {color: ink-2, size: fs-plot, family: font}`;
  axes `gridcolor: line`, `zerolinecolor: line`, `linecolor: line`,
  `tickfont: {color: ink-3}`, `title.font: {color: ink-2}`;
  backgrounds stay transparent.
- `SERIES_COLORS` / `seriesColor(i)` read the theme's series list.
- Replace hard-coded colours: density fill = series 1 at alpha 0.15 (build the
  rgba from the hex); scatter diagonal = `ink-3`, dashed; grouped_density "All" =
  `ink-2`.
- Tests: `plotTheme()` without `document` returns 8 series and the dark `--line`;
  `baseLayout().xaxis.gridcolor` equals it.

### Step 3. Drawn icons instead of characters (`feat:`)

Files: new `ffast/renderers/web/static/icons.js`, `index.html`, `app.js`,
`rail.js`, `sidebar.js`, `help.js`, `remote_browser.js`, `edit_mode.js`,
`panes/metrics.js`, tests that assert glyph text.

- `icons.js` exports `icon(name)` returning an inline `<svg>` string: 16×16
  viewBox `0 0 16 16`, `fill="none" stroke="currentColor" stroke-width="1.5"
  stroke-linecap="round" stroke-linejoin="round"`, `aria-hidden="true"`, class
  `icon`. Names: `pencil, plus, more, play, pause, step-back, step-forward, gear,
  close, new-window, chevron-right, chevron-down, folder, file, arrow-up, home,
  info, bond, align, force, scissors, sliders, help, check`. Draw each from simple
  lines, circles and polylines (no fills except `more`: three 1.5 px dots).
- Replace the glyphs listed here with `icon(...)` and keep a text label or
  `aria-label` on every button: `#tab-edit-btn` (pencil, "Edit tab layout"),
  `#tab-new-btn` (plus, "New tab"), `#tab-menu-btn` and rail `⋯` (more, "More"),
  `#add-dataset-btn` / `#add-prediction-btn` (plus, "Load dataset" / "Load
  prediction"), `#prev-frame-btn`, `#play-pause-btn` (play/pause), `#next-frame-btn`,
  `#playback-gear` (gear, "Playback settings"), `#pick-clear`, `#hint-dismiss`
  (close, "Clear" / "Dismiss"), `#popout-btn` (new-window + text "New window"),
  `#fb-up`, `#fb-home`, file-browser 📁 / 📄 (folder/file), pick tools
  (info, bond, align, force, scissors), sidebar chevron `▾` (chevron-down,
  rotated −90° when collapsed, as today), help "?" (help), edit-mode ⚙ ✕ (gear,
  close), metrics → × (chevron-right, close).
- `#sidebar-toggle`: sliders icon + text "Settings" (no arrow). Update the
  assertions that read `'Settings ▸'` / `'◂ Settings'`.
- `.icon { width: 1.1em; height: 1.1em; vertical-align: -0.15em; }`.
- `#playback-gear` must stay larger than `#play-pause-btn`
  (`test_web_fps_and_skip_live_in_a_gear_pop_up`): give it `--fs-xl`.
- Tests: `icon('plus')` returns an `<svg` string with `aria-hidden`; every name in
  the list returns one. Look: compare with `fp-3d.html` (pick tools).

### Step 4. Panel letters and captions (`feat:`)

Files: new `ffast/renderers/web/static/figure.js`, `analysis.js`
(`_buildPanelCard`, `_layoutGrid`, render path), `index.html` (CSS),
`tests/ffast/renderers/web/test_web_pure_helpers.py`, `test_web_runtime.py`.

- `figure.js` exports pure functions:
  - `panelLetters(cells)` — `cells` is `[{index, row, col}]` (the TOML index and
    the grid position from `placeCell`). Sort by `row`, then `col`, then `index`;
    return a `Map(index → letter)` with letters `a…z`, then `aa, ab…`.
  - `caption(kind, info)` returns the caption text (no letter):
    - `timeline` with one series: `"<y title>. Top N peaks: frames 22, 41, 58."`
      (frames ascending; omit the second sentence when there are no peaks).
    - `timeline` / `overlay_timeline` with several series: `"<y title>, <n> series."`
    - `density`, `grouped_density`: `"Distribution of <x title>."`
    - `scatter`: `"<y title> against <x title>."`
    - `table`, `grouped_table`, `3d`: `""` (letter only, no caption line).
    `info` carries `{xTitle, yTitle, nSeries, peaks}`; titles are the strings the
    builder put on the axes (already including the unit or `[unit]`).
- Panel card: a `span.panel-letter` (text = letter) as the first child of
  `.panel-title`; a `div.panel-caption-line` after the body, holding
  `<b>letter</b> | caption`. Hidden (`display:none`) when the caption is empty.
  Where a caption shows a unit, render it with the step-0 editable unit function.
- 3D cells (`.panel-3d`) get the letter as an absolutely positioned
  `span.panel-letter` top-left inside the viewport (z-index 3, ground at 70 %
  behind it). No caption.
- Letters are recomputed whenever the grid is laid out (`_layoutGrid`), so edit
  mode and user tabs get them.
- CSS: `.panel-letter { font: 700 var(--letter) Georgia, "Times New Roman", serif;
  color: var(--ink); margin-right: 8px; }`; `.panel-caption-line { font-size:
  var(--fs-s); color: var(--ink-3); padding: 6px 12px 8px; border-top: 1px solid
  var(--line); }`. Panel: `background: var(--surface); border: 1px solid
  var(--line); border-radius: 6px`. Title row: `font-size: var(--fs-m);
  font-weight: 600; color: var(--ink); padding: 10px 12px 4px`.
- Tests: `panelLetters` orders a 2×2 grid placed out of TOML order by screen
  position; 27 cells give `…z, aa`. `caption` for each kind. Runtime: on Basic
  Errors, every `.analysis-panel` has a `.panel-letter`, the first is `a`.
- Look: compare with `fp-analysis.html`.

### Step 5. Peak labels on timelines (`feat:`)

Files: new `ffast/renderers/web/static/peaks.js`, `panels.js` (`buildTimeline`),
`analysis.js` (`_wirePanelInteractions`, the frame callback), tests.

- `peaks.js` exports `pickPeaks(y, n = 5)`:
  - returns `[]` when `y.length < 10`;
  - a candidate is an index `i` with a finite `y[i]` that is `>=` each existing
    neighbour;
  - take candidates by value, largest first; skip one within
    `max(1, round(y.length / 50))` frames of an already taken one; stop at `n`;
  - return `[{frame: i, value: y[i]}]` sorted by frame.
- `buildTimeline`: only when the panel has exactly one series, compute
  `pickPeaks(y)` and add one Plotly annotation per peak: `x: frame, y: value,
  text: "<frame> · <value><unit>"` (value with the units branch's 3-significant-
  figure formatter; unit as on the y axis, or `[unit]`), `textangle: -90,
  showarrow: true, arrowhead: 0, arrowwidth: 1, arrowcolor: peak, ax: 0, ay: -18,
  yanchor: 'bottom', font: {color: peak-ink, size: fs-plot}, captureevents: true`.
  Give the y axis headroom: `range: [min(0, ymin), ymax * 1.45]`. Return the peaks
  so `caption` can list them.
- Click: in `_wirePanelInteractions`, `el.on('plotly_clickannotation', ev =>
  this._onPointFrame({datasetFp, modelFp, frame: peaks[ev.index].frame}))`, using
  the same dataset and model as the existing `plotly_click` path.
- Visited: `AnalysisManager` keeps a `Set` of `"<datasetFp>|<modelFp>|<frame>"`,
  added to on every `_onPointFrame` call (curve or label). A visited peak's
  annotation gets `opacity: 0.55` and `" ✓"` after its text. After adding,
  `Plotly.relayout` the annotations of the timeline panels of that dataset and
  model on the current tab. Not saved anywhere.
- Hover on a label: `hovertext: "Open frame <frame> in 3D"`.
- Tests: `pickPeaks` on a flat line with spikes at 22, 41, 58, 73, 86 returns those
  five; adjacent spikes 73 and 74 give one peak; length 9 gives none; NaN is
  skipped. Runtime: after loading the example data, the Basic Errors forces
  timeline has 5 annotations; clicking one sets `#frame-label` to that frame.
- Look: compare with `fp-analysis.html`, panels b and c.

### Step 6. Colour-blind-safe error colour, legend on the molecule (`feat:`)

Files: `colormap.js`, `panes/colorby.js`, `quick_styles.js`, `app.js`,
`index.html`, tests.

- `COLORMAP_STOPS.cividis` = `[[0,0.135,0.305],[0.103,0.22,0.436],
  [0.264,0.308,0.423],[0.381,0.395,0.436],[0.489,0.485,0.471],[0.609,0.58,0.464],
  [0.736,0.681,0.424],[0.871,0.79,0.343],[0.996,0.909,0.218]]` (matplotlib
  cividis at 9 even stops). Add `'cividis'` first in `COLORMAPS` in `colorby.js`.
- `forceErrorStyle` uses `'cividis'` instead of `'force_error'`. Keep
  `force_error` in the list.
- Legend: create `div#legend3d.legend3d` once and move it into the viewport of
  the focused 3D panel, the same way `#hint-bar` is moved (find it in `app.js`
  near the hint-bar move). Move `#colorbar` and `#colorbar-label` into it; keep
  their ids and their `.hidden` behaviour; the sidebar section no longer shows
  them. CSS: `position:absolute; left:12px; bottom:12px; z-index:3; width:240px;
  padding:8px 10px; background: color-mix(in srgb, var(--surface) 88%,
  transparent); border:1px solid var(--line); border-radius:6px;
  font-size: var(--fs-s); color: var(--ink-2)`. The label sits above the bar.
- The hint bar must not overlap the legend: when both show, the hint bar sits
  bottom-centre with `left: 264px` minimum.
- Tests: `gradientCss('cividis')` starts with the first stop (in the module's own
  format); the Force error quick style sets Colormap `cividis`; runtime: after
  Force error, `#colorbar` is inside `.viewport`. `test_metrics_pane.py` reads
  `#colorbar .cb-max` — keep that path working.
- Existing assertions to update in this commit (they expect the old list or the
  old quick-style map): `test_web_pure_helpers.py` — the `COLORMAPS` list
  (`['viridis','inferno',…,'force_error']`, now with `'cividis'` first) and the
  quick-style cases that expect `'force_error'` from `forceErrorStyle`
  (`git grep -n "force_error" tests/ffast/renderers/web`; leave cases that set
  `force_error` themselves, such as start-settings tests);
  `test_web_runtime.py::test_web_force_error_style_sets_the_existing_controls`.
- The desktop renderer (`ffast/renderers/vispy/adapter.py _get_colormap`) gets
  `cividis` from vispy's built-in maps, so a session coloured in the browser
  still draws on the desktop. No server change.
- Look: compare with `fp-3d.html` (legend bottom-left).

### Step 7. Journal page and density (`feat:`)

Files: `index.html` (markup + CSS), `app.js`, `analysis.js` (re-render hook),
`layout_state.js` (no change expected), tests.

- Top bar, right side before `#status`: a segmented control
  `div#view-mode[role=radiogroup]` with two `button[role=radio]`: "Screen" and
  "Journal page" (`aria-checked`). Next to it a `select#density` with "Compact",
  "Comfortable", "Large".
- Journal page: toggles `body.journal`. In CSS, `body.journal #tabpanels` redefines
  the Journal column of the tokens and `--series`; the rail, top bar and sidebar
  stay dark. Then every rendered Plotly panel is re-rendered (add
  `AnalysisManager.rerenderAll()`, which calls the existing render for each card)
  so `plotTheme()` picks up the new values. The 3D view is not changed.
- Density: sets `--scale` on `:root` (1, 1.15, 1.4), then `rerenderAll()`.
- Both are saved with `saveLayout({journal, density})` and restored at start.
- Tests: runtime — clicking "Journal page" makes a `.analysis-panel`'s computed
  background `rgb(255, 255, 255)` and keeps `#objectbar` dark; choosing "Large"
  makes `body` font-size 18.2px; both survive a reload. `test_web_input_boxes_are_dark`
  keeps passing (Journal is off by default).
- Look: compare with `fp-journal.html`.

### Step 8. Keyboard and dialogs (`fix:`)

Files: `analysis.js` (`_buildTab`), `rail.js`, `sidebar.js` (`createPane`,
`oneSectionOpen`), `dialogs.js`, `remote_browser.js`, `session_ops.js`,
`edit_mode.js`, `app.js`, tests.

- Tabs: `#tabbar` gets `role=tablist`; each `div.tab` gets `role=tab`,
  `aria-selected`, `tabindex` 0 on the active tab and −1 on the others. Left/Right
  arrows move focus and select; Home/End go to first/last.
- Rail rows: `role=button`, `tabindex=0`, `aria-pressed` = selected; Enter or Space
  does what a click does. The `⋯` button stays its own tab stop.
- Section headers: `role=button`, `tabindex=0`, `aria-expanded`; Enter or Space
  toggles, as a click does.
- New helper in `dialogs.js`: `trapFocus(modal)` keeps Tab and Shift+Tab inside
  the modal and returns a function that removes the trap. Use it in `askDialog`,
  `textDialog`, `edit_mode.js modal()`, the file browser, the connection dialog
  and the path prompt.
- `askDialog({title, message, buttons, danger})` (it already takes one object;
  `danger` is new and optional): when `danger` names a button, that
  button gets class `danger` (`background: transparent; color: var(--danger);
  box-shadow: inset 0 0 0 1px var(--danger)`) and focus goes to the first button
  that is not `danger`. Pass `{danger: 'Delete'}` from every Delete caller
  (`git grep -n "askDialog(" ffast/renderers/web/static`).
- File browser: `#fb-modal` gets `role=dialog`, `aria-modal=true`,
  `aria-labelledby` its title; Esc closes; on open, focus the first row; rows are
  `tabindex=0` with Enter opening a folder or selecting a file; the path field
  stops being read-only and Enter on it navigates to the typed path.
- Shortcuts, one `document` keydown listener in `app.js`, ignored when focus is in
  an input, textarea, select or contenteditable, or a modal is open:
  Left/Right = previous/next frame (the existing buttons' handlers), Space =
  play/pause, Esc = disarm the armed pick tool, `/` = focus `#sidebar-search`
  (show the sidebar first if hidden). Add them to the "?" help of Camera or a new
  line in the hint bar's third step: "Keys: ←/→ frame, Space play, / search".
- Tests: runtime — Tab reaches a `.tab` and ArrowRight selects the next; Enter on
  a focused rail row selects it; the Delete dialog focuses "Cancel"; Esc closes
  `#fb-modal`; ArrowRight advances `#frame-label`; `/` focuses `#sidebar-search`.

### Step 9. Status, hints and copy (`fix:`)

Files: `app.js`, `index.html`, `rail.js`, tests.

- `_onDatasetMeta`: when a dataset first arrives, `_setStatus('Dataset "<name>"
  ready · <n> frames', 'connected')`.
- `Connected (CONTROLLING)` → `Connected` with the role in the button's `title`.
  Update the test that asserts the old text.
- `HINTS['no-prediction']`: drop the "Quick style Force error" step (its button
  is disabled then); the steps become "Load a prediction: File ▸ Load Prediction…",
  "Arm a pick tool and click atoms", "Find any setting with the search box".
  Update `.hint-n` assertions if the count changes.
- Empty rail lists: both say "None loaded".
- The "only forces" warning (`#only-forces-warning-modal`) text: replace "You made
  the atoms disappear. Do not use…" with "Atoms are hidden while only forces are
  shown. Turn on atoms in Display to see them again."
- Rail dataset meta: "100 fr" → "100 frames" (and "frozen · 3 fr" →
  "frozen · 3 frames"; update `test_web_runtime.py`, which asserts
  `"frozen · 3 fr"`).
- Tests: status after a dataset load contains "ready"; the new hint texts.

### Step 10. Narrow windows (`fix:`)

Files: `index.html` (CSS), `analysis.js` (`_sizeGrid`), tests.

- `.analysis-grid`: `--col-min: min(360px, 100%)`. In `_sizeGrid`, when the grid's
  own width is below `2 × 360 + gap`, use one column (`repeat(1, 1fr)`) instead
  of scrolling sideways.
- `#tabbar`: keep `overflow-x: auto`, add a right-edge fade (`mask-image:
  linear-gradient(to right, black calc(100% - 24px), transparent)`) when it
  overflows (class `overflowing`, set from a ResizeObserver).
- `#pick-bar`: the read-out (`#pick-readout`) shrinks first; `#sidebar-toggle`
  never wraps (`white-space: nowrap`).
- Tests: at 1024×768 the Basic Errors grid has one column (`gridTemplateColumns`
  has one track) and `.analysis-grid` has no horizontal scroll
  (`scrollWidth <= clientWidth`).

### Step 11. Documents (`docs:`)

- `DESIGN.md` at the repo root (new): the tokens, the type scale, the panel
  letter and caption rule, the peak rule, the Journal page rule, and the icon
  rule (one stroke, `currentColor`, `icons.js` only). Short; this ADR has the
  detail.
- `docs/adr/README.md`: add the 0063 row.
- This ADR: status line "Accepted <date>, built <date> (branch `figure`)" and an
  **As built** section listing anything that differed from the steps.
- `.impeccable/` (ignored, main checkout only) holds the design brief; leave it.

## As built

Steps 0 to 11 were built in order, one commit each, on branch `figure`. What
differed from the steps, and what was added:

**Added, and not in the steps**

- **A "Peaks" checkbox on each one-series timeline** hides its peak labels, the
  headroom they need and the caption's peak sentence. Asked for by the user; its own
  commit. It is kept per panel for a reload in the tab's browser state
  (`sessionStorage`, like zoom and SUB), never in a session file.
- **Peak labels are laid out from their own text.** The step's fixed headroom
  (1.45 × the highest value) left about 50 px for a label of about 100 px, so labels
  were cut off. Plotly also centres a turned label on the far end of its arrow, so
  low peaks' labels crossed the x axis. Now each arrow ends half a label's length
  above its peak; the plot's top margin is set from the longest label (estimated
  from the character count, 0.6 em per character); the y range is set from the data
  alone, because Plotly would count the arrow ends and flatten the curve; and the
  plot grows by the room the labels take, with the tab's rows (`--row-min`), so the
  curve keeps its size. Side effects: every row of a tab with labelled timelines was
  about 120 px taller, and the explicit y range was recorded like a zoom. Changed
  after the build: a plot remembers the ranges it was drawn with, which are not a
  zoom, and a double-click goes back to them (Plotly's `doubleClick: 'reset'`), not
  to an autorange that counts the label arrows. Also changed: only the rows that
  hold peak labels grow. `--row-min` is gone; `_sizeRows` writes each row's size
  (300 px or the tab's set height, plus its labels' room), because the grid scrolls
  and a row there does not grow to fit what is in it.
- **`PEAK_HEADROOM`** existed for one commit (2.0) and was replaced by the above.
- **Journal page is a print view**, added after the build. Besides the light
  tokens, it hides the figure area's controls: the tab's control bar, the Peaks and
  Sub checkboxes, the Smoothing inputs, Plotly's toolbar and any other control in a
  panel title (edit mode keeps what it needs). A unit is plain text, not a link to
  name it, and captions are set in serif. The 3D view is drawn on white; this is
  local to the browser, like the switch itself, and an export keeps its own
  background. The rail, top bar, tab bar and 3D sidebar stay as they are.

**Where a step's text was followed differently**

- **Step 4.** The caption line is the card's last row (below the Smoothing inputs),
  as in the sketch. A panel with an empty axis title has no caption. The letter over
  a 3D view ignores the pointer, or it caught orbit and focus clicks. An independent
  panel's corner caption moves right of the letter. A "[unit]" in a caption is
  clickable; `unitRoleAt` now accepts any element with `data-unit-role`.
- **Step 5.** The rule "at least as high as each neighbour" made every frame of a
  level stretch a peak, so a flat series got five piled-up labels. Changed after the
  build: a peak must be higher than the frame before it and at least as high as the
  one after, so a level stretch gives one peak, at its first frame, and a series of
  one repeated value gives none. Opening a frame moves to the 3D tab, so the visited
  marks are drawn before the frame is opened. The labels do not use
  `yanchor: 'bottom'`: each arrow ends half a label's length above its peak, and
  the turned label is centred there (see "Peak labels are laid out from their own
  text").
- **Step 7.** The Journal tokens are set on `.analysis-grid`, not `#tabpanels`: the
  3D sidebar and pick bar are inside `#tabpanels` and would have turned white. The
  old token names are written again there, because an alias on `:root` keeps its
  dark value. `plotTheme()` reads the tokens from the first `.analysis-grid`.
  Only the tab on screen is drawn again when a setting changes (`rerenderAll`);
  other tabs are drawn when shown. Everything inside a 3D viewport in the figure
  area takes the Journal tokens with the grid: the colour legend and panel letter,
  and also the hint bar (`#hint-bar`), the load overlay (`#overlay`) and an
  independent panel's caption and note (`.panel-caption`, `.panel-note`). Since
  Journal page became a print view, the 3D view itself is drawn on white too.
- **Step 8.** The ADR names only the Delete callers for `danger`. The "Reset to
  original" tab dialog deletes the user's edited copy too, so it also passes it: its
  buttons are Cancel and Confirm (not Reset), Confirm is red and focus starts on
  Cancel. Esc closes the path prompt from any control in it.
- **Step 9.** The "ready" status replaces a finished Loading, Connecting or
  Connected, or an earlier "ready", but never a result such as "Loaded session from
  …", which the server's dataset messages can follow. After connecting, the last
  "ready" text is shown instead of a bare "Connected". One frame reads "1 frame".
  A subset gets no "ready" status.
- **Step 10.** The step says the Basic Errors grid has one column at 1024 px. Its
  own rule does not give that: the grid is 804 px wide there and two 360 px columns
  fit, with no sideways scroll. One column starts below about 970 px of window
  width. A tab that sets its own column widths keeps them. A narrow grid drops the
  tab's `row_heights` too. One column starts only below 730 px of grid width (two
  360 px columns and the gap), so a tab with three or more columns still scrolls
  sideways from there up to the width its columns need (1100 px for three).

**Other**

- Step 3's gear icon reads a little like a sun at small sizes.
- Step 6 also changed `test_web_edit_mode_makes_a_3d_panel_independent_with_the_look_on_screen`,
  which expected the saved colormap `force_error` after the Force error style.
- Not built, as the ADR says: Copy figure, atom rings, a Journal 3D background,
  peak labels on several series, Qt.
