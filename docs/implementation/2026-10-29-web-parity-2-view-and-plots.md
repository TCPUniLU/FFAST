# Web parity, 3D view and 2D plots: implementation plan (12 tasks)

Source: web parity plan 2026-10-29 (a code-read comparison of the desktop
client and the web client). There is no ADR for these tasks; this plan is the
whole design. Do the tasks **in the order given**, one commit per task. Each
task leaves the app working.

Words used here:
- **desktop** = the Qt client (`UI/`, `modules/`). **web** = the browser
  client in `ffast/renderers/web/static/`.
- **view** = one server-side 3D scene (`view_id`); the web keeps one sidebar
  settings object per view (`view.settings`, see `static/view_settings.js`).
- **panel** = one plot or table card on an analysis tab (`static/analysis.js`
  builds the card, `static/panels.js` draws it).

## Decisions (confirmed by the user 2026-10-29)

1. **Hover highlight with no tool armed:** yes. The atom under the pointer is
   always lit, as on the desktop (Task 12, a normal task).
2. **Plot CSV layout:** long format, one row per point:
   `series, <x axis title>, <y axis title>` (Task 6).
3. **Table CSV numbers:** full precision (the raw number), not the rounded
   text on screen (Task 7).
4. **Where label edits are kept:** in the tab's definition on the server,
   like everything else the user makes in a tab. They are new optional panel
   fields in the tab TOML (`x_title`, `y_title`, `label_size`, `legend_size`,
   `legend_x`, `legend_y`), saved through the existing SAVE_TAB path. Editing
   a built-in or project tab saves a user tab that replaces it, as the tab
   editor already does. Not in browser storage, not in a session file
   (Tasks 9-11).
5. **How labels are edited:** "Edit labels…" in the panel's ⋯ menu opens a
   small form (Task 11).
6. **Font sizes:** editable. The form has "Axis title size" (both axis
   titles) and "Legend text size" (Task 11).
7. **Legend entries are not renamed in plots.** Legend text stays the
   dataset/prediction names; renaming a prediction itself is another plan.
   The legend can be dragged, and its place is kept in the tab (Task 10).
8. **Info read-out wording:** the desktop's words plus units:
   `Atoms 3,5 / Elements C,H / Distance: 1.09 Å` (Task 3).
9. **Plot hover read-out:** series name, then `<x axis title> = value` and
   `<y axis title> = value`, plus dotted crosshair lines. No `error` line: the
   desktop never fills it (`hoverData` is read in `UI/Plots.py`
   `formatHoverText` but set nowhere) (Task 8).

## Findings that need no code

- **Pick radius**: deliberately removed from the web (ADR 0055: picking by the
  drawn ball replaced it; `test_web_pick_radius_control_is_gone` pins this).
  Not a gap.
- **Clear picks and the Force tool**: already works. `static/app.js`:
  ```js
  document.getElementById('pick-clear').addEventListener('click', () => {
    // Clear the Force picks of this frame too, not only the overlay (to_fix_v2).
    if (this._activeTool === 'forces') this._panes.forces.setPickedIndices([], this._forcePickFrame());
    this._clearPicks();
  });
  ```
  The clear button only shows while a tool is armed (`_updatePickStrip`), so
  there is no other case. No test pins it: Task 5 adds one.
- **Alignment vs centre of mass**: a real gap (Task 4). The desktop turns
  "Origin COM" off when 3-atom alignment turns on, and the reverse
  (`modules/loupe/loupeAtomAlign.py`, "MAKE IT EXCLUSIVE WITH COM"). Only
  3-atom alignment, not Kabsch. The web has no such link.

## Rules for the whole job

1. **Do not touch** files this plan does not name. In particular leave
   `CONTEXT.md`, `docs/adr/README.md`, every file under `docs/adr/`, every
   file under `docs/implementation/`, and every untracked file (`to_fix*.md`,
   `docs/research/...`) alone: the user commits them. You edit code and tests
   only. Stage with `git add <exact paths>`, never `git add -A` or `git add .`.
2. The user may commit while you work. Run `git status --short` and
   `git log --oneline -3` before each commit. Never `git reset --hard`, never
   rebase, never amend a commit you did not just make.
3. Commit messages: use the subject each task gives (it starts with `feat:`
   or `fix:`), in plain words about what the user sees; see
   `git log --oneline -20` for the style. Body: 2–4 short lines on what
   changed and why, naming "web parity plan 2026-10-29".
   **No `Co-Authored-By` line.**
4. Tests run with the project venv, never `uv run`:
   ```
   ~/.venvs/ffast_env/bin/python -m pytest <file> -q
   ```
   `-n 6` is on by default (pyproject). Add `-n 0` only to debug one test.
   Browser tests (under `tests/ffast/renderers/web/`) need Playwright's
   Chromium and are slow: run only the tests named in the task (`-k`), plus
   the whole small unit files named (`test_web_pure_helpers.py`,
   `test_web_panes.py`, `test_web_view_settings.py`).
5. After each task, run the fast suite once:
   ```
   ~/.venvs/ffast_env/bin/python -m pytest -m "not integration" -q
   ```
   It must stay green (one known order-dependent red test is described in
   `docs/issues/0067-01-*.md`; ignore only that one).
6. Match the surrounding code: a short docstring or JSDoc comment above each
   new function, saying *why* in plain words; 2-space indent in JS; the same
   naming style as the file. Mention "web parity" in a comment only where a
   reader would ask "why is it like this?".
7. If the plan does not match the code (a line moved, a name differs), find
   the place by searching for the quoted code, not by line number. If the plan
   is wrong about **behaviour**, stop and report; don't invent another design.
8. UI state never goes into a session file or a tab file (project rule, ADR
   0055 rule 3). Everything new in Tasks 1-8 lives in the view's settings
   object. The plot label edits of Tasks 9-11 are different: they are part of
   the tab the user makes, like a panel's title, so they are saved in the tab
   file (decision 4). Nothing in this plan goes into a session file or
   browser storage.

Paths are relative to the repo root. `static/` means
`ffast/renderers/web/static/`.

---

## Task 1: atom index labels on and off (Display pane)

**What the user sees:** the Display section has a checkbox "Atom index
labels" under "Atom size". Ticking it writes each atom's index next to it in
the 3D view; unticking removes them. Each view remembers its own choice while
the page is open.

**Facts.**
- The desktop sends `TOGGLE_FEATURE` `"labels"` (`UI/loupe/window.py`
  `onToggleSceneLabels`: `self._sendToggleFeature("labels", self.settings.get("showSceneLabels"))`);
  the control is `"showSceneLabels"` labelled "Atom index labels" in
  `modules/loupe/loupeViewSettings.py`.
- The server already builds labels when the feature is on
  (`ffast/visualization/scene_builder.py`: `if "labels" in state.enabled_features:`),
  and accepts any feature name (`ToggleFeatureCommand.feature: str` in
  `ffast/visualization/commands.py`).
- The web renderer already draws them: `static/renderer.js` `_updateLabels(labels)`
  (sprites in `this._labelSprites`), called from the snapshot and from a patch
  (`if (c.has('labels')) this._updateLabels(patch.labels || null);`).
- Nothing sends the feature. `static/app.js`:
  ```js
  const display = createDisplayPane(sidebarEl, {
    onAtomSize: (scale) => this._sendSetParameter('ffast.atom_sizes', 'scale', scale),
    onHideAtoms: (tokens) => this._sendSetParameter('ffast.atom_filter', 'indices', tokens),
    onHighlight: (indices) => this._sendSetSelection('picked', 'current_structure', indices),
    onUnitCell: (visible) => this._sendToggleFeature('no_unit_cell', !visible),
  });
  ```
- `static/view_settings.js` `defaultSettings()`:
  `display: { atomSize: 1, hide: '', highlight: '', unitCell: true },`.
- A frame subset's view starts from its parent's server state (`start_from`
  in `_openView`), so its features match the copied settings, as with the
  unit cell today. Nothing to replay.

**Change.**
1. `static/view_settings.js`: make it
   `display: { atomSize: 1, labels: false, hide: '', highlight: '', unitCell: true },`.
2. `static/panes/display.js`:
   - Add `onLabels: (on: boolean) => void,` to the JSDoc callbacks list.
   - Right after the `atomSizeRow` line add:
     ```js
     const labelsRow = checkboxRow(body, 'Atom index labels', false, (v) => {
       state.labels = v;
       callbacks.onLabels(v);
     });
     ```
   - In `show(settings)` add `labelsRow.checked = !!settings.labels;` after the
     `atomSizeRow` line.
   - Update the file's top comment: it now also carries the index labels.
3. `static/app.js`: add
   `onLabels: (on) => this._sendToggleFeature('labels', on),` to the
   `createDisplayPane` callbacks.

**Test.**
- `tests/ffast/renderers/web/test_web_view_settings.py`
  `test_defaults_are_todays_defaults`: the `display` dict becomes
  `{"atomSize": 1, "labels": False, "hide": "", "highlight": "", "unitCell": True}`.
- `tests/ffast/renderers/web/test_web_panes.py`:
  - `display_show`: show `{atomSize: 2, labels: true, hide: 'C', highlight: '0 1', unitCell: false}`
    and read the labels `['Atom size', 'Atom index labels', 'Hide atoms', 'Highlight atoms', 'Show unit cell']`.
    Test asserts `r["controls"] == ["2", True, "C", "0 1", False]`.
  - `display_write`: start from `{atomSize: 1, labels: false, hide: '', highlight: '', unitCell: true}`;
    after `fire(ctl(host, 'Atom size'), '3')` add `fire(ctl(host, 'Atom index labels'), true)`.
    Test asserts `r["s"] == {"atomSize": 3, "labels": True, "hide": "H", "highlight": "0 4", "unitCell": False}`
    and the call names `["onAtomSize", "onLabels", "onHideAtoms", "onHighlight", "onUnitCell"]`.
  - `display_isolated`: add `labels: false` to both objects and to the
    expected `s` dict.
