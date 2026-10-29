# Web loading parity: loader types, key picker, every Nth frame, models: implementation plan (9 tasks)

Part of the web parity plan 2026-10-29. There is no ADR for this work: the
desktop (Qt) client already does all of it, and this plan says how the web
client gets the same. Do the tasks **in the order given**, one commit per
task. Each task leaves the app working.

Words used below:
- **probe**: a small server request that reads only the start of a file and
  answers with what it found (`PROBE_DATASET_KEYS` answers the energy and
  force key names; `PROBE_DATASET_LENGTH` answers the frame count).
- **key**: the name under which a file stores energies or forces, for example
  `REF_energy` or `MACE_forces`. "Calculator" means ASE's built-in
  `energy=` / `forces` columns; the web sends the literal `'energy'` /
  `'forces'` for it, which `ffast/loaders/ase.py` already maps to the
  calculator (search `elif self.selected_energy_key == "energy":`).
- **slice_num**: the loader's stride number. `0` = load every frame, read in
  parts as needed (today's web default); `N > 1` = keep every Nth frame;
  `-1` = read every frame into memory at once.
- **the loader dialog**: the web's file-picker modal `#fb-modal`, run by
  `RemoteBrowser` in `static/remote_browser.js`.

## Decisions (confirmed by the user 2026-10-29)

1. **Loader types come with the connect replay**: a new `LOADER_TYPES`
   message the server sends to every new window, like the metric catalog.
   The dataset **Type** is guessed from the file's extension (`.npz` →
   `sGDML`, anything else → `ase (auto)`); the user can still change it.
2. **The dataset key picker shows only when the file offers a choice**
   (more than one energy option or more than one force option, the desktop
   rule `resolve_key_options` in `UI/menuLogic.py`). Otherwise the dataset
   loads with the server's default keys, as today.
3. **"Every Nth frame" is a field that is always in the dataset loader**
   (default 1). Files of 3 GB or more also get a note with the frame count.
   There is no "read all into memory" choice: the web keeps today's
   read-in-parts default.