- `tests/ffast/renderers/web/test_web_runtime.py`, new
  `test_web_atom_index_labels_switch_on_and_off` (copy the setup of
  `test_web_armed_tool_highlights_the_atom_under_the_pointer`:
  `_preload_dataset`, `_open_loupe`):
  `await _open_section(page, "Display")`; check
  `.pane[data-pane='Display'] .ctl-row[data-label='Atom index labels'] input`;
  `await page.wait_for_function("() => window.ffastApp.renderer._labelSprites.length > 0")`;
  uncheck it; wait for `=== 0`.

Run `test_web_view_settings.py`, `test_web_panes.py`, and
`test_web_runtime.py -k "atom_index_labels or unit_cell"`.

**Commit:** `feat: the web Display section switches atom index labels on and off`.

---

## Task 2: the server sends each atom's element with the scene

**What the user sees:** nothing yet; Task 3 uses it.

**Facts.**
- The browser knows atom positions and ids but not elements:
  `ffast/visualization/scene.py`:
  ```python
  class AtomScene(BaseModel):
      """Renderer-neutral atom instance data."""
      model_config = ConfigDict(extra="forbid")
      positions: list[list[float]]   # (N, 3)
      sizes: list[float]             # (N,)
      colors: list[list[float]]      # (N, 4) RGBA — element/default fallback
      atom_ids: list[int] | None = None
      color_by: AtomColorBy | None = None
  ```
- `ffast/visualization/scene_builder.py` `build_scene` has `z` (atomic
  numbers of the frame, `np.int64` array) and, after the atom filter, `keep`
  (bool mask or `None`). It builds:
  ```python
  scene.atoms = AtomScene(
      positions=positions.tolist(),
      sizes=sizes,
      colors=colors,
      atom_ids=atom_ids,
      color_by=color_by,
  )
  ```
- `AtomScene` is built only here (`grep -rn "AtomScene(" ffast`). The Vispy
  adapter reads fields by name, so a new optional field does not affect it.
- `tests/ffast/test_scene_builder.py` `_FakeDataset` has elements `[6, 1, 1, 1]`.

**Change.**
1. `ffast/visualization/scene.py` `AtomScene`: after `atom_ids`, add
   ```python
   # Atomic number of each displayed atom, so a client can name the atoms it
   # measures without a dataset of its own (web Info read-out).
   elements: list[int] | None = None
   ```
2. `ffast/visualization/scene_builder.py`, just before `scene.atoms = AtomScene(`:
   ```python
   elements = (z[keep] if keep is not None else z).astype(int).tolist()
   ```
   and pass `elements=elements,` to `AtomScene(...)`.
3. `static/protocol.js` `AtomScene` typedef: add
   `@property {number[]|null} [elements] atomic number per displayed atom`.

**Test.** `tests/ffast/test_scene_builder.py`, class `TestBuildSceneAtomFilter`:
- `test_atoms_carry_their_elements`: `self._filtered(ds, [])` →
  `scene.atoms.elements == [6, 1, 1, 1]`.
- `test_filtered_atoms_carry_their_elements`: `self._filtered(ds, [0, 2])` →
  `scene.atoms.elements == [6, 1]`.

Run `tests/ffast/test_scene_builder.py` and
`tests/ffast/renderers/web/test_web_server.py`.

**Commit:** `feat: the 3D scene names each atom's element`.

---

## Task 3: the Info read-out names the atoms and their elements

**What the user sees:** with the Info tool, two picked atoms read
`Atoms 3,5 / Elements C,H / Distance: 1.09 Å` (was `Distance: 1.09 Å`). One
atom reads `Atom 3 / Element C / (0.12, 1.30, -0.40)`. Angle and dihedral
likewise.

**Facts.**
- `static/measure.js`:
  ```js
  export function infoReadout(pts, ids) {
    if (!pts || pts.length === 0) return '';
    if (pts.length === 1) {
      const [x, y, z] = pts[0];
      return `Atom ${ids[0]}: (${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)})`;
    }
    if (pts.length === 2) return `Distance: ${distance(pts[0], pts[1]).toFixed(2)} Å`;
    if (pts.length === 3) return `Angle: ${degrees(angle(pts[0], pts[1], pts[2]))}`;
    return `Dihedral: ${degrees(dihedral(pts[0], pts[1], pts[2], pts[3]))}`;
  }
  ```
- Desktop text (`modules/loupe/loupeInfoSelect.py`):
  `f"Atoms {i},{j} / Elements {z[i]},{z[j]} / Distance: {d:.2f}"`,
  `f"Atom {i} / Element {z} / ({R[0]:.2f},{R[1]:.2f},{R[2]:.2f})"`.
- Caller, `static/app.js` `_reactTool`:
  ```js
  if (id === 'info') {
    const pts = this._picked.map((e) => this.renderer.atomPosition(e.displayIndex)).filter(Boolean);
    this._pickReadout = infoReadout(pts, ids);
  }
  ```
- `static/renderer.js` `_updateAtoms` caches `this._cachedAtomIds = atoms.atom_ids || null;`
  and has `atomPosition(displayIndex)`.
- `elementSymbol(z)` is exported by `static/panels.js`.

**Change.**
1. `static/renderer.js`:
   - In the constructor next to `this._cachedAtomIds = null;` add
     `this._cachedElements = null;        // atoms.elements (atomic numbers, Task 2)`.
   - In `_updateAtoms` next to the `_cachedAtomIds` line add
     `this._cachedElements = atoms.elements || null;`.
   - After `atomPosition`, add:
     ```js
     /** Atomic number of a displayed atom, or null when the server sent none. */
     atomElement(displayIndex) {
       const z = this._cachedElements?.[displayIndex];
       return z == null ? null : z;
     }
     ```
2. `static/measure.js`: replace `infoReadout` with:
   ```js
   /**
    * ... (keep the existing doc lines) ...
    * @param {(string|null)[]} [symbols] element symbols, parallel to `ids`;
    *   left out of the text unless every atom has one
    */
   export function infoReadout(pts, ids, symbols = []) {
     if (!pts || pts.length === 0) return '';
     const named = symbols.length === ids.length && symbols.every(Boolean);
     if (pts.length === 1) {
       const [x, y, z] = pts[0];
       const el = named ? ` / Element ${symbols[0]}` : '';
       return `Atom ${ids[0]}${el} / (${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)})`;
     }
     const head = `Atoms ${ids.join(',')}${named ? ` / Elements ${symbols.join(',')}` : ''} / `;
     if (pts.length === 2) return `${head}Distance: ${distance(pts[0], pts[1]).toFixed(2)} Å`;
     if (pts.length === 3) return `${head}Angle: ${degrees(angle(pts[0], pts[1], pts[2]))}`;
     return `${head}Dihedral: ${degrees(dihedral(pts[0], pts[1], pts[2], pts[3]))}`;
   }
   ```
3. `static/app.js`: import `elementSymbol` from `./panels.js`; in `_reactTool`:
   ```js
   if (id === 'info') {
     const pts = this._picked.map((e) => this.renderer.atomPosition(e.displayIndex)).filter(Boolean);
     const symbols = this._picked.map((e) => {
       const z = this.renderer.atomElement(e.displayIndex);
       return z == null ? null : elementSymbol(z);
     });
     this._pickReadout = infoReadout(pts, ids, symbols);
   }
   ```

**Test.**
- `tests/ffast/renderers/web/test_web_pure_helpers.py`: add
  `f"  const ms = await import('{origin}/measure.js');\n"` to the import list
  in the `results` fixture, and cases:
  - `"info_one_atom": "ms.infoReadout([[0,0,0]], [3], ['C'])"` →
    `"Atom 3 / Element C / (0.00, 0.00, 0.00)"`
  - `"info_distance_named": "ms.infoReadout([[0,0,0],[1,0,0]], [3,5], ['C','H'])"` →
    `"Atoms 3,5 / Elements C,H / Distance: 1.00 Å"`
  - `"info_distance_unnamed": "ms.infoReadout([[0,0,0],[1,0,0]], [3,5])"` →
    `"Atoms 3,5 / Distance: 1.00 Å"`
  with one `test_...` per case asserting the exact string.
- `test_web_runtime.py` `test_web_info_tool_reports_distance`: after the
  `to_contain_text("Distance")` line add
  `await expect(page.locator("#pick-readout")).to_contain_text("Elements")`.

Run `test_web_pure_helpers.py`, `test_measure_parity.py`,
`test_web_runtime.py -k "info_tool or picking_does_not_resize"`.

**Commit:** `feat: the Info read-out names the picked atoms and their elements`.

---

## Task 4: 3-atom alignment and "Origin COM" switch each other off

**What the user sees:** turning on "3-atom frame align" (or arming the Align
pick tool, which turns it on) unticks "Origin COM" in the Camera section, so
the camera stops following the centre of mass and the aligned atoms stay
still. Ticking "Origin COM" again turns 3-atom alignment off. Kabsch is not
affected (desktop does the same).

**Facts.**
- Desktop, `modules/loupe/loupeAtomAlign.py`:
  ```python
  settings.addParameterActions("alignAtoms",
      lambda: settings.setParameter("originCenterOfMass", False) if settings.get("alignAtoms") else None)
  settings.addParameterActions("originCenterOfMass",
      lambda: settings.setParameter("alignAtoms", False) if settings.get("originCenterOfMass") else None)
  ```
- Web "Origin COM" only moves the camera target each frame:
  `static/app.js` `_trackCameraCOM(positions, view)` returns early when
  `!(view.settings?.view.originCenterOfMass ?? true)`.
- The camera pane: `const comInput = checkboxRow(body, 'Origin COM', true, callbacks.onCOM);`
  and `setCOM(enabled)` sets the box without firing `onCOM`. App wiring:
  `onCOM: (enabled) => { const s = this._focused()?.settings; if (s) s.view.originCenterOfMass = enabled; },`.
- `static/panes/align.js`: the `atomAlignInput` handler and
  `enableAtomAlignMode()` are the two places 3-atom mode turns on. Its
  callbacks are `onKabsch`, `onAtomAlign`.
- `this._panes` is set in `_initUI` after both panes exist; callbacks run
  later, so they may read `this._panes`.

**Change.**
1. `static/panes/align.js`:
   - JSDoc callbacks: add `onAtomAlignOn: () => void,` ("3-atom mode was just
     turned on: the camera must stop following the centre of mass").
   - In the `atomAlignInput` handler, after `_syncVisibility();` and before
     `callbacks.onAtomAlign(...)`: `if (state.atomAlign) callbacks.onAtomAlignOn();`.
   - In `enableAtomAlignMode()`, after `_syncVisibility();`: `callbacks.onAtomAlignOn();`.
   - New method on the returned object:
     ```js
     /** Turn 3-atom mode off (Origin COM was turned on; the desktop keeps the two exclusive). */
     turnOffAtomAlign() {
       if (!state.atomAlign) return;
       state.atomAlign = false;
       atomAlignInput.checked = false;
       _syncVisibility();
       callbacks.onAtomAlign(false, state.indices);
     },
     ```
2. `static/app.js`:
   - New method near `_trackCameraCOM`:
     ```js
     /** Set the focused view's Origin COM and its checkbox, without the
      * checkbox's own handler (used when 3-atom alignment turns it off). */
     _setOriginCOM(enabled) {
       const s = this._focused()?.settings;
       if (s) s.view.originCenterOfMass = enabled;
       this._panes.camera.setCOM(enabled);
     }
     ```
   - `createAlignPane` callbacks: add `onAtomAlignOn: () => this._setOriginCOM(false),`.
   - Camera `onCOM`: after setting the setting, add
     `if (enabled) this._panes?.align.turnOffAtomAlign();`.

**Test.**
- `test_web_panes.py`, new case and test:
  ```
  "align_com_link": """(() => { const {pane, host, calls} = mk(ap, 'createAlignPane');
    const s = {kabsch: false, heavyOnly: true, atomAlign: false, idxInput: '', indices: []}; pane.show(s);
    fire(ctl(host, '3-atom frame align'), true); const onByBox = calls.map((c) => c[0]);
    calls.length = 0; pane.turnOffAtomAlign(); const off = {atomAlign: s.atomAlign, box: val(ctl(host, '3-atom frame align')), calls: calls.map((c) => c.slice())};
    calls.length = 0; pane.enableAtomAlignMode(); const onByTool = calls.map((c) => c[0]);
    return {onByBox, off, onByTool}; })()""",
  ```
  asserts: `"onAtomAlignOn" in r["onByBox"]`; `r["off"]["atomAlign"] is False`,
  `r["off"]["box"] is False`, `r["off"]["calls"] == [["onAtomAlign", False, []]]`;
  `"onAtomAlignOn" in r["onByTool"]`.
- `test_web_runtime.py`, new `test_web_three_atom_align_and_origin_com_switch_each_other_off`
  (setup as Task 1): open "Alignment", check
  `.pane[data-pane='Alignment'] .ctl-row[data-label='3-atom frame align'] input`;
  open "Camera"; expect `.pane[data-pane='Camera'] .ctl-row[data-label='Origin COM'] input`
  not checked and `window.ffastApp._focused().settings.view.originCenterOfMass === false`;
  check Origin COM; expect the 3-atom box unchecked and
  `window.ffastApp._focused().settings.align.atomAlign === false`.

Run `test_web_panes.py` and `test_web_runtime.py -k "align or com"`.

**Commit:** `feat: 3-atom alignment and Origin COM switch each other off, as on the desktop`.

---

## Task 5: a test pins that Clear also clears the Force picks

**What the user sees:** nothing new; this guards existing behaviour.

**Facts.** See "Findings that need no code" above. `static/panes/forces.js`
`pickedIndices(frame = null)` returns the stored picks; `app._picked` is the
current pick set; the existing test
`test_web_force_picks_follow_the_frame_on_a_variable_dataset` shows how to
pick without clicking: `page.evaluate("(i) => window.ffastApp._onPick([{displayIndex: i, atomId: i}])", i)`.

**Change.** None in code.

**Test.** `test_web_runtime.py`, new
`test_web_clear_picks_also_clears_the_force_atoms` (setup as Task 1): click
`#pick-toolbar .pick-tool-btn[data-tool='forces']`; pick atoms 1 and 2 via
`_onPick`; assert `window.ffastApp._panes.forces.pickedIndices()` is `[1, 2]`;
click `#pick-clear`; assert `pickedIndices()` is `[]` and
`window.ffastApp._picked.length === 0`.

Run `test_web_runtime.py -k "clear_picks or force_picks"`.

**Commit:** `fix: a test keeps the Clear button clearing the Force atom picks`
(the code already does it; the subject says what is guarded).

---

## Task 6: each panel gets a ⋯ menu; plots download PNG, SVG and CSV

**What the user sees:** every analysis panel has a ⋯ button at the right end
of its title row. On a plot it offers "Download PNG", "Download SVG" and
"Download data (CSV)". The files are named after the panel title
(`Energy_MAE_timeline.svg`). Plotly's own camera button stays. The ⋯ button is
hidden in journal view and in Edit mode, like the other title controls (the
existing CSS already does this for buttons in `.panel-title`).

**Facts.**
- `static/panels.js`:
  ```js
  export const PLOT_CONFIG = {
    responsive: true,
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'autoScale2d', 'toggleSpikelines'],
    doubleClick: 'reset',
  };
  ```
  Plotly is the vendored basic bundle v2.35.2 (`static/vendor/plotly/`); it
  has `Plotly.downloadImage(gd, {format: 'png'|'svg', filename, width, height})`.
- After drawing, `renderPanel` keeps `el._figureInfo = { xTitle, yTitle, nSeries, peaks }`
  and Plotly keeps the drawn traces on the element as `el.data` (each with
  `x`, `y`, `name`, and the scatter diagonal has `hoverinfo: 'skip'`).
- A failed or empty panel replaces the body's children with
  `<div class="panel-msg">`, so "drawn" = `el.querySelector('.main-svg')`.
- `static/analysis.js` `_buildPanelCard(t, spec, token, letter)` builds
  `title` (`.panel-title`: letter span, name span) and returns
  `card = { el, title, body, params, ... }`. Later code appends to the title:
  `_wireSub` (`card.title.appendChild(toggle)` and `card.title.appendChild(freeze)`),
  `_wirePeaksToggle` (`card.title.insertBefore(toggle, card.title.querySelector('.sub-toggle'))`),
  `_showWarnings` (`insertBefore(mark, card.title.children[2] || null)`).
- Menus: `static/actions.js` `sharedMenu(list)` → `{open(button, actions), close}`;
  an action is `{id, label, run, unavailable?}` (`unavailable()` returns ''
  or the reason). `static/rail.js` makes its list with
  `list.className = 'menu-list rail-menu hidden'; list.setAttribute('role', 'menu'); document.body.appendChild(list);`.
  `index.html` has `.tab-context-menu { position: fixed; }` for a body-level list.
- Icons: `static/icons.js` `icon('more')` (three dots).
- File names: `static/session_ops.js` exports `safeName(name, fallback)`.
- Download of text: `static/dialogs.js` `textDialog` does
  `a.href = URL.createObjectURL(new Blob([text], { type })); a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);`.

**Change.**
1. New file `static/panel_export.js` (pure helpers + two small DOM helpers):
   ```js
   /**
    * Download a panel's picture or data (web parity plan 2026-10-29: the
    * desktop's plot export menu). The CSV helpers are pure, for the tests.
    */
   import { safeName } from './session_ops.js';

   /** Text with HTML tags removed (axis titles may carry <sub> etc.). */
   const plain = (s) => String(s ?? '').replace(/<[^>]*>/g, '');

   /** One CSV cell: quoted when it holds a comma, quote or line break;
    * a number that is not finite is an empty cell. */
   function csvCell(v) {
     if (typeof v === 'number' && !Number.isFinite(v)) return '';
     const s = v == null ? '' : String(v);
     return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
   }

   /** Rows of cells as CSV text, one line per row. */
   export function csvText(rows) {
     return rows.map((r) => r.map(csvCell).join(',')).join('\n') + '\n';
   }

   /**
    * A plot's points as CSV, one row per point: series, x, y. The header
    * names the axes as drawn. Helper traces (`hoverinfo: 'skip'`, the
    * scatter's diagonal) are left out.
    * @param {Array<{x?: ArrayLike<any>, y?: ArrayLike<any>, name?: string, hoverinfo?: string}>} traces
    * @param {{xTitle?: string, yTitle?: string}} [titles]
    */
   export function plotCsv(traces, { xTitle = '', yTitle = '' } = {}) {
     const rows = [['series', plain(xTitle) || 'x', plain(yTitle) || 'y']];
     for (const tr of traces || []) {
       if (tr.hoverinfo === 'skip') continue;
       const xs = Array.from(tr.x || []), ys = Array.from(tr.y || []);
       for (let i = 0; i < Math.max(xs.length, ys.length); i++) rows.push([tr.name || '', xs[i] ?? '', ys[i] ?? '']);
     }
     return csvText(rows);
   }

   /** A panel table as CSV: a cell's `data-value` (the full number) when it
    * has one, else its text. */
   export function tableCsv(table) {
     if (!table) return '';
     return csvText([...table.querySelectorAll('tr')].map((tr) =>
       [...tr.children].map((c) => c.dataset.value ?? c.textContent.trim())));
   }

   /** The file name stem of a panel. */
   export function panelFileName(spec) {
     return safeName(spec.title || spec.kind, 'panel');
   }

   /** Save `text` as a file in the browser's downloads. */
   export function downloadText(text, filename, type = 'text/csv') {
     const a = document.createElement('a');
     a.href = URL.createObjectURL(new Blob([text], { type }));
     a.download = filename;
     a.click();
     setTimeout(() => URL.revokeObjectURL(a.href), 1000);
   }

   /** Save a drawn plot as PNG or SVG at its size on screen. */
   export function downloadPlot(el, format, stem) {
     return globalThis.Plotly.downloadImage(el, {
       format, filename: stem, width: el.clientWidth, height: el.clientHeight,
     });
   }
   ```
2. `static/analysis.js`:
   - Imports: `sharedMenu` from `./actions.js`; `icon` from `./icons.js`;
     `plotCsv, tableCsv, panelFileName, downloadText, downloadPlot` from
     `./panel_export.js`.
   - Constructor, at the end:
     ```js
     // One ⋯ menu list for every panel (web parity: plot and table export).
     const panelMenuList = document.createElement('div');
     panelMenuList.className = 'menu-list panel-menu hidden';
     panelMenuList.id = 'panel-menu-list';
     panelMenuList.setAttribute('role', 'menu');
     document.body.appendChild(panelMenuList);
     this._panelMenu = sharedMenu(panelMenuList);
     ```
   - `_buildPanelCard`: after `el.appendChild(title);` add
     ```js
     const more = document.createElement('button');
     more.type = 'button';
     more.className = 'panel-more';
     more.innerHTML = icon('more');
     more.setAttribute('aria-label', `More for ${spec.title || 'this panel'}`);
     more.setAttribute('aria-haspopup', 'menu');
     more.setAttribute('aria-expanded', 'false');
     title.appendChild(more);
     ```
     put `more` into the returned `card` object, and after `card` is made:
     `more.addEventListener('click', () => this._panelMenu.open(more, this._panelActions(t, spec, card)));`
   - Keep ⋯ last in the title row: in `_wireSub` change both
     `card.title.appendChild(toggle)` and `card.title.appendChild(freeze)` to
     `card.title.insertBefore(..., card.more)`; in `_wirePeaksToggle` use
     `card.title.insertBefore(toggle, card.title.querySelector('.sub-toggle') || card.more)`.
   - New method:
     ```js
     /** The ⋯ menu of a panel: downloads (web parity plan 2026-10-29). */
     _panelActions(t, spec, card) {
       const el = card.body;
       const stem = panelFileName(spec);
       if (!PLOT_KINDS.has(spec.kind)) return [];   // Task 7 adds the table item
       const notDrawn = () => (el.querySelector('.main-svg') ? '' : 'Nothing is drawn yet');
       return [
         { id: 'panel-png', label: 'Download PNG', run: () => downloadPlot(el, 'png', stem), unavailable: notDrawn },
         { id: 'panel-svg', label: 'Download SVG', run: () => downloadPlot(el, 'svg', stem), unavailable: notDrawn },
         { id: 'panel-csv', label: 'Download data (CSV)', unavailable: notDrawn,
           run: () => downloadText(plotCsv(el.data, el._figureInfo || {}), `${stem}.csv`) },
       ];
     }
     ```
     (A table panel's ⋯ shows an empty menu until Task 7; that is fine for one
     commit. If you prefer, hide `more` for table kinds in this task and show
     it in Task 7.)
3. `static/index.html` CSS, next to the `.peaks-toggle` rules:
   ```css
   .panel-menu { position: fixed; }
   .analysis-panel .panel-title .panel-more { height: auto; padding: 1px 4px; flex-shrink: 0; }
   ```

**Test.**
- `test_web_pure_helpers.py`: import `const pe = await import('{origin}/panel_export.js');`.
  Cases:
  - `"csv_quotes": "pe.csvText([['a,b', 'say \"hi\"', 1.5, NaN]])"` →
    `'"a,b","say ""hi""",1.5,\n'`.
  - `"plot_csv": "pe.plotCsv([{x: [0, 1], y: [0.5, 0.25], name: 'MACE'}, {x: [0, 9], y: [0, 9], hoverinfo: 'skip'}], {xTitle: 'Configuration index', yTitle: 'Energy MAE (eV)'})"` →
    `"series,Configuration index,Energy MAE (eV)\nMACE,0,0.5\nMACE,1,0.25\n"`.
  - `"plot_csv_typed": "pe.plotCsv([{x: new Float64Array([1]), y: new Float64Array([2]), name: ''}])"` →
    `"series,x,y\n,1,2\n"`.
- `test_web_runtime.py`, new `test_web_a_plot_downloads_svg_and_csv`
  (setup as `test_web_every_analysis_panel_has_a_letter_and_the_first_is_a`:
  `_preload_dataset_and_prediction`, click the dataset and model rows,
  `_open_analysis_tab(page, "Basic Errors")`, wait
  `_PANEL_HAS_POINTS` for `"Energy MAE timeline"`):
  - `panel = page.locator(".tabpanel.active .analysis-panel[data-title='Energy MAE timeline']")`;
    click `panel.locator(".panel-more")`; expect `#panel-menu-list` visible.
  - `async with page.expect_download() as dl:` click
    `#panel-menu-list [data-action='panel-svg']`; the file name ends with
    `.svg` and its bytes contain `b"<svg"`. If the headless browser fires no
    download event for Plotly's SVG save, assert instead that
    `await page.evaluate("(el) => Plotly.toImage(el, {format: 'svg'})", <the .panel-plot element handle>)`
    starts with `"data:image/svg+xml"`, and say so in the report.
  - Open the menu again; `expect_download` on `[data-action='panel-csv']`;
    the file is named `Energy_MAE_timeline.csv`; its first line starts with
    `series,`; it has more than 2 lines.

Run `test_web_pure_helpers.py` and `test_web_runtime.py -k "downloads_svg or letter or freeze_button or peaks"`.

**Commit:** `feat: a plot's ⋯ menu downloads it as PNG, SVG or CSV`.

---

## Task 7: tables download as CSV

**What the user sees:** a table panel's ⋯ menu offers "Download table (CSV)".
The file has the header row as shown (with units) and full-precision numbers.

**Facts.**
- `static/panels.js` `buildTable`: cells are
  `` `<td>${v === undefined ? '—' : fmt(v, spec.precision)}</td>` ``, where
  `v` comes from `scalarOf(s.data.value)` (a number or null).
- `buildGroupedTable` has
  ```js
  const cell = (s, z, role, allRole) => {
    if (z === 'All') return fmt(scalarOf(s.data[allRole]), spec.precision);
    const ri = order.indexOf(z);
    if (ri < 0) return '—';
    return fmt(values1d(s.data[role])[ri], spec.precision);
  };
  ```
  used as `` `<td>${cell(s, z, 'mae', 'mae_all')}</td>` `` (twice per row form,
  in both modes).
- Tests read cells with `td.textContent`, so a new attribute does not change
  them (`panel_table_grid`, `panel_grouped_table_*` in `test_web_pure_helpers.py`).

**Change.**
1. `static/panels.js`:
   - Near `esc`, add:
     ```js
     /** A table cell showing `v` rounded, with the full number kept in
      * `data-value` for the CSV download (panel_export.js). */
     function valueCell(v, precision) {
       const raw = typeof v === 'number' && Number.isFinite(v) ? ` data-value="${v}"` : '';
       return `<td${raw}>${v === undefined ? '—' : fmt(v, precision)}</td>`;
     }
     ```
   - `buildTable`: replace
     `` return `<td>${v === undefined ? '—' : fmt(v, spec.precision)}</td>`; ``
     with `return valueCell(v, spec.precision);`.
   - `buildGroupedTable`: change `cell` to return the raw number:
     ```js
     const rawCell = (s, z, role, allRole) => {
       if (z === 'All') return scalarOf(s.data[allRole]);
       const ri = order.indexOf(z);
       return ri < 0 ? undefined : values1d(s.data[role])[ri];
     };
     ```
     and replace each `` `<td>${cell(s, z, R, A)}</td>` `` with
     `valueCell(rawCell(s, z, R, A), spec.precision)`. (`fmt(undefined)` is
     already '—'; `valueCell` keeps that.)
2. `static/analysis.js` `_panelActions`: replace the `return [];` for tables with
   ```js
   if (!PLOT_KINDS.has(spec.kind)) {
     const table = () => el.querySelector('table.panel-table');
     return [{ id: 'panel-table-csv', label: 'Download table (CSV)',
       unavailable: () => (table() ? '' : 'Nothing is drawn yet'),
       run: () => downloadText(tableCsv(table()), `${stem}.csv`) }];
   }
   ```

**Test.**
- `test_web_pure_helpers.py` (uses `pe` from Task 6 and `pn`):
  `"table_csv"`:
  ```
  (() => { const s = (v) => ({nd: {values: [v], shape: [1]}});
    const html = pn.buildPanel({kind:'table', precision:2, title:'MAE'}, [{datasetFp:'d1', datasetName:'aspirin',
      modelFp:'m1', modelName:'MACE', name:'MACE', data:{value: s(0.123456)}}], {}).html;
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return pe.tableCsv(doc.querySelector('table')); })()
  ```
  → `"MAE,aspirin\nMACE,0.123456\n"`. A grouped-table case likewise: the
  numbers in the CSV are the raw ones (`0.1`, `0.5`), the screen text stays
  `0.10`. Re-run the existing `panel_table_*` and `panel_grouped_table_*`
  tests: they must pass unchanged.
- `test_web_runtime.py`: extend Task 6's test or add
  `test_web_a_table_downloads_as_csv`: on "Basic Errors", the panel
  `[data-title='Energy MAE']` (a table), ⋯ → `[data-action='panel-table-csv']`,
  download named `Energy_MAE.csv`, two or more lines, the second line has a
  number with more than 2 decimals or is an integer (do not pin a value).

Run `test_web_pure_helpers.py`, `test_web_runtime.py -k "downloads"`.

**Commit:** `feat: a table's ⋯ menu downloads it as CSV`.

---

## Task 8: plot hover shows the series and both values, with a crosshair

**What the user sees:** hovering a point shows a box with the series name and
`<x axis title> = value`, `<y axis title> = value` (6 significant digits), and
dotted lines from the point to both axes, like the desktop's crosshair. The
scatter diagonal shows nothing on hover, as today.

**Facts.**
- Desktop: `UI/Plots.py` `formatHoverText` (label, `x = {x:.6g}`,
  `y = {y:.6g}`, optional `error` that nothing sets) and `onMouseMoved`
  (vertical + horizontal `InfiniteLine` at the point).
- Web today: `baseLayout()` sets `hovermode: 'closest'` and no trace sets a
  `hovertemplate`, so Plotly shows its default `(x, y)` label.
- `buildPanel(spec, series, ctx)` in `static/panels.js` is the one place
  every plot kind passes through:
  ```js
  export function buildPanel(spec, series, ctx) {
    const plot = PLOT_BUILDERS[spec.kind];
    if (plot) return plot(spec, series || [], ctx || {});
    ...
  }
  ```
- Bars use category labels on x (`x: labels`), so their x must not get a
  number format.
- `plotTheme().ink3` is the muted ink colour (`static/theme.js`).

**Change.** `static/panels.js`:
1. New exported function (after `withLegend`):
   ```js
   /**
    * The hover read-out of the desktop's plots (UI/Plots.py formatHoverText):
    * the series, then both values named by the axis titles, and dotted lines
    * from the point to the axes. Helper traces (hoverinfo 'skip') keep none.
    */
   export function withHoverReadout(built) {
     const name = (t, fallback) => String(t || '').replace(/<[^>]*>/g, '') || fallback;
     const xName = name(built.layout.xaxis?.title?.text, 'x');
     const yName = name(built.layout.yaxis?.title?.text, 'y');
     for (const tr of built.traces) {
       if (tr.hoverinfo === 'skip' || tr.hovertemplate) continue;
       const x = tr.type === 'bar' ? '%{x}' : '%{x:.6g}';
       tr.hovertemplate = `${tr.name ? '<b>%{fullData.name}</b><br>' : ''}${xName} = ${x}<br>${yName} = %{y:.6g}<extra></extra>`;
     }
     const spikes = { showspikes: true, spikemode: 'across', spikesnap: 'hovered data',
       spikethickness: 1, spikedash: 'dot', spikecolor: plotTheme().ink3 };
     Object.assign(built.layout.xaxis, spikes);
     Object.assign(built.layout.yaxis, spikes);
     return built;
   }
   ```
2. `buildPanel`: `if (plot) return withHoverReadout(plot(spec, series || [], ctx || {}));`
   (Task 10 puts the panel's label edits on before this call, so the hover
   names the axes as edited.)

**Test.** `test_web_pure_helpers.py`:
- `"hover_timeline"`:
  `(() => { const b = pn.buildPanel({kind: 'timeline', y_label: ['Force MAE', 'u']}, [{datasetFp: 'D', modelFp: 'M', name: 'MACE', data: {y: {nd: {values: [1, 2, 3], shape: [3]}}}}], {units: {}, peaks: false}); return [b.traces[0].hovertemplate, b.layout.xaxis.showspikes, b.layout.yaxis.spikemode]; })()`
  → the template contains `"Configuration index = %{x:.6g}"`, `"%{y:.6g}"`,
  `"<b>%{fullData.name}</b>"`; `True`; `"across"`.
- `"hover_scatter_diagonal"`: a `kind: 'scatter', diagonal: true` panel with
  one series → `b.traces[1].hovertemplate === undefined`.
- `"hover_bars"`: a `kind: 'bars'` panel (copy an existing bars case in the
  file if there is one; else `ctx = {elementOrder: [1, 6], selectedElements: ['All']}`
  and `data: {value: {nd: {values: [0.1, 0.2], shape: [2]}}}`) → template
  contains `"= %{x}<br>"`.
Then run the whole `test_web_pure_helpers.py` (some cases compare traces).

Hover itself is drawn by Plotly; no browser test beyond these.

**Commit:** `feat: plot hover names the series and both values, with crosshair lines`.

---

## Task 9: a tab file keeps a plot's axis titles, text sizes and legend place

**What the user sees:** nothing in the browser yet (Task 10 draws the fields,
Task 11 adds the form). A tab file may now say, for one plot panel, the axis
titles, the text sizes and where the legend sits, and the server keeps them
like every other part of the tab.

**Facts.**
- The panel model is `PanelConfig` in `ffast/config/models.py` (not in
  `ffast/config/tabs.py`: `tabs.py` only turns tabs into the wire layout).
  Today, with `model_config = ConfigDict(extra="forbid")`:
  ```python
  # An axis label is null | "label" | ["label", "<userConfig unit key>"].
  AxisLabel = Union[str, list[str], None]
  ...
      x_label: AxisLabel = None
      y_label: AxisLabel = None
      diagonal: bool = False
      precision: int | None = None   # table decimals; unset = 3 significant digits
  ```
  `x_label`/`y_label` already exist and mean something else: the tab
  author's label plus a unit slot (`y_label = ["Energy MAE", "energyUnit"]`
  in `builtin_tabs/01_basic_errors.toml`). The browser appends the unit
  (`static/panels.js` `axisTitle`). So the user's edits get **new** fields;
  reusing `x_label` would lose the author's label, and "Reset labels" could
  not bring it back.