4. **A dataset opened from the Recent list loads at once** with its default
   keys and no key check (today's behaviour). Only a file picked in the file
   list is probed, and the picker shows only when it offers a choice (rule 2).
5. **A freshly loaded model or zero model is not selected automatically.**
   It appears in the rail's predictions list; the user selects it there.
6. **Load Model… and Load Zero Model are File menu items only** in this
   plan. Their keyboard shortcuts come from plan 3
   (`2026-10-29-web-parity-3-workspace.md`), not from here.
7. **No "3 GB auto-compute guard".** The desktop guard (`UI/ContentTab.py`
   `_isLargeSelection`, `UI/Plots.py` `onAutoCompute`) stops the desktop
   from computing *every* plot on every selection change. The web already
   draws only the tab on screen (`static/analysis.js` header: "2D panels
   render lazily: a tab's panels are (re)fetched when it becomes the active
   tab"), so the large-file note of decision 3 is enough.

## Rules for the whole job

1. **Do not touch** files this plan does not name. In particular leave
   `CONTEXT.md`, `docs/adr/README.md`, every file under `docs/` (including
   every `docs/implementation/*` file) and every untracked file
   (`to_fix*.md`, `docs/research/...`, `docs/adr/0059-*`, `docs/adr/0068-*`,
   `docs/adr/0070-*`) alone: the user commits them. You edit code and tests
   only. Stage with `git add <exact paths>`, never `git add -A` or
   `git add .`.
2. The user may commit while you work. Run `git status --short` and
   `git log --oneline -3` before each commit. Never `git reset --hard`, never
   rebase, never amend a commit you did not just make.
3. Commit messages: subject starts with `feat:` (or `fix:` for a fix), in
   plain words about what the user sees; see `git log --oneline -20` for the
   style. Body: 2–4 short lines on what changed and why, naming "web parity
   plan 2026-10-29". **No `Co-Authored-By` line.**
4. Tests run with the project venv, never `uv run`:
   ```
   ~/.venvs/ffast_env/bin/python -m pytest <file> -q
   ```
   `-n 6` is on by default (pyproject). Add `-n 0` only to debug one test.
   Browser tests (under `tests/ffast/renderers/web/`) need Playwright's
   Chromium and are slow: run only the tests named in the task (`-k`).
5. After each task, run the fast suite once:
   ```
   ~/.venvs/ffast_env/bin/python -m pytest -m "not integration" -q
   ```
   It must stay green (one known order-dependent red test is described in
   `docs/issues/0067-01-*.md`; ignore only that one).
6. Match the surrounding code: a short docstring or JSDoc comment above each
   new function, saying *why* in plain words; 2-space indent in JS; the same
   naming style as the file. Where a reader would ask "why is it like this?",
   say "as the desktop does" and name the desktop file, not on every line.
7. If the plan does not match the code (a line moved, a name differs), find
   the place by searching for the quoted code, not by line number. If the plan
   is wrong about **behaviour**, stop and report; don't invent another design.
8. **Do not install or try ML backends** (MACE, NequIP, SchNet, sGDML,
   SpookyNet). Model loading is tested only through its failure path.
9. **The zero model loads only when the user asks** (File ▸ Load Zero
   Model). Never send `LOAD_ZERO_MODEL` from any automatic path.

Paths are relative to the repo root. `static/` means
`ffast/renderers/web/static/`.

### A new test file for the browser tests of this plan

Tasks 2–9 add browser tests to a **new** file
`tests/ffast/renderers/web/test_web_loading.py` (the runtime test file is
already 6000+ lines). Create it in Task 2 with this head, copying the import
style of `tests/ffast/renderers/web/test_metric_boxes_pane.py`:

```python
"""The web loader against a real server: loader types, energy/force keys,
every Nth frame, .npz predictions, models (web parity plan 2026-10-29)."""
import re

import ase.io
import numpy as np
import pytest
from playwright.async_api import async_playwright, expect

from .test_web_runtime import (
    DATASET_PATH, PREDICTION_PATH, REPO_ROOT, _RECORD_SENDS, _expect_connected,
    _file_menu, _open_loupe, _preload_dataset, _preload_uniform_dataset,
    _spawn_server, _wait_for_server_ready,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def ffast_web_server():
    async with _spawn_server() as ports:
        yield ports


async def _go_to(page, folder):
    """In the open loader, list `folder`."""
    await page.locator("#fb-path").fill(str(folder))
    await page.locator("#fb-path").press("Enter")


async def _sent(page, event):
    """The kwargs of every `event` the page sent since _RECORD_SENDS ran."""
    return await page.evaluate(
        "(e) => window.__sent.filter(([name]) => name === e).map(([, kw]) => kw)", event)
```

Each browser test opens the page like the existing ones:
`browser = await p.chromium.launch(headless=True)`,
`page = await browser.new_page(viewport={"width": 1200, "height": 820})`,
`await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")`,
`await _expect_connected(page)`, and closes the browser in `finally`.
`_RECORD_SENDS` (in `test_web_runtime.py`) wraps `window.ffastApp._conn.send`
so `window.__sent` lists `[event, kwargs]` of every message sent; run it with
`await page.evaluate(_RECORD_SENDS)` after connecting.

The pure JS helpers added in this plan are tested in
`tests/ffast/renderers/web/test_web_pure_helpers.py`: add an entry to the
`CASES` dict (a JS expression; `rb` is `remote_browser.js`), then a
`def test_...(results):` that asserts `results["<name>"]`. Copy the
`load_*` / `keys_*` entries and `test_can_load_*` tests there.

---

## Task 1: the server says which loaders it has (LOADER_TYPES)

**What the user sees:** nothing yet; this is the server half of Task 2.

**Facts.**
- Loader plugins register by name into two dicts on the environment:
  `ffast/core/environment.py` `self.modelTypes = {}` and
  `self.datasetTypes = {}`, filled by `initialiseModelType` /
  `initialiseDatasetType`. Today the registered dataset types are
  `"ase (auto)"` (an *instance*, `ffast/plugins/loaders/ase.py`
  `SmartASELoader`, `datasetFileExtension = "*"`) and `"sGDML"`
  (`ffast/plugins/models/sgdml.py`, `datasetFileExtension = "*.npz"`).
  Model types: `sGDML` (`*.npz`), and, when their backend imports, `MACE`
  (`*.model`), `Nequip` (`*.pth`), `SchNet`, `SpookyNet` (these two inherit
  `modelFileExtension = "*"` from `ffast/loaders/model.py`).
- No message lists them today (searched `datasetTypes` / `modelTypes` in
  `ffast/session`, `ffast/protocol`, `server.py`: none).
- `ffast/session/window_feed.py` `WindowFeed.replay()` builds what a new
  window gets: each dataset, each model, then `METRIC_CATALOG`, then the
  rest of `_PIECES`:
  ```python
        # ── the catalog ─────────────────────────────────────────────────────
        try:
            out.append((self.metric_catalog(), "METRIC_CATALOG"))
        except Exception as exc:
            logger.warning("State replay: METRIC_CATALOG error: %s", exc)
  ```
  A test (`tests/ffast/session/test_server_session_user_metrics.py`
  `test_the_replay_ends_with_who_uses_what`) requires the replay to **end**
  with `METRIC_USERS`, so the new message goes **before** the catalog.
- `env` may be `None` in tests (`ServerSession(None, out).replay()` in
  `tests/ffast/test_atom_coloring.py`); the feed reads env attributes with
  `getattr(env, name, None)`.
- `ffast/protocol/control.py` lists one-shot announcements with a comment
  each (search `TAB_LAYOUT = "TAB_LAYOUT"`).

**Change.**

1. `ffast/protocol/control.py`: after the `TAB_LAYOUT` constant and its
   comment, add:
   ```python
   # Which dataset and model loaders this server has (its plugins):
   # {datasets: [{name, extension}], models: [{name, extension}]}, names sorted
   # case-blind. Pushed to a window on connect only: plugins load once, at
   # start (web parity plan 2026-10-29). A dedicated announcement, out of
   # REPLY_EVENTS.
   LOADER_TYPES = "LOADER_TYPES"
   ```
   Do **not** add it to `CLIENT_TO_SERVER` (the window never sends it).
2. `ffast/session/window_feed.py`:
   - New builder after `metric_users()`:
     ```python
     def loader_types(self) -> bytes:
         """LOADER_TYPES: the dataset and model loaders the plugins registered,
         so the web loader lists the server's types instead of a fixed pair."""
         from ffast.protocol.rpc import pack

         def rows(registry, attr):
             items = sorted((registry or {}).items(), key=lambda kv: kv[0].lower())
             return [{"name": name, "extension": getattr(cls, attr, None) or "*"}
                     for name, cls in items]

         return pack(control.LOADER_TYPES, [], {
             "datasets": rows(getattr(self.env, "datasetTypes", None), "datasetFileExtension"),
             "models": rows(getattr(self.env, "modelTypes", None), "modelFileExtension"),
         })
     ```
   - In `replay()`, just **before** the `# ── the catalog ──` block, add
     the same try/except shape:
     ```python
         # ── the loaders this server has (they never change while it runs) ──
         try:
             out.append((self.loader_types(), control.LOADER_TYPES))
         except Exception as exc:
             logger.warning("State replay: LOADER_TYPES error: %s", exc)
     ```
   - Update the `replay()` docstring sentence to name "the loader types".
   - Do **not** add it to `_PIECES` (it is never re-broadcast).

**Test.**
- `tests/ffast/session/test_window_feed.py`:
  - `test_replay_order`: the expected list becomes
    `[REMOTE_DATASET_META, REMOTE_MODEL_META, LOADER_TYPES, METRIC_CATALOG, TAB_LAYOUT, USER_METRICS, METRIC_USERS]`.
  - New `test_loader_types_lists_the_registered_loaders_sorted`: an `_Env`
    subclass with
    `datasetTypes = {"sGDML": type("S", (), {"datasetFileExtension": "*.npz"}), "ase (auto)": type("A", (), {"datasetFileExtension": "*"})()}`
    and `modelTypes = {"MACE": type("M", (), {"modelFileExtension": "*.model"}), "SchNet": type("N", (), {})}`;
    `event, _, kw = unpack(WindowFeed(env, lambda d: None).loader_types())`;
    assert `event == control.LOADER_TYPES`,
    `kw["datasets"] == [{"name": "ase (auto)", "extension": "*"}, {"name": "sGDML", "extension": "*.npz"}]`,
    `kw["models"] == [{"name": "MACE", "extension": "*.model"}, {"name": "SchNet", "extension": "*"}]`.
  - New `test_loader_types_without_an_env_is_empty`:
    `WindowFeed(None, ...)` → both lists are `[]`.
- `tests/ffast/session/test_server_session.py`
  `test_replay_empty_env_enqueues_only_the_shared_pieces`: the expected list
  becomes `["LOADER_TYPES", "METRIC_CATALOG", "TAB_LAYOUT", "USER_METRICS", "METRIC_USERS"]`.
- Run `tests/ffast/session/` and `tests/ffast/test_atom_coloring.py`.

**Commit:** `feat: the server tells a new window which loaders it has`.

---

## Task 2: the dataset Type list comes from the server and follows the file

**What the user sees:** the loader's **Type** list shows every dataset
loader the server has (today `ase (auto)` and `sGDML`; a plugin's own type
too). The Type is **guessed from the file's extension** (decision 1):
picking a `.npz` file switches Type to `sGDML` by itself; any other file
switches it to `ase (auto)`. The guess only sets the list's value: the user
can still pick another Type before Load, and Load sends what the list shows.
A Recent dataset gets the same guess (it loads at once, so there the guess
is final).

**Facts.**
- `static/index.html`, inside `#fb-dataset-fields`:
  ```html
          <select id="fb-type">
            <option value="ase (auto)">ase (auto)</option>
            <option value="sGDML">sGDML</option>
          </select>
  ```
- `static/remote_browser.js` `_load()` reads `$('fb-type').value` and sends
  `this._ports.send(OUT.LOAD_DATASET, {}, [path, typ]);`.
- `static/app.js` registers handlers before `await conn.connect()` (search
  `conn.on(IN.DATASET_KEYS_RESPONSE`); replay messages that arrive during
  the handshake reach these handlers.
- `static/events.js` `IN` must list every inbound name;
  `tests/ffast/renderers/web/test_event_constants.py`
  `test_inbound_events_are_things_the_server_sends` has an `announcements`
  set that must include it.
- `_render(entries)` builds one `.fb-row` per entry `e` = `{name, is_dir, size}`.
- `_useRecent(path)` loads a Recent dataset at once with whatever
  `#fb-type` shows (it calls `this._load()`, which reads `$('fb-type').value`):
  ```js
      if (this._mode === 'dataset') {
        this._path = dir;
        this._selected = name;
        this._load();
      } else {
  ```

**Change.**

1. `static/events.js`: in `IN`, after `TAB_LAYOUT`, add
   `// The dataset and model loaders the server has, on connect (web parity plan 2026-10-29).`
   and `LOADER_TYPES: 'LOADER_TYPES',`.
2. `tests/ffast/renderers/web/test_event_constants.py`: add
   `control.LOADER_TYPES` to the `announcements` set.
3. `static/index.html`: keep only the first option in `#fb-type`
   (`<option value="ase (auto)">ase (auto)</option>`), so the loader still
   works if the message never comes. Delete the `sGDML` option.
4. `static/remote_browser.js`:
   - New exported pure helper, below `keyOptions`:
     ```js
     /**
      * The loader type a file most likely needs: the first type whose
      * extension pattern (`*.npz`) the name ends with, else the catch-all
      * `fallback` when it is offered, else the first type. The desktop's
      * file dialog does this with its filter list.
      * @param {string} name @param {{name: string, extension: string}[]} types
      * @param {string} fallback @returns {string}
      */
     export function typeForFile(name, types, fallback = 'ase (auto)') {
       const lower = name.toLowerCase();
       const hit = (types || []).find((t) => t.extension && t.extension !== '*'
         && lower.endsWith(t.extension.replace(/^\*/, '').toLowerCase()));
       if (hit) return hit.name;
       if ((types || []).some((t) => t.name === fallback)) return fallback;
       return types?.[0]?.name ?? fallback;
     }
     ```
   - In the constructor: `this._types = { datasets: [{ name: 'ase (auto)', extension: '*' }], models: [] };`
   - New method:
     ```js
     /** The server's loaders (LOADER_TYPES, on connect): fill the Type list. */
     setLoaderTypes(kw) {
       this._types = { datasets: kw.datasets || [], models: kw.models || [] };
       if (!this._types.datasets.length) this._types.datasets = [{ name: 'ase (auto)', extension: '*' }];
       this._fillSelect($('fb-type'), this._types.datasets.map((t) => ({ value: t.name, label: t.name })));
       $('fb-type').value = typeForFile('', this._types.datasets);
     }
     ```
   - In `_selectFile(row, name)`, before `this._refreshLoadEnabled();`, add
     `if (this._mode === 'dataset') $('fb-type').value = typeForFile(name, this._types.datasets);`
     (a guess: the user can change the list afterwards; nothing resets it
     until another file is clicked).
   - In `_useRecent(path)`'s dataset branch, before `this._load();`, add
     `$('fb-type').value = typeForFile(name, this._types.datasets);   // guessed from the extension (decision 1)`.
5. `static/app.js`: next to `conn.on(IN.DATASET_KEYS_RESPONSE, …)` add
   `conn.on(IN.LOADER_TYPES, (kw) => this._browser.setLoaderTypes(kw));`.

**Test.**
- `test_web_pure_helpers.py` cases (types =
  `[{name:'ase (auto)',extension:'*'},{name:'sGDML',extension:'*.npz'}]`):
  `typeForFile('m.NPZ', T)` → `'sGDML'`; `typeForFile('a.xyz', T)` →
  `'ase (auto)'`; `typeForFile('a.xyz', [{name:'X', extension:'*.x'}])` →
  `'X'`; `typeForFile('a', [])` → `'ase (auto)'`.
- Create `tests/ffast/renderers/web/test_web_loading.py` (head above) with
  `test_web_the_type_list_is_the_servers_and_follows_the_file`:
  `await _wait_for_server_ready(ws_port)`, connect, `_file_menu(page, "Load Dataset…")`;
  the options of `#fb-type` (`evaluate_all("(os) => os.map((o) => o.value)")`
  on `#fb-type option`) equal `["ase (auto)", "sGDML"]`; write
  `np.savez(tmp_path / "m.npz", x=np.zeros(1))` and copy nothing else;
  `_go_to(page, tmp_path)`, click the `m.npz` row → `#fb-type` has value
  `sGDML`; write `ase.io.write(tmp_path / "a.xyz", ase.io.read(DATASET_PATH, index=0))`,
  re-list (`_go_to(page, tmp_path)` again) and click `a.xyz` → value
  `ase (auto)`; then `select_option("sGDML")` on `#fb-type` → the value
  stays `sGDML` (the guess does not override the user). The test takes
  `tmp_path` and `ffast_web_server`.
- Run `test_event_constants.py`, the pure helpers file, the new test, and
  in `test_web_runtime.py` `-k "loader or cannot_be_loaded or units"`.

**Commit:** `feat: the web loader lists the server's dataset types and picks one by file`.

---

## Task 3: choose the energy and force keys when loading a dataset

**What the user sees:** after clicking an ASE file that has more than one
energy key or more than one force key (or a key plus the calculator), a
**Keys** row appears above the loader's buttons: "Reference: Energy [▾]
Force [▾]". It starts on the first named key, like the desktop. Load uses
the chosen keys. A file with one key of each loads exactly as today, with
no row. Load waits (greyed) for the few ms the key check takes.
A dataset clicked in the **Recent** list is not checked: it loads at once
with the server's default keys, exactly as today (decision 4). To choose
keys for a Recent file, the user picks it in the file list instead.

**Facts.**
- Desktop: `UI/mainMenu.py` `onDatasetLoad` → `_showASEKeySelectionDialog`
  probes the first frame, then
  `need_dialog, default_energy, default_force = resolve_key_options(...)`
  (`UI/menuLogic.py`):
  ```python
      energy_options = len(energy_keys) + (1 if has_calc_energy else 0)
      force_options = len(force_keys) + (1 if has_calc_forces else 0)
      need_dialog = energy_options > 1 or force_options > 1
  ```
  `UI/KeySelectionDialog.py` defaults each combo to the first named key
  ("Skip calculator by default"). Port this rule; drop the Qt dialog.
- The server already accepts the keys:
  `ffast/session/server_session.py` `_on_load_dataset(self, path, dataset_type, **kwargs)`
  passes `selected_energy_key`, `selected_force_key`, `prediction_keys`,
  `slice_num` on to `env.taskLoadDataset`; `ffast/protocol/messages.py`
  `LoadDatasetRequest` declares exactly these optional fields (extra keys are
  refused). Keys are honoured only for `dataset_type == "ase (auto)"`
  (`ffast/core/loading_coordinator.py` `loadDataset`).
- The web prediction path already probes (`static/remote_browser.js`):
  ```js
      this._ports.send(OUT.PROBE_DATASET_KEYS, {}, [path, 'ase (auto)']);
  ```
  and `onDatasetKeys(path, kw)` returns at once unless `this._mode === 'prediction'`.
  The reply is `DATASET_KEYS_RESPONSE` with `args[0] = path` and kwargs
  `{energy_keys, force_keys, has_calculator_energy, has_calculator_forces, error}`.
- `keyOptions(keys, {allowNone, calculator, calculatorValue})` builds
  `[{value, label}]` (calculator first, then keys).
- The server logs a failed probe twice with `logger.warning(...)` in
  `_on_probe_dataset_keys`, and every WARNING pops up in every window
  (`ffast/session/warning_relay.py`) unless logged with
  `extra={"popup": False}`. Once datasets are probed, a non-dataset file
  (the existing test `test_web_a_dataset_that_cannot_be_loaded_says_so_in_the_status`
  clicks `README.md`) would add two pop-ups; that test expects exactly one.
- `_useRecent(path)` loads a Recent dataset at once (after Task 2 it also
  sets the guessed Type first):
  ```js
      if (this._mode === 'dataset') {
        this._path = dir;
        this._selected = name;
        $('fb-type').value = typeForFile(name, this._types.datasets);   // guessed from the extension (decision 1)
        this._load();
      } else {
        this._selectAfterListing = name;
        this._navigate(dir);
      }
  ```
  This stays a load-at-once (decision 4). But the Keys row may still be
  showing from a file clicked earlier in the same open loader, and `_load()`
  (below) sends the keys whenever the row is visible. So `_useRecent` must
  hide the row first.
- The row handlers: `row.addEventListener('dblclick', () => { this._selectFile(row, e.name); if (!$('fb-load').disabled) this._load(); });`
  and in `_listKey`, `act.load` does `row.click(); if (!$('fb-load').disabled) this._load();`.

**Change.**

1. `ffast/session/server_session.py` `_on_probe_dataset_keys` and
   `_on_probe_dataset_length`: add `extra={"popup": False}` to each of
   their two `logger.warning(...)` calls (the reply carries `error`; the
   window that asked decides what to show).
2. `static/index.html`:
   - Between `<div id="fb-list"></div>` and `<div id="fb-footer">` add:
     ```html
      <!-- Which energy/force keys a dataset uses, when its file offers a
           choice (as the desktop's key dialog). -->
      <div id="fb-keys" hidden>
        <div class="fb-keys-row">
          <span class="fb-keys-head">Reference</span>
          <label for="fb-ds-energy-key">Energy</label>
          <select id="fb-ds-energy-key"></select>
          <label for="fb-ds-force-key">Force</label>
          <select id="fb-ds-force-key"></select>
        </div>
      </div>
     ```
   - CSS next to the `#fb-recent` rules:
     ```css
     #fb-keys { padding: 6px 12px; border-top: 1px solid var(--line); }
     #fb-keys[hidden] { display: none; }
     #fb-keys .fb-keys-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
     #fb-keys .fb-keys-head, #fb-keys label { font-size: var(--fs-s); color: var(--muted); font-weight: 600; }
     ```
3. `static/remote_browser.js`:
   - New exported pure helpers below `keyOptions`:
     ```js
     /** Whether a probed file offers a key choice: more than one energy
      * option or more than one force option, the calculator counting as one
      * (the desktop's resolve_key_options, UI/menuLogic.py). */
     export function needsKeyChoice(probe) {
       const e = (probe?.energy_keys || []).length + (probe?.has_calculator_energy ? 1 : 0);
       const f = (probe?.force_keys || []).length + (probe?.has_calculator_forces ? 1 : 0);
       return e > 1 || f > 1;
     }

     /** The key a picker starts on: the first named key, else the calculator,
      * else '' (as the desktop's key dialog). */
     export function defaultKey(keys, calculator, calculatorValue) {
       if ((keys || []).length) return keys[0];
       return calculator ? calculatorValue : '';
     }

     /**
      * The LOAD_DATASET kwargs the loader's choices make. Empty when nothing
      * was chosen, so a plain file sends what it always sent.
      * @param {{keysShown?: boolean, energyKey?: string, forceKey?: string}} c
      */
     export function datasetLoadKwargs({ keysShown = false, energyKey = '', forceKey = '' } = {}) {
       const kw = {};
       if (keysShown) {
         kw.selected_energy_key = energyKey || null;
         kw.selected_force_key = forceKey || null;
       }
       return kw;
     }
     ```
   - `canLoad`: add `probing = false` to the destructured argument and make
     the dataset rule `if (mode === 'dataset') return !probing;` (before the
     final `return true;`). Update its JSDoc: "a dataset waits for its key
     check".
   - Constructor: add `this._probing = null;   // the path whose key check is still out`
     and `this._loadWhenReady = false;   // load once the key check is back`.
   - New method `_probeKeys(path)`:
     ```js
     /** Ask the server which energy/force keys `path` has; its answer is
      * matched to the path (a quicker click on another file wins). */
     _probeKeys(path) {
       this._probing = path;
       // Server route requires (path, dataset_type); ASE auto-detect reads the keys.
       this._ports.send(OUT.PROBE_DATASET_KEYS, {}, [path, 'ase (auto)']);
     }
     ```
   - `_selectFile(row, name)`:
     - In the prediction branch replace the `this._ports.send(OUT.PROBE_DATASET_KEYS, …)`
       line (and its comment) with `this._probeKeys(path);`.
     - Add a dataset branch (after the Task 2 type line):
       ```js
       if (this._mode === 'dataset') this._refreshDatasetKeys();
       ```
   - New method:
     ```js
     /** The Keys row follows the selected file and the Type: only an ASE
      * file is probed; any other type has no keys to choose. */
     _refreshDatasetKeys() {
       $('fb-keys').hidden = true;
       this._probing = null;
       if (this._selected && $('fb-type').value === 'ase (auto)') {
         this._probeKeys(joinPath(this._path, this._selected));
       }
       this._refreshLoadEnabled();
     }
     ```
   - `bindControls()`: add
     `$('fb-type').addEventListener('change', () => this._refreshDatasetKeys());`
   - Replace `onDatasetKeys` with:
     ```js
     /** Server replied to PROBE_DATASET_KEYS for `path`. */
     onDatasetKeys(path, kw) {
       if (path !== this._probing) return;   // a file no longer selected
       this._probing = null;
       if (this._mode === 'prediction') {
         // (keep the existing two _fillSelect calls and the Probe error status here, unchanged)
       } else if (this._mode === 'dataset') {
         this._showDatasetKeys(kw);
       }
       this._refreshLoadEnabled();
       this._loadIfReady();
     }
     ```
   - New methods:
     ```js
     /** Show the Keys row when the file offers a choice; a failed check
      * shows nothing here (the load itself says why it failed). */
     _showDatasetKeys(kw) {
       const choice = !kw.error && needsKeyChoice(kw);
       $('fb-keys').hidden = !choice;
       if (!choice) return;
       this._fillSelect($('fb-ds-energy-key'), keyOptions(kw.energy_keys, {
         calculator: kw.has_calculator_energy, calculatorValue: 'energy' }));
       this._fillSelect($('fb-ds-force-key'), keyOptions(kw.force_keys, {
         calculator: kw.has_calculator_forces, calculatorValue: 'forces' }));
       $('fb-ds-energy-key').value = defaultKey(kw.energy_keys, kw.has_calculator_energy, 'energy');
       $('fb-ds-force-key').value = defaultKey(kw.force_keys, kw.has_calculator_forces, 'forces');
     }

     /** Load now if it can, else as soon as the key check is back (a
      * double-click or Enter on a file row). */
     _loadWhenPossible() {
       if (!$('fb-load').disabled) this._load();
       else if (this._probing) this._loadWhenReady = true;
     }

     /** A load that waited for the key check goes ahead only when there is
      * nothing left to choose. */
     _loadIfReady() {
       if (!this._loadWhenReady) return;
       this._loadWhenReady = false;
       if (this._mode === 'dataset' && $('fb-keys').hidden && !$('fb-load').disabled) this._load();
     }
     ```
   - The dblclick handler and `_listKey`'s `act.load` branch: replace
     `if (!$('fb-load').disabled) this._load();` with `this._loadWhenPossible();`.
   - `_useRecent`: keep the load-at-once dataset branch (decision 4). Before
     its `this._load();` add:
     ```js
        // A Recent dataset loads with its default keys, no key check
        // (decision 4): drop any Keys row left from a file clicked before.
        $('fb-keys').hidden = true;
        this._probing = null;
        this._loadWhenReady = false;
     ```
     Its doc comment stays true ("A dataset loads at once"); add "with the
     server's default keys".
   - `open()` and `close()`: set `this._loadWhenReady = false;` and
     `this._probing = null;`; in `open()` also `$('fb-keys').hidden = true;`.
   - `_refreshLoadEnabled()`: pass `probing: Boolean(this._probing)` to `canLoad`
     — but only for the dataset mode (`probing: this._mode === 'dataset' && Boolean(this._probing)`),
     so the prediction rule is unchanged.
   - `_load()` dataset branch: replace `this._ports.send(OUT.LOAD_DATASET, {}, [path, typ]);` with
     ```js
     const kwargs = datasetLoadKwargs({
       keysShown: !$('fb-keys').hidden,
       energyKey: $('fb-ds-energy-key').value,
       forceKey: $('fb-ds-force-key').value,
     });
     this._ports.send(OUT.LOAD_DATASET, kwargs, [path, typ]);
     ```
     and update the comment above it (keys travel as kwargs when the file
     offered a choice).

**Test.**
- `test_web_pure_helpers.py` cases:
  `needsKeyChoice({energy_keys:['E'], force_keys:['F']})` → false;
  `needsKeyChoice({energy_keys:['E1','E2'], force_keys:['F']})` → true;
  `needsKeyChoice({energy_keys:['E'], force_keys:[], has_calculator_energy:true, has_calculator_forces:true})` → true;
  `defaultKey(['E1','E2'], true, 'energy')` → `'E1'`; `defaultKey([], true, 'energy')` → `'energy'`;
  `datasetLoadKwargs({})` → `{}`;
  `datasetLoadKwargs({keysShown:true, energyKey:'energy', forceKey:'MACE_forces'})` →
  `{selected_energy_key:'energy', selected_force_key:'MACE_forces'}`;
  `canLoad({selected:'a.xyz', mode:'dataset', probing:true})` → false.
- `test_web_loading.py`, helper:
  ```python
  def _two_key_file(tmp_path):
      """10 frames with REF_ and MACE_ energies and forces."""
      frames = ase.io.read(DATASET_PATH, index=":10")
      for a in frames:
          a.info["MACE_energy"] = a.info["REF_energy"] + 0.1
          a.arrays["MACE_forces"] = a.arrays["REF_forces"] + 0.01
      path = tmp_path / "two_keys.xyz"
      ase.io.write(path, frames, format="extxyz")
      return path
  ```
  `test_web_a_dataset_with_two_keys_asks_which_one`: connect, run
  `_RECORD_SENDS`, Load Dataset…, `_go_to(page, tmp_path)`, click
  `two_keys.xyz`; `#fb-keys` visible; `#fb-ds-energy-key` has value
  `REF_energy` and its option values include `MACE_energy`; select
  `MACE_energy` and `MACE_forces` (`select_option`); click `#fb-load`;
  `#dataset-list .obj-row` count 1 (timeout 30000);
  `await _sent(page, "LOAD_DATASET") == [{"selected_energy_key": "MACE_energy", "selected_force_key": "MACE_forces"}]`.
  Then Load Dataset… again, `_go_to(page, DATASET_PATH.parent)`, click
  `dataset.xyz`: `#fb-keys` stays hidden (`await expect(page.locator("#fb-keys")).to_be_hidden()`
  after `await expect(page.locator("#fb-load")).to_be_enabled()`); Load; the
  last `LOAD_DATASET` kwargs are `{}`.
  `test_web_a_recent_dataset_loads_at_once_with_default_keys` (decision 4):
  connect, Load Dataset…, `_go_to(page, tmp_path)`, click `two_keys.xyz`,
  wait for `#fb-keys` visible, click `#fb-load`; `#dataset-list .obj-row`
  count 1 (timeout 30000). Then `await page.evaluate(_RECORD_SENDS)`,
  Load Dataset… again, `_go_to(page, DATASET_PATH.parent)`, click
  `dataset.xyz` (a file row, so the Keys row is reset; this makes sure the
  Recent click below starts from a probed state), then click the
  `#fb-recent .fb-recent-row` whose text is `str(tmp_path / "two_keys.xyz")`:
  `#fb-modal` hidden at once (no wait for a key check);
  `await _sent(page, "LOAD_DATASET") == [{}]` (default keys, no picker; do
  not assert "already loaded": the server may count other keys as another
  dataset); and `await _sent(page, "PROBE_DATASET_KEYS")` lists only the
  probe for `dataset.xyz` — none for `two_keys.xyz` after the Recent click
  (`_sent` gives kwargs only; check the count is 1, and since the Recent
  path is never probed that one is `dataset.xyz`'s).
- Re-run in `test_web_runtime.py`:
  `-k "cannot_be_loaded or last_files_loaded or units_only or already_holds or loader"`
  (the Recent test `test_web_the_loader_lists_the_last_files_loaded_per_server`
  must pass unchanged: a Recent `dataset.xyz` still loads at once and the
  modal closes).

**Commit:** `feat: the web loader asks which energy and force keys a dataset uses`.

---

## Task 4: load more key pairs of the same file as predictions

**What the user sees:** in the Keys row (Task 3) a button **Also load a
prediction from this file** adds a line "Energy [▾] Force [▾] Name [____]
Remove". Each line becomes a prediction of the loaded dataset, named as
typed (blank = `Model`), shown in the rail's predictions list.

**Facts.**
- Desktop: `UI/KeySelectionDialog.py` "Load as Predictions (Optional)"
  section, `_addPredictionRow`, and `getSelection()` returns
  `'predictions': [(energy_key, force_key, model_name), ...]` with
  `model_name = name_edit.text() or "Model"`. Port the rows; drop Qt.
- Server: `_on_load_dataset` turns each `prediction_keys` list back into a
  tuple; `LoadingCoordinator._loadPredictionsFromKeys` makes one prediction
  (a "ghost model") per `(energy_key, force_key, model_name)`, named
  `model_name` (`registerGhostModel(..., name=name)`), and announces it with
  `REMOTE_MODEL_META`.
- The desktop's `_pairKeys` prefix rule (`get_prefix`: the part before the
  first `_`, `-` or `.`) is a good default name: `MACE_energy` → `MACE`.

**Change.**

1. `static/index.html`, inside `#fb-keys` after `.fb-keys-row`:
   ```html
        <div id="fb-pred-rows"></div>
        <button id="fb-add-pred" type="button">Also load a prediction from this file</button>
   ```
   CSS: `#fb-pred-rows .fb-pred-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 4px; }`
   and `#fb-add-pred { margin-top: 4px; }`.
2. `static/remote_browser.js`:
   - New exported pure helper:
     ```js
     /** A prediction's default name from its key: the part before the first
      * `_`, `-` or `.` (MACE_energy → MACE), else 'Model' (the desktop's
      * KeySelectionDialog prefix rule). */
     export function keyPrefix(key) {
       const m = /^([^_.\-]+)[_.\-]/.exec(key || '');
       return m ? m[1] : 'Model';
     }
     ```
   - `datasetLoadKwargs`: add `predictions = []` to its argument; after the
     keys, `if (keysShown && predictions.length) kw.prediction_keys = predictions.map(({ energyKey: e, forceKey: f, name }) => [e || '', f || '', name || 'Model']);`
     (an empty string means "calculator" to the server, as on the desktop).
     Update its JSDoc.
   - `_showDatasetKeys(kw)`: first line `$('fb-pred-rows').replaceChildren();`
     and keep `this._probe = kw;` so new rows can be filled.
   - `bindControls()`: `$('fb-add-pred').addEventListener('click', () => this._addPredictionRow());`
   - New methods:
     ```js
     /** One more key pair to load as a prediction (as the desktop's
      * "Add Prediction"). Starts on the first keys the reference is not using. */
     _addPredictionRow() {
       const kw = this._probe || {};
       const row = document.createElement('div');
       row.className = 'fb-pred-row';
       const pick = (keys, calc, calcValue, taken, cls, label) => {
         const sel = document.createElement('select');
         sel.className = cls;
         sel.setAttribute('aria-label', label);
         this._fillSelect(sel, keyOptions(keys, { calculator: calc, calculatorValue: calcValue }));
         sel.value = (keys || []).find((k) => k !== taken) ?? defaultKey(keys, calc, calcValue);
         return sel;
       };
       const energy = pick(kw.energy_keys, kw.has_calculator_energy, 'energy',
         $('fb-ds-energy-key').value, 'fb-pred-energy', 'Prediction energy key');
       const force = pick(kw.force_keys, kw.has_calculator_forces, 'forces',
         $('fb-ds-force-key').value, 'fb-pred-force', 'Prediction force key');
       const name = document.createElement('input');
       name.className = 'fb-pred-name';
       name.placeholder = 'Model';
       name.setAttribute('aria-label', 'Prediction name');
       name.value = keyPrefix(energy.value);
       const remove = document.createElement('button');
       remove.type = 'button';
       remove.textContent = 'Remove';
       remove.addEventListener('click', () => row.remove());
       row.append('Energy', energy, 'Force', force, 'Name', name, remove);
       $('fb-pred-rows').appendChild(row);
     }

     /** The prediction lines, as datasetLoadKwargs reads them. */
     _predictionRows() {
       return [...document.querySelectorAll('#fb-pred-rows .fb-pred-row')].map((row) => ({
         energyKey: row.querySelector('.fb-pred-energy').value,
         forceKey: row.querySelector('.fb-pred-force').value,
         name: row.querySelector('.fb-pred-name').value.trim(),
       }));
     }
     ```
   - `_load()`: pass `predictions: this._predictionRows()` to `datasetLoadKwargs`.
   - `open()`: `$('fb-pred-rows').replaceChildren();`.

**Test.**
- Pure cases: `keyPrefix('MACE_energy')` → `'MACE'`; `keyPrefix('energy')` → `'Model'`;
  `datasetLoadKwargs({keysShown:true, energyKey:'REF_energy', forceKey:'REF_forces', predictions:[{energyKey:'MACE_energy', forceKey:'MACE_forces', name:''}]}).prediction_keys`
  → `[['MACE_energy','MACE_forces','Model']]`;
  `datasetLoadKwargs({keysShown:false, predictions:[{energyKey:'E', forceKey:'F', name:'X'}]})` → `{}`.
- `test_web_loading.py` `test_web_more_keys_of_a_file_load_as_predictions`:
  `_two_key_file(tmp_path)`; connect, `_RECORD_SENDS`, Load Dataset…, go to
  `tmp_path`, click `two_keys.xyz`; click `#fb-add-pred`; the new row's
  `.fb-pred-energy` value is `MACE_energy`, `.fb-pred-force` is
  `MACE_forces`, `.fb-pred-name` is `MACE`; Load; `#model-list .obj-row`
  with text `MACE` appears (timeout 30000); the sent `LOAD_DATASET` kwargs
  equal `{"selected_energy_key": "REF_energy", "selected_force_key": "REF_forces", "prediction_keys": [["MACE_energy", "MACE_forces", "MACE"]]}`.
  Also: add a second row and click its Remove: `#fb-pred-rows .fb-pred-row`
  count goes back to 1 (do this before Load).
- If the load fails on the server (the dataset reads frames in parts,
  `slice_num = 0`, and `_loadPredictionsFromKeys` uses that frame list),
  stop and report with the server log; do not change the loader.

**Commit:** `feat: the web loader loads extra key pairs of a file as predictions`.

---

## Task 5: every Nth frame, and a note for files of 3 GB or more

**What the user sees:** the dataset loader has a field **Every Nth frame**
(starts at 1 = all frames). For a file of 3 GB or more a note appears above
the buttons: "This file is 3.4 GB: 1,234,567 frames. Every 10th frame:
~123,457." (it says "Counting frames…" until the server answers). A
double-click or Enter on such a file does not load it by itself; the user
sets N and presses Load. A Recent file loads at once whatever its size
(decision 4), with the "Every Nth frame" value the field shows; Recent
entries carry no size, so no note is shown for them.

**Facts.**
- Desktop, local: `UI/mainMenu.py` `onDatasetLoad`: a file of 3 GB or more
  (`file_size >= 3`, in GB of 10^9 bytes) opens `BigDatasetWarningDialog`
  (`UI/Templates/dialogs.py`) with three buttons: "Load as a whole"
  (`slice_num 0`), "Load as a whole (without caching)" (`-1`) and "Pick
  samples" (`N`). Desktop, remote: `RemoteStrideDialog` ("Load every Nth
  frame (1 = all)", an estimate `~ceil(n_total / N)` frames) after
  `PROBE_DATASET_LENGTH`, and `stride_to_slice_num` (`UI/menuLogic.py`):
  `return 0 if stride == 1 else stride`. Port the remote dialog's field
  and estimate; drop the "without caching" choice (decision 3).
- Server: `control.PROBE_DATASET_LENGTH` route `_on_probe_dataset_length(self, path)`
  replies `DATASET_LENGTH_RESPONSE` with `args = (path,)`, kwargs
  `{n, error}`. It counts frames with ASE, so it works for ASE files only.
  `DATASET_LENGTH_RESPONSE` is in `control.REPLY_EVENTS`, so the IN-name
  test accepts it.
- `slice_num` reaches the loaders: `SmartASELoader.__call__`
  (`ffast/plugins/loaders/ase.py`) `ase.io.read(path, index=slice(0, None, slice_num))`
  for `slice_num > 0`; `sGDMLDatasetLoader` keeps every `slice_num`-th entry.
- `DIR_LISTING` entries carry `size` in bytes; `formatSize(bytes)` exists.

**Change.**

1. `static/events.js`: `OUT.PROBE_DATASET_LENGTH: 'PROBE_DATASET_LENGTH'`
   (after `PROBE_DATASET_KEYS`), `IN.DATASET_LENGTH_RESPONSE: 'DATASET_LENGTH_RESPONSE'`
   (after `DATASET_KEYS_RESPONSE`).
2. `static/index.html`:
   - In `#fb-dataset-fields`, after the `#fb-type` select:
     ```html
          <label for="fb-stride">Every Nth frame</label>
          <input id="fb-stride" type="number" min="1" step="1" value="1">
     ```
     CSS: `#fb-stride { width: 70px; }`.
   - Between `#fb-keys` and `#fb-footer`: `<div id="fb-large" hidden></div>`;
     CSS: `#fb-large { padding: 6px 12px; font-size: var(--fs-s); color: var(--muted); border-top: 1px solid var(--line); }`
     and `#fb-large[hidden] { display: none; }`.
3. `static/remote_browser.js`:
   - New exports:
     ```js
     /** From this size on (bytes) a file gets the large-file note, as the
      * desktop's BigDatasetWarningDialog (UI/mainMenu.py, 3 GB). */
     export const LARGE_FILE_BYTES = 3_000_000_000;

     /** The loader's stride number for "every Nth frame": 1 (or less, or not
      * a number) means all frames, which the loader spells 0 (the desktop's
      * stride_to_slice_num). */
     export function strideToSliceNum(stride) {
       const n = Math.floor(Number(stride));
       return Number.isFinite(n) && n > 1 ? n : 0;
     }

     /** The large-file note: size, frame count when known, and what every
      * Nth frame keeps (the desktop's RemoteStrideDialog estimate). */
     export function largeFileNote({ size, n = null, stride = 1, error = null }) {
       const head = `This file is ${formatSize(size)}`;
       if (error) return `${head}. The frames could not be counted (${error}).`;
       if (n === null || n === undefined) return `${head}. Counting frames…`;
       const step = strideToSliceNum(stride);
       const all = `${head}: ${n.toLocaleString('en-US')} frames.`;
       if (!step) return `${all} Set "Every Nth frame" to load fewer.`;
       return `${all} Every ${step}th frame: ~${Math.max(1, Math.ceil(n / step)).toLocaleString('en-US')}.`;
     }
     ```
     (`formatSize` is defined above in the same file.)
   - `datasetLoadKwargs`: add `stride = 1`; at the end
     `const sliceNum = strideToSliceNum(stride); if (sliceNum) kw.slice_num = sliceNum;`
     (it applies whether or not the Keys row shows).
   - Constructor: `this._largeBytes = LARGE_FILE_BYTES;   // tests lower it`
     and `this._large = null;   // {path, size, n, error} of a large selected file`.
   - `_render`: on a file row add `row.dataset.size = String(e.size ?? 0);`.
   - `_selectFile(row, name)` dataset branch: after `_refreshDatasetKeys()`,
     call `this._refreshLarge(Number(row.dataset.size || 0));`.
   - New methods:
     ```js
     /** A file of 3 GB or more shows its size and frame count (counted by
      * the server, ASE files only) so "Every Nth frame" can be chosen. */
     _refreshLarge(size) {
       const big = this._mode === 'dataset' && size >= this._largeBytes;
       const path = this._selected ? joinPath(this._path, this._selected) : null;
       this._large = big ? { path, size, n: null, error: null } : null;
       $('fb-large').hidden = !big;
       if (!big) return;
       if ($('fb-type').value === 'ase (auto)') this._ports.send(OUT.PROBE_DATASET_LENGTH, {}, [path]);
       else this._large.error = 'only ASE files are counted';
       this._drawLarge();
     }

     _drawLarge() {
       if (!this._large) return;
       $('fb-large').textContent = largeFileNote({ ...this._large, stride: $('fb-stride').value });
     }

     /** Server replied to PROBE_DATASET_LENGTH. */
     onDatasetLength(path, kw) {
       if (!this._large || this._large.path !== path) return;
       this._large.n = kw.n ?? null;
       this._large.error = kw.error || null;
       this._drawLarge();
     }
     ```
   - `bindControls()`: `$('fb-stride').addEventListener('input', () => this._drawLarge());`
   - `_loadIfReady()`: add `&& $('fb-large').hidden` to the load condition
     (a large file clicked in the list waits for the user; a Recent file
     does not go through `_loadIfReady`, decision 4).
   - `open()`: `$('fb-stride').value = '1'; $('fb-large').hidden = true; this._large = null;`
   - `_load()`: pass `stride: $('fb-stride').value` to `datasetLoadKwargs`.
4. `static/app.js`: next to the `DATASET_KEYS_RESPONSE` handler add
   `conn.on(IN.DATASET_LENGTH_RESPONSE, (kw, args) => this._browser.onDatasetLength(args[0], kw));`

**Test.**
- Pure cases: `strideToSliceNum(1)` → 0; `strideToSliceNum('10')` → 10;
  `strideToSliceNum('x')` → 0; `datasetLoadKwargs({stride: 10})` →
  `{slice_num: 10}`; `largeFileNote({size: 3.5e9})` contains
  `Counting frames`; `largeFileNote({size: 3.5e9, n: 100, stride: 10})`
  ends with `Every 10th frame: ~10.`.
- `test_web_loading.py` `test_web_every_nth_frame_and_the_large_file_note`:
  connect, `_RECORD_SENDS`, then
  `await page.evaluate("() => { window.ffastApp._browser._largeBytes = 1; }")`;
  Load Dataset…, go to `DATASET_PATH.parent`, click `dataset.xyz`;
  `#fb-large` visible and contains `100 frames` (timeout 15000); fill
  `#fb-stride` with `10` → `#fb-large` contains `~10`; Load; the last
  `LOAD_DATASET` kwargs are `{"slice_num": 10}`;
  `await page.wait_for_function("() => [...window.ffastApp._datasets.values()].some((m) => m.n === 10)", timeout=30000)`.
- Run the Task 3 browser tests again (Recent, cannot-be-loaded).

**Commit:** `feat: the web loader loads every Nth frame and notes very large files`.

---

## Task 6: .npz predictions load in the web

**What the user sees:** choosing a `.npz` prediction file no longer leaves
**Load** greyed out. The Energy and Force lists say "from the .npz file"
and Load attaches it to the chosen dataset, as on the desktop.

**Facts.**
- Today `_selectFile` probes every prediction file as ASE:
  `this._probeKeys(path)` (after Task 3) with `'ase (auto)'`. ASE cannot
  read `.npz`, so the reply has `error` and no keys, `#fb-force-key` stays
  empty, and `canLoad` refuses (`if (mode === 'prediction') return Boolean(forceKey && targetDataset);`).
- The server needs no keys for `.npz`:
  `ffast/core/loading_coordinator.py` `loadPrepredictedDataset` starts with
  `if "npz" in path:` and reads `d["E"], d["F"]`; keys are ignored.
  The desktop also skips its key dialog for `.npz` (`UI/mainMenu.py`
  `onPrepredictedModelLoad`: `if path and "npz" not in path:`).
- `_load()` prediction branch sends
  `{ selected_energy_key: eKey, selected_force_key: fKey }` with
  `eKey = $('fb-energy-key').value || null`, and returns early
  `if (!dsFp || !fKey) return;`.

**Change** (`static/remote_browser.js` only):

1. `canLoad`: add `isNpz = false` to the argument; the prediction rule
   becomes `return Boolean(targetDataset && (forceKey || isNpz));`. JSDoc:
   "an .npz prediction carries its own E and F, so it needs no key".
2. New exported pure helper `isNpzFile(name)`:
   `return /\.npz$/i.test(name || '');` with a one-line JSDoc.
3. `_selectFile` prediction branch: if `isNpzFile(name)`, do **not** probe;
   instead fill both selects with one option
   `[{ value: '', label: 'from the .npz file' }]` and set `this._probing = null`.
   Otherwise keep today's code (the "…probing…" options and `_probeKeys`).
4. `_refreshLoadEnabled()`: pass `isNpz: this._mode === 'prediction' && isNpzFile(this._selected)`.
5. `_load()` prediction branch: change the early return to
   `if (!dsFp || (!fKey && !isNpzFile(this._selected))) return;`.

**Test.**
- Pure cases: `canLoad({selected:'p.npz', mode:'prediction', forceKey:'', targetDataset:'fp', isNpz:true})`
  → true; `isNpzFile('a.NPZ')` → true; `isNpzFile('a.xyz')` → false.
- `test_web_loading.py` `test_web_an_npz_prediction_loads`:
  `dataset_fp = await _preload_uniform_dataset(ws_port, tmp_path)` (5
  frames of the same 49 atoms); write
  `np.savez(tmp_path / "pred.npz", E=np.zeros(5), F=np.zeros((5, 49, 3)))`;
  `await _open_loupe(page, ws_port, web_port, dataset_fp)`; `_RECORD_SENDS`;
  Load Prediction…; go to `tmp_path`; click `pred.npz`; `#fb-force-key`
  has text `from the .npz file`; `#fb-load` enabled; click it;
  `#model-list .obj-row` with text `pred.npz` appears (timeout 30000);
  `await _sent(page, "PROBE_DATASET_KEYS") == []`.
  (Check the atom count first: `len(ase.io.read(DATASET_PATH, index=0))`
  must be 49; if not, use that number.)

**Commit:** `fix: an .npz prediction file can be loaded from the web`.

---

## Task 7: the server tells the window when a model fails to load

**What the user sees:** nothing yet in the web (no Load Model yet); the
desktop is unchanged. This makes Task 8's "Loading model…" end with the
reason when a model cannot load.

**Facts.**
- `ffast/session/server_session.py`:
  ```python
      async def _on_load_model(self, path, model_type, **kwargs) -> None:
          self.env.taskLoadModel(path, model_type)
  ```
  The dataset and prediction handlers pass
  `on_error=self._load_failed("dataset", path)`; `_load_failed(kind, path)`
  sends `LOAD_FAILED {kind, path, error}` to the asking window.
- `ffast/core/environment.py` `taskLoadModel(self, path, modelType)` delegates
  to `self.loading.taskLoadModel(path, modelType)`.
- `ffast/core/loading_coordinator.py`:
  ```python
      def taskLoadModel(self, path, modelType):
          """Queue model loading so disk I/O and setup do not block the main loop."""
          self._queueLoad(self.loadModel, "Loading model", args=(path, modelType))
  ```
  `_queueLoad(fn, name, args, kwargs, on_error)` already passes `on_error`
  into the load function and reports raised errors. `loadModel(self, path, modelType, taskID=None)`
  returns `None` after `_resolveLoad` fails (missing file, unknown type) and
  after its two `except` branches, which only `logger.error(...)`.
- `loadDataset` logs with `extra={"popup": on_error is None}` so the
  window sees one pop-up, not two (ADR 0066). Copy that.
- `requestModelLoad` (desktop, no server) calls `self.taskLoadModel(path, modelType)`;
  the test `test_requestModelLoad_no_session_falls_back_to_local_task`
  asserts the queued function is `coord.loadModel`, which stays true when
  `on_error` is `None` (no wrapper).

**Change.**

1. `ffast/core/loading_coordinator.py`:
   - `taskLoadModel(self, path, modelType, on_error=None)`: pass
     `on_error=on_error` to `_queueLoad`.
   - `loadModel(self, path, modelType, taskID=None, on_error=None)`:
     - After `loaderClass = self._resolveLoad("model", path, modelType)`:
       `if loaderClass is None:` → `if on_error: on_error(f"no file at {path}" if not os.path.exists(path) else f"this server has no model type {modelType!r}")`, then `return None`.
     - In the `except (ImportError, ModuleNotFoundError) as exc:` and the
       `except Exception as exc:` branches, add
       `extra={"popup": on_error is None}` to `logger.error(...)` and then
       `if on_error: on_error(exc)` before `return None`.
     - `if model is None:` branch: same, with
       `on_error("the model loader returned nothing")`.
2. `ffast/core/environment.py` `taskLoadModel(self, path, modelType, on_error=None)`:
   pass `on_error=on_error` on.
3. `ffast/session/server_session.py` `_on_load_model`:
   `self.env.taskLoadModel(path, model_type, on_error=self._load_failed("model", path))`.
4. `ffast/protocol/control.py`: the `LOAD_FAILED` comment says
   "A LOAD_DATASET / LOAD_PREDICTION that failed"; make it
   "A LOAD_DATASET / LOAD_PREDICTION / LOAD_MODEL that failed".

**Test.**
- `tests/ffast/session/test_server_session.py`: in `_FailingLoadEnv` add
  ```python
      def taskLoadModel(self, path, model_type, on_error=None):
          on_error(ImportError("no module named mace"))
  ```
  and a third parametrize row
  `("LOAD_MODEL", ["/x/m.model", "MACE"], "model")`; extend the `error in (...)`
  tuple with `"no module named mace"`.
- `tests/ffast/test_loading_coordinator.py`:
  `test_a_model_that_fails_to_load_reports_why(tmp_path)`:
  `env = _fake_env(); env.modelTypes = {"Broken": Broken}; env.datasetTypes = {}`
  where `class Broken:` has `def __init__(self, env, path): raise ValueError("bad file")`;
  `(tmp_path / "m.bin").write_bytes(b"x")`; `errors = []`;
  `LoadingCoordinator(env).loadModel(str(tmp_path / "m.bin"), "Broken", on_error=errors.append)`;
  assert `[str(e) for e in errors] == ["bad file"]`.
  And `test_a_missing_model_file_reports_why`: same env, path that does not
  exist → one error whose text contains `no file at`.
- Run both files.

**Commit:** `fix: a model that fails to load tells the window that asked`.

---

## Task 8: Load Model… in the web

**What the user sees:** File ▸ **Load Model…** opens the loader titled
"Load Model" with a **Model type** list (the server's model loaders; it
picks by file extension: `.model` → MACE, `.pth` → Nequip, `.npz` →
sGDML). Load sends the model to the server, which runs it there (ADR
0030). When it arrives it shows in the rail's predictions list,
**not selected** (decision 5): the user clicks its row to select it, as
for any prediction. Status "Model "<name>" loaded". A
failure ends with "Could not load <file>: <why>". Recent model files are
listed like datasets.

**Facts.**
- Desktop: `UI/mainMenu.py` `onModelLoad` → `customFileDialog` with
  `env.modelTypes` names and extensions → `env.requestModelLoad(path, typ)`
  → `LOAD_MODEL [path, model_type]`. Port: the type list and the send.
- Server: `control.LOAD_MODEL` route `_Route(self._on_load_model, ["path", "model_type"], LoadModelRequest)`;
  `LoadModelRequest` has only `path`, `model_type` (no other kwargs).
  On success `ModelRegistry.add` fires `MODEL_LOADED`, and `server.py`
  `_on_model_loaded_meta` broadcasts `REMOTE_MODEL_META (model_fp,) {name, dataset_fingerprints}`.
  A fresh model has `dataset_fingerprints == []` (no results cached yet).
- `static/app.js` `_onModelMeta(fp, meta)`:
  ```js
      const loaded = !this._models.has(fp) && this._browser.settled('prediction');
      ...
      if (!wantsSpecificModel && this._currentDatasetFp &&
          (meta?.dataset_fingerprints || []).length
          && predictionApplies(meta, this._currentDatasetFp, this._datasets)) {
        this._currentModelFp = fp;
        ...
      } else if (loaded) {
        this._setStatus(`Prediction "${meta?.name || fp.slice(0,8)}" loaded`, 'connected');
      }
  ```
  `_selectModel(fp)` sets `_currentModelFp`, re-renders, syncs analysis,
  re-opens the view. It is what a click on a rail row calls
  (`onSelectModel: (fp) => this._selectModel(fp)`).
- A fresh model has no results, so the auto-select rule above skips it
  (`(meta?.dataset_fingerprints || []).length` is 0) and the `else if (loaded)`
  branch runs: it stays unselected, which is what decision 5 wants. No
  select code is added. Its rail row is still clickable: `static/rail.js`
  `_renderModels` disables a row only when `applies(meta)` is false, and
  `predictionApplies` (`static/frame_links.js`) returns `true` when
  `dataset_fingerprints` is empty.
- `RemoteBrowser.settled(kind, ok, fp)` ends a pending load of `kind` and
  remembers its path in Recent under that kind; `onTaskDone()` ends a pending
  load the server answered with nothing new ("is already loaded").
  The `LOAD_FAILED` handler in `app.js` calls `this._browser.settled(kw.kind, false)`.
- The File menu is `_buildActions()` in `app.js`; the test list
  `FILE_MENU` in `tests/ffast/renderers/web/test_web_runtime.py` must match
  it in order.
- `open(mode)` today:
  ```js
      const isPred = mode === 'prediction';
      $('fb-title').textContent = isPred ? 'Load Prediction' : 'Load Remote Dataset';
      $('fb-dataset-fields').style.display = isPred ? 'none' : '';
      $('fb-prediction-fields').style.display = isPred ? 'inline-flex' : 'none';
  ```

**Change.**

1. `static/events.js`: `OUT.LOAD_MODEL: 'LOAD_MODEL'` after `LOAD_PREDICTION`.
2. `static/index.html`, in `#fb-footer` after `#fb-prediction-fields`:
   ```html
        <span id="fb-model-fields" style="display:none; align-items:center; gap:6px;">
          <label for="fb-model-type">Model type</label>
          <select id="fb-model-type" style="width:130px"></select>
        </span>
   ```
   and add `#fb-model-fields` to the `#fb-prediction-fields { flex: 1 1 100%; flex-wrap: wrap; }` rule.
3. `static/remote_browser.js`:
   - JSDoc of the class/`open`: mode is `'dataset'|'prediction'|'model'`.
   - `canLoad`: add `modelType = ''`; `if (mode === 'model') return Boolean(modelType);`.
   - `setLoaderTypes(kw)`: also fill `#fb-model-type` from
     `this._types.models` (value and label = name).
   - `open(mode)`: replace the four lines quoted above with
     ```js
     const titles = { dataset: 'Load Remote Dataset', prediction: 'Load Prediction', model: 'Load Model' };
     $('fb-title').textContent = titles[mode];
     $('fb-dataset-fields').style.display = mode === 'dataset' ? '' : 'none';
     $('fb-prediction-fields').style.display = mode === 'prediction' ? 'inline-flex' : 'none';
     $('fb-model-fields').style.display = mode === 'model' ? 'inline-flex' : 'none';
     const isPred = mode === 'prediction';
     ```
     (keep `if (isPred) this._populatePredictionTargets();`).
   - `_selectFile`: `if (this._mode === 'model') $('fb-model-type').value = typeForFile(name, this._types.models, '');`
     (with `''` as fallback this picks the first type when nothing matches).
   - `_refreshLoadEnabled()`: pass `modelType: $('fb-model-type').value`.
   - `_load()`: add a model branch before the dataset `else`:
     ```js
     } else if (this._mode === 'model') {
       const typ = $('fb-model-type').value;
       // LOAD_MODEL reads args=[path, model_type]; the model runs on the server (ADR 0030).
       this._ports.send(OUT.LOAD_MODEL, {}, [path, typ]);
       this._ports.setStatus(`Loading model ${this._selected}…`, 'connected');
       this._pending = { kind: 'model', name: this._selected, path };
     }
     ```
   - New method `pendingKind()` returning `this._pending?.kind ?? null`
     (JSDoc: which load this window is waiting for).
   - `settled`'s JSDoc: kinds are `'dataset'`, `'prediction'` or `'model'`.
4. `static/app.js`:
   - `_buildActions()`: after the `load-prediction` entry add
     ```js
     { id: 'load-model', label: 'Load Model…', menu: 'file',
       run: () => this._browser.open('model'), unavailable: needsControl },
     ```
   - `_onModelMeta`: replace the `const loaded = …` line with
     ```js
     // A prediction or a model this window asked for ends its "Loading…".
     const asked = this._browser.pendingKind();
     const loaded = !this._models.has(fp)
       && (asked === 'prediction' || asked === 'model') && this._browser.settled(asked);
     ```
     Do **not** add any code that selects the new model (decision 5: the
     user selects it in the rail). In the final `else if (loaded)` branch
     only change the text to
     `` `${asked === 'model' ? 'Model' : 'Prediction'} "${meta?.name || fp.slice(0,8)}" loaded` ``.
     Update the comment above `const wantsSpecificModel` to add: "A model
     this window just loaded has no results yet, so it is not selected; the
     user picks it in the rail (web parity plan 2026-10-29, decision 5)."
5. `tests/ffast/renderers/web/test_web_runtime.py`: in `FILE_MENU` insert
   `"Load Model…"` after `"Load Prediction…"`.

**Test.**
- Pure cases: `canLoad({selected:'m.model', mode:'model', modelType:'MACE'})` → true;
  `canLoad({selected:'m.model', mode:'model', modelType:''})` → false.
- `test_web_loading.py` `test_web_a_model_that_cannot_load_says_why`:
  `await _wait_for_server_ready(ws_port)`; connect; `_RECORD_SENDS`;
  `_file_menu(page, "Load Model…")`; `#fb-title` has text `Load Model`;
  the `#fb-model-type option` values include `sGDML` (always registered: its
  ML import is lazy); go to `REPO_ROOT`; click `README.md`; select
  `sGDML` in `#fb-model-type`; Load; `#status` contains
  `Could not load README.md` (timeout 30000) and has class `error`;
  `await _sent(page, "LOAD_MODEL") == [{}]` (the path and type travel as args);
  after `wait_for_timeout(1500)`, `#toasts .toast` count is 1 (no second
  pop-up from the server log, Task 7).
- Run `test_web_runtime.py -k "file_menu"` (the FILE_MENU list) and the
  prediction tests `-k "prediction"` (the `_onModelMeta` change).
- A real model load is **not** tested (rule 8).

**Commit:** `feat: the web loads a machine-learned model on the server`.

---

## Task 9: Load Zero Model in the web

**What the user sees:** File ▸ **Load Zero Model** adds "Zero Model" (all
energies and forces zero, a baseline) to the predictions list, **not
selected** (decision 5): the user clicks its row to use it. (The desktop's
Ctrl+0 does the same load; the web shortcut comes from plan 3.) A second
click says
`"Zero Model" is already loaded`. It never loads by itself.

**Facts.**
- Desktop: `UI/mainMenu.py` `onZeroModelLoad` → `env.taskLoadZeroModel()`
  (in-process on the desktop). `ffast/core/environment.py`:
  ```python
      def taskLoadZeroModel(self):
          """Queue loading of the built-in zero baseline model."""
          self.newTask(self.loadZeroModel, args=(), visual=True, name="Loading model", threaded=True)
  ```
  `loadZeroModel` returns early when `self.models.exists("zeroModel")`;
  otherwise `self.models.add(model)` fires `MODEL_LOADED`, which the server
  turns into `REMOTE_MODEL_META ("zeroModel",) {name: "Zero Model", ...}`.
  The server's environment is a `HeadlessEnvironment(Environment, ...)`, so
  it has this method.
- There is **no** server route for it (searched `LOAD_ZERO`, `taskLoadZeroModel`
  in `ffast/session`: none). `ZeroModelLoader` is not in `env.modelTypes`
  (it is never passed to `initialiseModelType`), so `LOAD_MODEL` cannot
  load it. A new route is needed.
- Routes: `ffast/protocol/control.py` constant + `CLIENT_TO_SERVER` +
  `MUTATING_CLIENT_EVENTS` (a read-only window must not load it); the
  handler table in `ServerSession.__init__` (search
  `control.LOAD_EXAMPLE:               _Route(self._on_load_example, [], EmptyRequest),`);
  `tests/ffast/test_control_events.py`
  `test_server_session_handlers_match_client_to_server_constants` checks
  the table equals `CLIENT_TO_SERVER`.
- Memory rule: the zero model loads only on an explicit user action.

**Change.**

1. `ffast/protocol/control.py`: after `LOAD_EXAMPLE` and its comment:
   ```python
   # Load the zero baseline model (all energies and forces zero). Only ever sent
   # by an explicit user action (File ▸ Load Zero Model), never automatically.
   LOAD_ZERO_MODEL = "LOAD_ZERO_MODEL"
   ```
   Add `LOAD_ZERO_MODEL` to `CLIENT_TO_SERVER` and to `MUTATING_CLIENT_EVENTS`.
2. `ffast/session/server_session.py`:
   - Table row after `LOAD_EXAMPLE`:
     `control.LOAD_ZERO_MODEL:            _Route(self._on_load_zero_model, [], EmptyRequest),`
     (align the columns like the rows around it).
   - Handler after `_on_load_example`:
     ```python
         async def _on_load_zero_model(self, **kwargs) -> None:
             """Load the zero baseline model; a second request loads nothing
             (the window then reads "already loaded" from the task's end)."""
             self.env.taskLoadZeroModel()
     ```
3. `static/events.js`: `OUT.LOAD_ZERO_MODEL: 'LOAD_ZERO_MODEL'` after `LOAD_EXAMPLE`.
4. `static/remote_browser.js`: new method
   ```js
   /** File ▸ Load Zero Model: no file to pick. Its "Loading…" ends like a
    * model's (REMOTE_MODEL_META, or "already loaded" when the task ends). */
   loadZeroModel() {
     this._ports.send(OUT.LOAD_ZERO_MODEL, {}, []);
     this._ports.setStatus('Loading Zero Model…', 'connected');
     this._pending = { kind: 'model', name: 'Zero Model', path: null };
   }
   ```
   In `settled()`, guard the Recent list: `if (ok && this._pending.path) this._remember(...)`;
   in `onTaskDone()`, the same guard on its `_remember` call.
5. `static/app.js` `_buildActions()`: after `load-model`:
   ```js
   { id: 'load-zero-model', label: 'Load Zero Model', menu: 'file',
     run: () => this._browser.loadZeroModel(), unavailable: needsControl },
   ```
6. `tests/ffast/renderers/web/test_web_runtime.py` `FILE_MENU`: insert
   `"Load Zero Model"` after `"Load Model…"`.

**Test.**
- `tests/ffast/session/test_server_session.py`:
  `test_load_zero_model_asks_the_env_once_per_request`: a `_FakeEnv`
  subclass with `zero_calls = 0` and `def taskLoadZeroModel(self): self.zero_calls += 1`;
  dispatch `"LOAD_ZERO_MODEL", [], {}` → `zero_calls == 1`.
- `tests/ffast/test_multi_client_role_model.py` (or wherever the
  READ_ONLY gate is tested; search `MUTATING_CLIENT_EVENTS` in `tests/`):
  if there is a list of mutating events checked one by one, add
  `LOAD_ZERO_MODEL`. If not, add nothing.
- `test_web_loading.py` `test_web_the_zero_model_loads_only_when_asked`:
  `dataset_fp = await _preload_dataset(ws_port)`; `_open_loupe(...)`;
  `#model-list .obj-row` count 0 (it did not load by itself);
  `_file_menu(page, "Load Zero Model")`; `row = page.locator("#model-list .obj-row", has_text="Zero Model")`
  appears (timeout 30000); it is **not** selected and not disabled
  (`static/rail.js` `_renderModels` marks rows with the classes `selected`
  and `disabled`): `await expect(row).not_to_have_class(re.compile(r"\bselected\b"))`,
  `await expect(row).not_to_have_class(re.compile(r"\bdisabled\b"))`, and
  `await page.evaluate("() => window.ffastApp._currentModelFp") is None`;
  `#status` contains `Model "Zero Model" loaded`. Click `row` → it gets
  the `selected` class (the user selects it). Click the menu item again →
  `#status` has text `"Zero Model" is already loaded` (timeout 15000).
- Run `tests/ffast/test_control_events.py`, `test_event_constants.py`,
  `test_web_runtime.py -k file_menu`.

**Commit:** `feat: the web loads the zero baseline model when asked`.

---

## Not in this plan (found while checking)

- `loadPrepredictedDataset` treats any path that **contains** `npz` as an
  `.npz` file (`if "npz" in path:`), so `npz_runs/pred.xyz` is read wrongly.
  The desktop has the same check. Worth a one-line `fix:` later
  (`path.lower().endswith(".npz")`), with its own test.
- Whether the 3D force arrows of a freshly loaded real or zero model show
  before any plot has computed its forces: the server scene reads cached
  forces only (`_PredictionView` in `server_session.py`). Check by hand
  after Task 9; if the arrows are missing until an analysis tab is opened,
  that is a separate task.

## When done

Report: the nine commit hashes, the tests you added, anything in this plan
that did not match the code and what you did about it, and any test that
was red (with its output). Do not push.