- The class already has one `@model_validator(mode="after")`,
  `_3d_panel_binds_no_metrics`; `PANEL_KIND_3D` and `Field` are already in
  the module.
- Every panel field reaches the browser unchanged: `ffast/config/tabs.py`
  `_panel_layout` starts with `d = panel.model_dump()` and only replaces
  `metrics`. So no code change in `tabs.py`; only its docstring list.
- A tab file holds what a person would write: `ffast/config/user_tabs.py`
  `_authoring` is `tab.model_dump(mode="json", exclude_defaults=True, exclude_none=True)`.
  An unset field is never written. `TabSources.export` uses `_authoring` too,
  so Export carries the new fields with no change.
- `TabSources.save` (from SAVE_TAB, `ffast/session/server_session.py`
  `_on_save_tab`) validates with `AnalysisTabConfig.model_validate(tab_data)`
  and, for a built-in or project tab, writes a user tab that replaces it:
  `store.write(tab, replaces=editing.tab.name, original=tab_fingerprint(editing.tab))`.
  A refusal is a `TabError` whose text is `where: msg` from pydantic
  (`_validation_message`); the browser shows it in the status line.
- **A trap:** `tab_fingerprint` hashes the **full** dump:
  ```python
  def tab_fingerprint(tab: AnalysisTabConfig) -> str:
      """Identifies a tab's content: equal fingerprints, equal tabs."""
      text = json.dumps(tab.model_dump(mode="json"), sort_keys=True)
      return hashlib.sha256(text.encode()).hexdigest()[:16]
  ```
  New fields change the dump of every tab, so every user tab that already
  replaces a built-in tab would show "edited · original changed" (its
  `original` in `state.json` no longer matches). Unset label fields must be
  left out of the hash.
- The desktop reads project tabs through `UI/panels_toml.py`, which copies
  named fields only (`title`, `x_label`, …), so it ignores the new fields.
  The desktop keeps its own edits in `~/.ffast/display_overrides.json`
  (ADR 0029); leave that alone.
- Tests: `tests/ffast/test_analysis_tabs_config.py` (model and layout,
  e.g. `test_3d_panel_fields_that_do_not_fit_are_config_errors`,
  `test_a_linked_panel_is_written_as_its_place_only`),
  `tests/ffast/test_user_tabs.py` (`_tab`, `_sources`, `SMOOTH`, `VIEW`,
  the `store` fixture), `tests/ffast/session/test_server_session_tabs.py`
  (`_dispatch`, `_layout`, `TABLE`).

**Change.**
1. `ffast/config/models.py`:
   - After the `PanelRole = ...` line add:
     ```python
     # The panel fields the browser's "Edit labels…" writes (web parity plan
     # 2026-10-29); the desktop keeps the same edits in display_overrides.json.
     LABEL_FIELDS = ("x_title", "y_title", "label_size", "legend_size", "legend_x", "legend_y")
     ```
   - In `PanelConfig`, after `precision`, add:
     ```python
     # Label edits made in the browser (web parity plan 2026-10-29). Each
     # replaces what the panel draws by default; unset = the default.
     x_title: str | None = Field(default=None, min_length=1)   # the whole x axis title, unit included
     y_title: str | None = Field(default=None, min_length=1)
     label_size: int | None = Field(default=None, ge=6, le=40)    # px, both axis titles
     legend_size: int | None = Field(default=None, ge=6, le=40)   # px, the legend's text
     legend_x: float | None = Field(default=None, ge=-2, le=3)    # legend place, plot-area fractions
     legend_y: float | None = Field(default=None, ge=-2, le=3)
     ```
     (-2..3 is the range Plotly itself allows for `legend.x`/`legend.y`.)
   - Add to the class docstring one sentence: "``x_title`` … ``legend_y``
     are the user's label edits from the browser (axis title text, text
     sizes, legend place); the desktop ignores them."
   - New validator in `PanelConfig`, after `_3d_panel_binds_no_metrics`:
     ```python
     @model_validator(mode="after")
     def _label_edits_fit(self) -> "PanelConfig":
         """A 3D panel has no axes or legend; a legend place needs both x and y."""
         if self.kind == PANEL_KIND_3D and any(getattr(self, f) is not None for f in LABEL_FIELDS):
             raise ValueError("a 3D panel draws no axes or legend; it takes no label fields")
         if (self.legend_x is None) != (self.legend_y is None):
             raise ValueError("'legend_x' and 'legend_y' are set together")
         return self
     ```
2. `ffast/config/user_tabs.py`:
   - Import `LABEL_FIELDS` with `PANEL_KIND_3D` from `ffast.config.models`.
   - Replace `tab_fingerprint` with:
     ```python
     def tab_fingerprint(tab: AnalysisTabConfig) -> str:
         """Identifies a tab's content: equal fingerprints, equal tabs. Unset
         label fields (added 2026-10-29) are left out, so a tab without label
         edits keeps the fingerprint it had before they existed and no edited
         tab is wrongly marked "original changed"."""
         data = tab.model_dump(mode="json")
         for panel in data["panels"]:
             for key in LABEL_FIELDS:
                 if panel.get(key) is None:
                     panel.pop(key, None)
         text = json.dumps(data, sort_keys=True)
         return hashlib.sha256(text.encode()).hexdigest()[:16]
     ```
3. `ffast/config/tabs.py` `_panel_layout` docstring: add "label edits
   (``x_title`` … ``legend_y``)" to the list of fields that pass through.

**Test.**
- `tests/ffast/test_analysis_tabs_config.py` (import `LABEL_FIELDS` from
  `ffast.config.models`):
  ```python
  PLAIN = {"kind": "timeline", "row": 0, "col": 0, "title": "T",
           "metrics": {"y": {"metric": "ffast.gyradius"}}}
  LABELLED = {**PLAIN, "x_title": "Frame", "y_title": "Rg (Å)", "label_size": 14,
              "legend_size": 12, "legend_x": 0.1, "legend_y": 0.9}
  ```
  - `test_label_edits_parse_and_reach_the_browser`: validate a tab with
    `LABELLED`; `panel = build_tab_layout([tab])[0]["panels"][0]`;
    `{k: panel[k] for k in LABEL_FIELDS} == {k: LABELLED[k] for k in LABEL_FIELDS}`.
  - `test_a_panel_without_label_edits_writes_none`: `_authoring` (import
    inside the test, as `test_a_linked_panel_is_written_as_its_place_only`
    does) of a tab with `PLAIN` → `not set(LABEL_FIELDS) & set(panels[0])`.
  - `test_label_edits_that_do_not_fit_are_config_errors`, parametrized
    `(extra, error)`:
    `({"label_size": 2}, "label_size")`, `({"legend_size": 99}, "legend_size")`,
    `({"legend_x": 9, "legend_y": 0}, "legend_x")`,
    `({"legend_x": 0.5}, "set together")`, `({"y_title": ""}, "y_title")`;
    each `pytest.raises(ValidationError, match=error)` on
    `{"name": "T", "panels": [{**PLAIN, **extra}]}`. Plus one case for
    `{"kind": "3d", "row": 0, "col": 0, "y_title": "x"}` → `match="3D panel"`.
- `tests/ffast/test_user_tabs.py` (import `hashlib`; `LABEL_FIELDS` from
  `ffast.config.models`; `tab_fingerprint` from `ffast.config.user_tabs`):
  - `test_label_edits_are_saved_in_the_tab_file_and_go_with_export(store, tmp_path)`:
    `sources = _sources(store)`; save
    `{"name": "Gyration", "panels": [{**SMOOTH, "y_title": "Rg (Å)", "label_size": 14, "legend_x": 0.1, "legend_y": 0.9}]}`
    with `previous_name="Gyration"`. The entry replaces `"Gyration"`.
    `tomllib.loads((tmp_path / "tabs" / "gyration.toml").read_text())["tabs"][0]["panels"][0]`
    has those four values and no `legend_size`/`x_title`.
    `ProjectConfig.model_validate(tomllib.loads(sources.export("Gyration")))`
    → `visualization.tabs[0].panels[0].y_title == "Rg (Å)"` and `legend_y == 0.9`.
    Then save `{"name": "Gyration", "panels": [SMOOTH]}` with
    `previous_name="Gyration"` (what "Reset labels" sends) → the file's panel
    has none of `LABEL_FIELDS`.
  - `test_a_tab_without_label_edits_keeps_its_fingerprint`:
    `tab = _tab("Gyration", SMOOTH)`; `data = tab.model_dump(mode="json")`;
    delete every `LABEL_FIELDS` key from each panel dict; the old-style hash
    `hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]`
    equals `tab_fingerprint(tab)`; and
    `tab_fingerprint(_tab("Gyration", {**SMOOTH, "y_title": "Rg"}))` differs.
- `tests/ffast/session/test_server_session_tabs.py`, new
  `test_label_edits_reach_every_window(env)`: dispatch `SAVE_TAB` with
  `{"tab": {"name": "Basic", "panels": [{**TABLE, "y_title": "Mine"}]}, "previous_name": "Basic"}`;
  `_layout(everyone)["tabs"][1]["panels"][0]["y_title"] == "Mine"`.

Run `tests/ffast/test_analysis_tabs_config.py`, `tests/ffast/test_user_tabs.py`,
`tests/ffast/session/test_server_session_tabs.py`, `tests/test_panels_toml.py`.

**Commit:** `feat: a tab file keeps a plot's axis titles, text sizes and legend place`.
Body: say a tab file with these fields cannot be read by a server older than
this commit (`extra="forbid"`).

---

## Task 10: plots draw the label fields; a dragged legend is saved in the tab

**What the user sees:** a plot whose tab has label fields draws them: the
edited axis titles, the text sizes, the legend where it was put. Dragging a
plot's legend (when it shows one: more than one series) saves its place in
the tab; the status line says "Saved <tab>". On a built-in tab that first
save makes the user's own copy of the tab, marked "edited", exactly as a
save from Edit mode does ("Reset to original" in the tab menu brings the
built-in back). When this window may not change tabs (Edit mode is open, or
another window has control), the status line says why and the legend goes
back at the next redraw.

**Facts.**
- After Task 8, `static/panels.js`:
  ```js
  export function buildPanel(spec, series, ctx) {
    const plot = PLOT_BUILDERS[spec.kind];
    if (plot) return withHoverReadout(plot(spec, series || [], ctx || {}));
    ...
  }
  ```
  `spec` is the panel as the server sent it, so it carries the Task 9 fields
  (`null` when unset).
- `withLegend` turns the legend on only for more than one trace:
  `layout.legend = { orientation: 'h', y: 1.12, font: { size: plotTheme().fsPlot + 1 } };`.
  `baseLayout()` sets `font: { color: t.ink2, size: t.fsPlot, family: t.font }`
  and axis titles `title: { font: { color: t.ink2 } }` (no size: they use
  `layout.font.size`).
- `renderPanel` keeps facts about the drawn plot on the element
  (`el._figureInfo = { xTitle, yTitle, ... }`, read for the caption line),
  and in the table branch sets each of them to `null`.
- `PLOT_CONFIG` has no `edits`. Plotly 2.35 `edits: { legendPosition: true }`
  lets the legend be dragged; the drag ends in one `plotly_relayout` event
  `{'legend.x': x, 'legend.y': y}` (plot-area fractions, same anchors).
- `static/analysis.js` `_wirePanelInteractions`:
  `el.on('plotly_relayout', () => this._noteZoom(t, spec, card));`.
  `followSize` calls `Plotly.relayout(el, { autosize: true })`: that event
  has no legend key.
- How a change made outside Edit mode is saved today: `static/app.js`
  `_chooseShown` (the 3D panel caption) finds the panel in
  `this._layoutTabs` by `(p) => p.kind === cell.spec.kind && p.row === cell.spec.row && p.col === cell.spec.col`,
  then
  ```js
  const r = await this._tabOps.save(toSaved(next), place.server.name);
  ...
  this._reportTab(r);
  ```
  and refuses first with `this._whyNoEdit()` ('' when allowed).
  `TabOps.save(tab, previousName)` sends SAVE_TAB; `toSaved` (in
  `static/tab_edit.js`) drops `metrics`/`unresolved`, keeps every other panel
  field. Tab specs drawn by `AnalysisManager` are the same objects as
  `this._layoutTabs` (both on the fine grid, `_applyLayout`), so the place
  matches.
- `static/tab_edit.js` has small pure editors such as
  ```js
  export function setPanelStart(tab, index, start) {
    const next = clone(tab);
    ...
    return next;
  }
  ```
- `AnalysisManager` gets its callbacks as `deps` in its constructor
  (`this._onSub = deps.onSub;` …); `app.js` passes them in `new AnalysisManager({...})`,
  including `whyNoMove: () => this._whyNoEdit()`.
- A save sends a new layout to every window, and `setLayout` rebuilds every
  tab, so every plot draws again (zoom is kept, `layout_state.js`). The
  caption drop-down costs the same today; accept it.

**Change.**
1. `static/tab_edit.js`, after `setPanelStart`:
   ```js
   /** The panel fields "Edit labels…" and a legend drag write (web parity
    * plan 2026-10-29; ffast/config/models.py LABEL_FIELDS). */
   export const LABEL_FIELDS = Object.freeze(['x_title', 'y_title', 'label_size', 'legend_size', 'legend_x', 'legend_y']);

   /** Set label fields of panel `index`; null or '' removes that field.
    * Other keys in `patch` are ignored. */
   export function setPanelLabels(tab, index, patch) {
     const next = clone(tab);
     const panel = next.panels[index];
     for (const [key, value] of Object.entries(patch)) {
       if (!LABEL_FIELDS.includes(key)) continue;
       if (value == null || value === '') delete panel[key];
       else panel[key] = value;
     }
     return next;
   }
   ```
2. `static/panels.js`:
   - New exported function before `withHoverReadout`:
     ```js
     /**
      * Put a panel's label edits (tab fields x_title, y_title, label_size,
      * legend_size, legend_x, legend_y; web parity plan 2026-10-29) onto a
      * built plot. Pure. Keeps what they replace in `built.labelDefaults`,
      * for the "Edit labels…" form. Legend fields count only when the plot
      * shows a legend.
      */
     export function applyPanelLabels(spec, built) {
       const { layout } = built;
       built.labelDefaults = {
         x: layout.xaxis?.title?.text || '',
         y: layout.yaxis?.title?.text || '',
         labelSize: layout.font?.size ?? null,
         legendSize: layout.legend?.font?.size ?? null,
         legend: !!layout.showlegend,
       };
       const axes = [layout.xaxis, layout.yaxis].filter(Boolean);
       if (spec.x_title && layout.xaxis) layout.xaxis.title = { ...layout.xaxis.title, text: spec.x_title };
       if (spec.y_title && layout.yaxis) layout.yaxis.title = { ...layout.yaxis.title, text: spec.y_title };
       if (spec.label_size) for (const ax of axes) ax.title = { ...ax.title, font: { ...ax.title?.font, size: spec.label_size } };
       if (layout.showlegend) {
         if (spec.legend_size) layout.legend = { ...layout.legend, font: { ...layout.legend?.font, size: spec.legend_size } };
         if (spec.legend_x != null && spec.legend_y != null) layout.legend = { ...layout.legend, x: spec.legend_x, y: spec.legend_y };
       }
       return built;
     }
     ```
   - `buildPanel`: `if (plot) return withHoverReadout(applyPanelLabels(spec, plot(spec, series || [], ctx || {})));`
     (labels first, so the hover and the caption use the edited titles).
   - `renderPanel`: in the plot branch, before `el._figureInfo = {`, add
     `el._labelDefaults = built.labelDefaults || null;`; in the table branch
     add `el._labelDefaults = null;` with the other fields.
   - `PLOT_CONFIG`: add `edits: { legendPosition: true },` with the comment
     "a dragged legend is saved in the tab (web parity plan 2026-10-29)".
3. `static/protocol.js` `PanelLayout` typedef: add
   `@property {string|null} [x_title]`, `[y_title]` (the user's axis titles),
   `@property {number|null} [label_size]`, `[legend_size]` (px),
   `@property {number|null} [legend_x]`, `[legend_y]` (legend place).
4. `static/app.js`:
   - Import `setPanelLabels` with the other `./tab_edit.js` names.
   - New method next to `_chooseShown`:
     ```js
     /** Save label fields of one plot panel in its tab (web parity plan
      * 2026-10-29: "Edit labels…" and a dragged legend). A null value
      * removes the field. A built-in or project tab gets a user tab
      * replacing it, as an Edit mode save does.
      * @returns {Promise<{ok: boolean, error?: string|null}>} */
     async _savePanelLabels(tabName, spec, patch) {
       const server = this._layoutTabs.find((t) => t.name === tabName);
       const index = (server?.panels || []).findIndex(
         (p) => p.kind === spec.kind && p.row === spec.row && p.col === spec.col);
       const why = this._whyNoEdit() || (index < 0 ? 'This tab is not from the server' : '');
       if (why) { this._setStatus(why, 'error'); return { ok: false, error: why }; }
       const r = await this._tabOps.save(toSaved(setPanelLabels(server, index, patch)), server.name);
       this._reportTab(r);
       return r;
     }
     ```
   - In `new AnalysisManager({...})` add
     `onPanelLabels: (tabName, spec, patch) => this._savePanelLabels(tabName, spec, patch),`.
5. `static/analysis.js`:
   - Constructor: `this._onPanelLabels = deps.onPanelLabels;` next to the
     other callbacks.
   - `_wirePanelInteractions`: replace
     `el.on('plotly_relayout', () => this._noteZoom(t, spec, card));` with
     ```js
     el.on('plotly_relayout', (ev) => {
       // A dragged legend is saved in the tab (web parity plan 2026-10-29).
       if (ev && 'legend.x' in ev && 'legend.y' in ev) {
         const r3 = (v) => Math.round(Number(v) * 1000) / 1000;
         this._onPanelLabels?.(t.spec.name, spec, { legend_x: r3(ev['legend.x']), legend_y: r3(ev['legend.y']) });
       }
       this._noteZoom(t, spec, card);
     });
     ```

**Test.**
- `test_web_pure_helpers.py` (`te`, `TE()` and `pn` are already imported):
  - `"te_set_labels"`:
    `(() => { const a = te.setPanelLabels(TE(), 2, {y_title: 'Mine', label_size: 14, colour: 'red'}); const b = te.setPanelLabels(a, 2, {y_title: null}); return [a.panels[2], b.panels[2], te.toSaved(b).panels[2], TE().panels[2]]; })()`
    → `a` has `y_title == "Mine"`, `label_size == 14`, no `colour`; `b` has
    `label_size == 14` and no `y_title`; the saved form keeps `label_size`;
    `TE()` is unchanged (no `label_size`).
  - `"pn_labels_applied"`: a two-series timeline,
    `pn.buildPanel({kind: 'timeline', y_label: ['Force MAE', 'u'], y_title: 'Mine', label_size: 15, legend_size: 9, legend_x: 0.1, legend_y: 0.9}, [S('MACE'), S('SchNet')], {units: {}, peaks: false})`
    where `S = (name) => ({datasetFp: 'D', modelFp: name, name, data: {y: {nd: {values: [1, 2, 3], shape: [3]}}}})`;
    return `[b.layout.yaxis.title.text, b.layout.yaxis.title.font.size, b.layout.xaxis.title.font.size, b.layout.legend.font.size, b.layout.legend.x, b.layout.legend.y, b.labelDefaults, b.traces[0].hovertemplate]`
    → `"Mine"`, `15`, `15`, `9`, `0.1`, `0.9`; `labelDefaults.y` is the
    old title (starts with `"Force MAE"`), `labelDefaults.legend` is `True`;
    the hover template contains `"Mine = %{y:.6g}"`.
  - `"pn_labels_one_series"`: the same spec with one series → no
    `layout.legend` x (`b.layout.legend?.x` is undefined / `None`), and
    `labelDefaults.legend` is `False`; the y title is still `"Mine"`.
  Then run the whole file (Task 8's hover cases must still pass).
- `test_web_runtime.py`, new `test_web_a_dragged_legend_is_kept_in_the_tab(tmp_path)`:
  `tabs_dir = tmp_path / "tabs"`;
  `async with _spawn_server("--tabs-dir", str(tabs_dir)) as (ws_port, web_port):`
  then set up as `test_web_every_analysis_panel_has_a_letter_and_the_first_is_a`
  (`_preload_dataset_and_prediction`, `page.goto`, `_expect_connected`,
  click the dataset and model rows, `_open_analysis_tab(page, "Basic Errors")`,
  wait `_PANEL_HAS_POINTS` for `"Energy MAE timeline"`).
  - `plot = page.locator(".tabpanel.active .analysis-panel[data-title='Energy MAE timeline'] .panel-plot")`;
    `await plot.evaluate("(el) => Plotly.relayout(el, {'legend.x': 0.2, 'legend.y': 0.8})")`
    (this is the event a drag ends with; one prediction draws no legend,
    but the save path is the same).
  - Wait (poll up to 10 s with `asyncio.sleep(0.1)`) for
    `tabs_dir / "basic-errors.toml"`; in it, the panel whose `title` is
    `"Energy MAE timeline"` has `legend_x == 0.2` and `legend_y == 0.8`.
  - `json.loads((tabs_dir / "state.json").read_text())["replaces"]["basic-errors.toml"]["tab"] == "Basic Errors"`.

Run `test_web_pure_helpers.py`,
`test_web_runtime.py -k "dragged_legend or zoom or sub or peaks or caption"`.

**Commit:** `feat: plots draw their tab's label fields and a dragged legend is saved in the tab`.

---

## Task 11: "Edit labels…" and "Reset labels" in a plot's ⋯ menu

**What the user sees:** a plot's ⋯ menu has, after a separator, "Edit
labels…" and "Reset labels". "Edit labels…" opens a small form: "X axis
title", "Y axis title", "Axis title size", and, when the plot shows a legend,
"Legend text size". Each box shows the default as grey placeholder text; an
empty box means "the default". A note under the title says: "Leave a box
empty for the default. A typed axis title replaces the whole title, unit
included." (plus "Drag the legend to move it." when there is a legend). Save
writes the fields into the tab (Task 10's path); the plot redraws when the
new layout arrives. "Reset labels" removes all six fields, legend place
included. Both are greyed, with the reason, when this window may not change
tabs. On a built-in tab the saved copy stays after a reset (it equals the
original apart from the grid form); "Reset to original" in the tab menu
removes it.

**Facts.**
- `static/dialogs.js` has `askDialog({title, message, buttons, danger})`
  (built from `.warning-modal`, `.warning-modal-panel`,
  `.warning-modal-title`, `.warning-modal-footer`, `labelBy`, `trapFocus`;
  Escape → `done(null)`). There is no form dialog: one is new here.
- After Task 10: `card.body._labelDefaults` (`{x, y, labelSize, legendSize, legend}`)
  on a drawn plot; `spec.x_title` … `spec.legend_y` on the panel spec;
  `LABEL_FIELDS` in `tab_edit.js`; `this._onPanelLabels(tabName, spec, patch)`
  in `AnalysisManager`; `this._whyNoMove()` is app's `_whyNoEdit()`.
- Task 6's `_panelActions(t, spec, card)` returns the plot list with
  `notDrawn`. `fillMenu` in `actions.js` draws `{separator: true}` as a line.
- `analysis.js` imports `unitPopover` from `./dialogs.js` and
  `GRID_COLUMNS, ROW_UNIT_PX, toFineGrid` from `./tab_edit.js`.

**Change.**
1. `static/dialogs.js`, new export after `askDialog`:
   ```js
   /**
    * A small form: one box per field. Resolves with the boxes' text in field
    * order when the last button is chosen (or Enter is pressed in a box), and
    * null otherwise (another button, Escape).
    * @param {{title: string, note?: string, buttons?: string[],
    *   fields: Array<{label: string, value: string, placeholder?: string,
    *   type?: 'text'|'number', min?: number, max?: number}>}} o
    * @returns {Promise<string[]|null>}
    */
   export function fieldsDialog({ title, note = '', fields, buttons = ['Cancel', 'Save'] }) { ... }
   ```
   Build it like `askDialog` (same classes, `labelBy`, `trapFocus`, Escape →
   null), with `modal.className = 'warning-modal ask-dialog fields-dialog'`;
   an optional `<p class="warning-modal-message">` with `note`; for each
   field a `<label>` holding its text and an `<input>` (`type` default
   `'text'`, `value`, `placeholder`, and `min`/`max` when given); focus goes
   to the first input.
2. `static/index.html` CSS: `.fields-dialog label { display: flex; align-items: center; gap: 8px; margin: 6px 0; }`
   and `.fields-dialog label input { flex: 1; min-width: 0; }`.
3. `static/analysis.js`:
   - Imports: add `fieldsDialog` to the `./dialogs.js` line and
     `LABEL_FIELDS` to the `./tab_edit.js` line.
   - In `_panelActions`, append to the plot list:
     ```js
     { separator: true },
     { id: 'panel-labels', label: 'Edit labels…',
       unavailable: () => notDrawn() || this._whyNoMove?.() || '',
       run: () => this._editLabels(t, spec, card) },
     { id: 'panel-labels-reset', label: 'Reset labels',
       unavailable: () => this._whyNoMove?.()
         || (LABEL_FIELDS.some((k) => spec[k] != null) ? '' : 'No labels changed'),
       run: () => this._onPanelLabels?.(t.spec.name, spec,
         Object.fromEntries(LABEL_FIELDS.map((k) => [k, null]))) },
     ```
   - New method:
     ```js
     /** Edit a plot's axis titles and text sizes (web parity plan
      * 2026-10-29, decisions 4-6); saved in the tab. */
     async _editLabels(t, spec, card) {
       const d = card.body._labelDefaults || { x: '', y: '', labelSize: null, legendSize: null, legend: false };
       const strip = (s) => String(s ?? '').replace(/<[^>]*>/g, '');
       const sizeBox = (label, value, fallback) => ({
         label, type: 'number', min: 6, max: 40, value: String(value ?? ''), placeholder: strip(fallback) });
       const fields = [
         { label: 'X axis title', value: spec.x_title || '', placeholder: strip(d.x) },
         { label: 'Y axis title', value: spec.y_title || '', placeholder: strip(d.y) },
         sizeBox('Axis title size', spec.label_size, d.labelSize),
       ];
       if (d.legend) fields.push(sizeBox('Legend text size', spec.legend_size, d.legendSize));
       const values = await fieldsDialog({
         title: `Labels of ${strip(spec.title) || 'this plot'}`,
         note: 'Leave a box empty for the default. A typed axis title replaces the whole title, unit included.'
           + (d.legend ? ' Drag the legend to move it.' : ''),
         fields,
       });
       if (!values) return;
       const text = (v) => v.trim() || null;
       const size = (v) => { const n = Number(v); return v.trim() && Number.isFinite(n) ? Math.round(n) : null; };
       const patch = { x_title: text(values[0]), y_title: text(values[1]), label_size: size(values[2]) };
       if (d.legend) patch.legend_size = size(values[3]);
       await this._onPanelLabels?.(t.spec.name, spec, patch);
     }
     ```
     A size outside 6-40 is refused by the server (Task 9); its message
     reaches the status line through `_reportTab`.

**Test.**
- `test_web_runtime.py`, new `test_web_a_plots_labels_are_edited_saved_and_reset(tmp_path)`
  (server and setup as Task 10's test, on "Energy MAE timeline";
  `panel = page.locator(".tabpanel.active .analysis-panel[data-title='Energy MAE timeline']")`,
  `plot = panel.locator(".panel-plot")`):
  - Click `panel.locator(".panel-more")`, then
    `#panel-menu-list [data-action='panel-labels']`; the `.fields-dialog`
    shows. Fill the input inside the label with text "Y axis title" with
    `My energy` and the one inside "Axis title size" with `16`; click
    "Save".
  - `await page.wait_for_function("() => { const el = document.querySelector(\".tabpanel.active .analysis-panel[data-title='Energy MAE timeline'] .panel-plot\"); return el?.layout?.yaxis?.title?.text === 'My energy' && el.layout.yaxis.title.font.size === 16; }")`
    (look the element up inside the function: the layout save rebuilds the
    panel, so an element handle taken before would be stale).
  - `tabs_dir / "basic-errors.toml"`: the panel titled
    `"Energy MAE timeline"` has `y_title == "My energy"` and
    `label_size == 16`, and no `x_title`.
  - The caption line under the plot (`.panel-caption-line`) contains
    `My energy`, since the caption is built from the drawn titles.
  - Reload the page (`page.reload`), `_expect_connected`, click the dataset
    and model rows, open "Basic Errors" again: the y title is still
    `My energy` (it came from the server).
  - ⋯ → `[data-action='panel-labels-reset']`; wait until the y title is no
    longer `My energy`; the TOML panel has none of the six fields.
- `test_web_pure_helpers.py` has `di` (dialogs.js): add
  `"fields_dialog"`: open
  `di.fieldsDialog({title: 'T', fields: [{label: 'A', value: 'a'}, {label: 'B', value: ''}, {label: 'N', value: '', type: 'number', min: 6, max: 40}]})`,
  set the second input to `'b'` and the third to `'12'`, click the last
  footer button, and return the awaited value together with the third
  input's `type` and `min` read before the click → `[["a", "b", "12"], "number", "6"]`.
  Write it as `"await (async () => { ... })()"`, the way the existing
  `"theme_dark_is_root"` case does: start the dialog without awaiting it,
  fill and click, then `return await` the promise.

Run `test_web_pure_helpers.py`, `test_web_runtime.py -k "labels or dragged_legend or caption or unit_named"`.

**Commit:** `feat: a plot's axis titles and text sizes can be edited and reset from its ⋯ menu`.

---

## Task 12: hover highlight with no tool armed

**What the user sees:** with no pick tool armed, the atom under the pointer is
drawn larger and lighter, as on the desktop (`UI/loupe/canvas.py`
`on_mouse_move` calls `set_transient_highlight` always). Dragging to orbit
does not highlight.

**Facts.** `static/picking.js`:
```js
_onHoverMove(e) {
  if (!this._tool || e.buttons) return;   // no tool, or mid-drag
  this._hoverAt = this._canvasXY(e);
  if (this._hoverFrame != null) return;
  this._hoverFrame = requestAnimationFrame(() => {
    this._hoverFrame = null;
    if (!this._tool || !this._hoverAt) return;
    const hit = this._renderer.pickAtom(this._hoverAt.x, this._hoverAt.y);
    this._renderer.setHoveredAtom(hit ? hit.displayIndex : null);
  });
}
```
`pickAtom` is one pass over the atoms per animation frame; `setHoveredAtom`
rewrites one instance. The file comment says "While armed, the atom a click
would pick is highlighted"; `test_web_armed_tool_highlights_the_atom_under_the_pointer`
asserts "No tool armed: hovering does nothing".

**Change.** `static/picking.js`: drop `!this._tool ||` from both conditions
(keep `e.buttons`, which skips orbit drags). Update the top-of-file comment:
the atom under the pointer is highlighted whether or not a tool is armed
(desktop parity). `disarm()` still clears the hover; keep it.

**Test.** `test_web_runtime.py`
`test_web_armed_tool_highlights_the_atom_under_the_pointer`: rename to
`test_web_the_atom_under_the_pointer_is_highlighted`, update the docstring,
and replace the "No tool armed" block with: move to the atom with no tool;
`wait_for_function(... hoveredAtom === atom['index'])`; then press the mouse
button, move 30 px, release (an orbit drag); move the pointer off the
molecule; `hoveredAtom === null`. Keep the rest (armed tool, larger and
lighter, back to normal).

Run `test_web_runtime.py -k "highlight or pick"`.

**Commit:** `feat: the 3D view highlights the atom under the pointer, as the desktop does`.

---

## When done

Report: the commit hashes, the tests you added, anything in this plan that did
not match the code and what you did about it (especially Task 6's SVG download
check, Task 10's legend-drag event keys, and Task 11's dialog test
placement), and any test that was red (with its output). Do not push.
