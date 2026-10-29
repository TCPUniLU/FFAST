# Web workspace parity: start folder, file browser, export formats, rename, info, tasks button, Alt+Shift shortcuts: implementation plan (8 tasks)

Web parity plan 2026-10-29. The desktop (Qt) client has these features and
the browser (web) client does not. This plan says how to build them in the
web client. There is no ADR; every design choice was decided by the user
(list below). Do the tasks **in the order given**, one commit per task. Each
task leaves the app working.

Words used below:
- **window**: one browser page connected to the server.
- **rail**: the left column of the web page listing datasets and
  predictions (`#objectbar`, drawn by `static/rail.js`).
- **⋯ menu**: the menu a rail row opens from its "more" button.
- **announcement**: a message the server sends without being asked
  (for example `REMOTE_DATASET_META`).
- **file browser**: the dialog that walks the server's folders
  (`#fb-modal`, `static/remote_browser.js`).

## Decisions (confirmed by the user 2026-10-29)

1. **"Load Config…" is not ported.** `--config` stays a start-up option
   (the desktop item calls a `loadConfig` that no longer exists).
2. **Without `--workdir`, the file browser starts in the folder the server
   was started from** (`os.getcwd()` of the server process), as the desktop
   does (`UIHandler.workdir = workdir or os.getcwd()`). Task 1.
3. **Every file shortcut is Alt+Shift+letter** (Option+Shift on a Mac), so
   no key fights the browser: Alt+Shift+S save session, Alt+Shift+L load
   session, Alt+Shift+D load dataset, Alt+Shift+P load prediction,
   Alt+Shift+M load model, Alt+Shift+0 load zero model, Alt+Shift+N new 3D
   view (pop-out), Alt+Shift+C connect to cluster. Keys are matched by
   `event.code`; they are ignored while focus is in a text field. The File
   menu shows them (⌥⇧S on a Mac, Alt+Shift+S elsewhere). Task 8.
4. **The Info dialog is computed in the browser** from the metadata the
   window already has. Task 5.
5. **The tasks list is hidden until the user opens it**: a "Tasks (N)"
   button in the top bar, next to the status label, shown only while at
   least one task runs; a click opens a drop-down list with name, message,
   progress bar and Cancel per task. Not in the rail. The status line keeps
   its "Loading…" text. Task 7.
6. **The path dialog (`#path-modal`) is removed.** Save session, load
   session and export use the file browser. Task 2.
7. **Rename opens a small dialog**, not an in-place edit. Task 4.
8. **The export format list includes sGDML (.npz)**. Task 3.

## Rules for the whole job

1. **Do not touch** files this plan does not name. In particular leave
   `CONTEXT.md`, `docs/adr/*`, every `docs/implementation/*` file, the
   desktop client (`UI/`, `main.py`, top-level `launcher.py`) and every
   untracked file (`to_fix*.md`, `docs/research/...`) alone: the user
   commits them. You edit code and tests only. Stage with
   `git add <exact paths>`, never `git add -A` or `git add .`.
2. The user may commit while you work, and other plans (for example
   `2026-10-29-web-parity-1-loading.md`, which adds model loading) may change `static/app.js` at
   the same time. Run `git status --short` and `git log --oneline -3` before
   each commit. Never `git reset --hard`, never rebase, never amend a commit
   you did not just make.
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
   naming style as the file. Mention "web parity" in a comment only where a
   reader would ask "why is it like this?".
7. If the plan does not match the code (a line moved, a name differs), find
   the place by searching for the quoted code, not by line number. If the plan
   is wrong about **behaviour**, stop and report; don't invent another design.
8. A new client→server message needs five edits, every time. Copy how
   `FREEZE_SUBSET` does each one:
   - `ffast/protocol/control.py`: the constant (with a one-line comment), and
     the name in `CLIENT_TO_SERVER`; if it changes the shared session, also in
     `MUTATING_CLIENT_EVENTS`.
   - `ffast/protocol/messages.py`: a request model with
     `model_config = ConfigDict(extra="forbid")` (see `FreezeSubsetRequest`).
   - `ffast/protocol/__init__.py`: import it and add it to `__all__`.
   - `ffast/session/server_session.py`: import it in the
     `from ffast.protocol.messages import (...)` block in `__init__`, add a
     row to the handler table (search `control.FREEZE_SUBSET:`), and write
     the `_on_...` handler.
   - `static/events.js`: the name in `OUT`.
   `tests/ffast/test_control_events.py` and
   `tests/ffast/renderers/web/test_event_constants.py` check that these
   agree; run both.
9. A new server→client announcement needs: the constant in `control.py`
   (in the "dedicated announcements" part, with a comment saying its shape),
   the name in `IN` in `static/events.js`, and the name in the
   `announcements` set of
   `test_inbound_events_are_things_the_server_sends` in
   `tests/ffast/renderers/web/test_event_constants.py`.

Paths are relative to the repo root. `static/` means
`ffast/renderers/web/static/`.

---

## Task 1: the file browser starts in the folder the server was started from, or in `--workdir`

**What the user sees:** `cd ~/runs/aspirin && ffast-web` opens the file
browser in `~/runs/aspirin`, not in the home folder (as the desktop opens
its file dialogs in the folder it was started from).
`ffast-web --workdir ~/runs/aspirin` (and `ffast --workdir …` on a machine
without Qt, which falls through to `ffast-web`) opens it in that folder from
anywhere. `ffast-server --workdir PATH` does the same for a server started
by hand. The ⌂ button still goes to the home folder.

**Facts.**
- Desktop: `main.py` `cli()` reads `--workdir`, makes it absolute with
  `os.path.abspath(os.path.expanduser(...))`, and drops it with a printed
  warning when it is not a folder. `UI/UIHandler.py`:
  `self.workdir = workdir if workdir else os.getcwd()`; the file dialogs
  start there (`UI/mainMenu.py`: `workdir = self.handler.workdir`).
- `ffast/renderers/web/launcher.py`:
  ```python
  def _spawn_server(ws_port: int, host: str = LOOPBACK, trace: bool = False) -> subprocess.Popen:
      args = ["--host", host, "--port", str(ws_port), "--snapshot-interval", "0"]
      if trace:
          args.append("--trace")
      ...
      return subprocess.Popen(cmd)
  ```
  `Popen(cmd)` passes no `cwd`, so the server runs in the folder
  `ffast-web` was started from. `main()` builds
  `spawn_server=functools.partial(_spawn_server, trace=args.trace)`.
- No code calls `os.chdir` (searched `ffast/`, `server.py`, `cluster/`,
  `client/`), so `os.getcwd()` in the server is its start folder.
- `server.py` `cli()` parses `--trace`, `--host`, `--config`, … and calls
  `asyncio.run(_main(args.port, ..., metrics_dir=args.metrics_dir))`.
  `_main(...)` starts with `env = HeadlessEnvironment()`.
- `ffast/session/server_session.py` `_on_list_dir`:
  ```python
  home = os.path.expanduser("~")
  abspath = os.path.abspath(os.path.expanduser(path)) if path else home
  ```
  The web client asks `LIST_DIR` with `null` when the browser first opens
  (`RemoteBrowser.open`: `this._navigate(this._path || null)`), so this
  line decides the start folder. `home` is also sent back (the ⌂ button).
- The browser tests start the server with `cwd=str(REPO_ROOT)`
  (`_spawn_server` in `tests/ffast/renderers/web/test_web_runtime.py`), so
  after this task their file browser starts in the repo folder. The
  existing tests type a path into `#fb-path` before they rely on a folder,
  so none depends on the home folder (checked: no test compares `#fb-path`
  with `expanduser("~")`).

**Change.**
1. `server.py`:
   - `cli()`: add `parser.add_argument("--workdir", type=str, default=None, metavar="PATH", help="Folder the web file browser starts in (default: the folder the server was started from).")`.
     After `args = parser.parse_args()`, resolve it like `main.py` does:
     absolute, `~` expanded; when `os.path.isdir` is false, `logger.warning`
     ("--workdir %s is not a folder; the file browser starts in %s", path,
     `os.getcwd()`) and use `None`. Pass `workdir=workdir` to `_main`.
   - `_main(...)`: new keyword parameter `workdir: str | None = None`; right
     after `env = HeadlessEnvironment()` set
     `env.workdir = workdir or os.getcwd()` with a one-line comment (where
     the web file browser starts; the start folder by default, as on the
     desktop).
2. `ffast/session/server_session.py` `_on_list_dir`: when `path` is empty,
   start at `getattr(self.env, "workdir", None) or os.getcwd()`
   (`getattr`, because tests pass fake environments). Keep `home=home` in
   the reply. Replace the docstring sentence "An empty/None `path` starts at
   the server user's home directory" with: "An empty/None `path` starts at
   `env.workdir` (`--workdir`, else the folder the server was started
   from); `home` is sent back for the ⌂ button."
3. `ffast/renderers/web/launcher.py`:
   - `_spawn_server(..., trace=False, workdir: str | None = None)`: when
     `workdir`, append `"--workdir", workdir`. Mention it in the docstring,
     and that without it the server starts in this process's folder (no
     `cwd` is passed), which is where the file browser then starts.
   - `main()`: `parser.add_argument("--workdir", ...)` with the same help
     text; pass it through:
     `functools.partial(_spawn_server, trace=args.trace, workdir=os.path.abspath(os.path.expanduser(args.workdir)) if args.workdir else None)`.
     (The server checks that it is a folder.)

**Test.**
- `tests/ffast/renderers/web/test_launcher.py`
  `test_spawn_server_passes_the_workdir`: copy
  `test_spawn_server_binds_the_given_host`; call
  `launcher._spawn_server(8765, host="127.0.0.1", workdir="/data/runs")`;
  assert `cmd[cmd.index("--workdir") + 1] == "/data/runs"`; and a second call
  without `workdir` has no `"--workdir"` in `cmd`.
- `tests/ffast/session/test_server_session.py`
  `test_list_dir_starts_in_the_workdir(tmp_path)`: `env = _FakeEnv()`,
  `env.workdir = str(tmp_path)`; `(tmp_path / "a.xyz").write_text("")`;
  dispatch `"LIST_DIR"` with args `[None]`, kwargs `{}` on
  `ServerSession(env, asyncio.Queue())` (copy the `scenario()` pattern of
  `test_dispatch_export_subset_missing_dataset_emits_error`); unpack the
  reply: `kwargs["path"] == str(tmp_path)`, an entry named `a.xyz`, and
  `kwargs["home"] == os.path.expanduser("~")`. Also
  `test_list_dir_without_workdir_starts_in_the_current_folder(tmp_path, monkeypatch)`:
  plain `_FakeEnv()`, `monkeypatch.chdir(tmp_path)`;
  `kwargs["path"] == str(tmp_path)` (compare with `os.path.realpath` on
  both sides: on macOS `tmp_path` may sit under a `/private` link).
- `tests/ffast/renderers/web/test_web_runtime.py`:
  - `test_web_the_file_browser_starts_in_the_workdir(tmp_path)`:
    `async with _spawn_server("--workdir", str(tmp_path)) as (ws_port, web_port)`,
    put `tmp_path / "start-here.xyz"` in it, `_wait_for_server_ready`, open the
    page, `_file_menu(page, "Load Dataset…")`, expect `#fb-path` to have value
    `str(tmp_path)` and `#fb-list` to contain `start-here.xyz`.
  - `test_web_the_file_browser_starts_where_the_server_started(ffast_web_server)`:
    open the page, `_file_menu(page, "Load Dataset…")`, expect `#fb-path` to
    have value `str(REPO_ROOT)` (the test server runs with
    `cwd=str(REPO_ROOT)`).

**Commit:** `feat: the web file browser starts where the server was started, or in --workdir`.

---

## Task 2: save session, load session and export choose their place in the file browser

**What the user sees:** File → Save Session… opens the file browser titled
"Save Session" with a **Name** box (`ffast-session`) and a **Save** button:
the session is saved as that folder inside the folder on screen. File →
Load Session… opens the browser titled "Load Session": walk into the
session folder and press **Load**. File → Export Selected Dataset… opens it
titled "Export Dataset" with a Name box (`<dataset>.extxyz`) and an
**Export** button; clicking a file puts its name in the box (to overwrite
it). The typed-path dialog is gone. Typing a path in the browser's path box
still works.

**Facts.**
- `static/session_ops.js` `SessionOps` opens one path dialog for all three:
  ```js
  saveSession() {
    this._openPathModal({
      title: 'Save Session', okLabel: 'Save', defaultValue: '~/ffast-session',
      onConfirm: (path) => {
        this._ports.send(OUT.SAVE_SESSION, { path });
        this._ports.setStatus(`Saving session to ${path}…`, 'connected');
      },
    });
  }
  ```
  `loadSession()` is the same with `LOAD_SESSION` / "Loading session from";
  `exportSelectedDataset()` sends `OUT.EXPORT_SUBSET, { fingerprint: fp, path }`
  with default `~/${base}.extxyz` where `base = safeName(meta.name, 'subset')`.
  `bindControls()`, `reset()`, `_openPathModal`, `_closePathModal`,
  `_confirmPathModal` serve only the path dialog.
- A session is a **folder** (`ffast/session/persistence.py` `save` makes
  `path/`, `path/cache/`, `path/info.json`; `load` reads `path/info.json`
  and raises "No saved session at …" when it is missing).
- `index.html` has the `#path-modal` block (comment "server-side path
  prompt (issues 20/21)") and CSS for `#path-modal`, `#path-panel`,
  `#path-header`, `#path-body`, `#path-footer`, `#path-input`,
  `#path-error` (search `path-`).
- `static/app.js`: `_initShortcuts` skips keys while
  `#path-modal:not(.hidden)` is open; `_disconnect()` calls
  `this._sessionOps.reset();` and `this._browser.close();`; `_initUI` calls
  `this._sessionOps.bindControls();`. `SessionOps` is built after
  `RemoteBrowser` in the constructor (`this._browser = new RemoteBrowser({...})`
  then `this._sessionOps = new SessionOps({...})`).
- `static/remote_browser.js` `RemoteBrowser`:
  - `open(mode = 'dataset')` sets `this._mode`, the title (`#fb-title`),
    shows `#fb-dataset-fields` or `#fb-prediction-fields`, renders recent
    files and unit choices, traps focus, sets `this._focusFirstRow = true`,
    `this._selected = null`, disables `#fb-load`, and calls
    `this._navigate(this._path || null)`.
  - `onDirListing(kw, args)` sets `this._path`, `#fb-path` and renders rows.
  - `_selectFile(row, name)` marks a file row and, in prediction mode, sends
    `PROBE_DATASET_KEYS`.
  - `_refreshLoadEnabled()` sets `#fb-load.disabled = !canLoad({...})`.
  - `_load()` sends `LOAD_PREDICTION` or `LOAD_DATASET` and closes.
  - Pure, exported: `joinPath(dir, name)`, `canLoad({selected, mode, forceKey, targetDataset})`.
  - `listKey` makes Enter on a file row call `_load()` when `#fb-load` is
    enabled; this stays.
- `index.html` `#fb-footer` holds `#fb-dataset-fields`,
  `#fb-prediction-fields`, a spacer, `#fb-cancel`, `#fb-load`.
- Tests that use the path dialog today, all in
  `tests/ffast/renderers/web/test_web_runtime.py`:
  `test_web_closing_a_dialog_gives_focus_back_to_what_opened_it`
  (`#path-input` focused, Escape hides `#path-modal`),
  `test_web_export_subset_writes_extxyz_and_reports_path`,
  `test_web_save_and_load_session_restores_dataset`,
  `test_web_loading_a_session_that_does_not_exist_shows_an_error`.

**Change.**
1. `static/index.html`:
   - In `#fb-footer`, after `#fb-prediction-fields`, add
     ```html
     <span id="fb-pick-fields" hidden>
       <label for="fb-name">Name</label>
       <input id="fb-name" type="text" spellcheck="false" autocomplete="off">
     </span>
     ```
     and CSS `#fb-pick-fields { display: inline-flex; align-items: center; gap: 6px; }`
     `#fb-pick-fields[hidden] { display: none; }` `#fb-name { width: 220px; }`
     next to the other `#fb-…` rules.
   - Delete the `#path-modal` block and its CSS rules.
2. `static/remote_browser.js`:
   - New exported constant `PICK_MODES = new Set(['save-folder', 'open-folder', 'save-file'])`
     with a comment: the browser also chooses where a session or an export
     goes (web parity).
   - New exported pure function
     ```js
     /** The server path a pick mode returns, or null while there is none:
      *  the folder on screen for 'open-folder', a name inside it otherwise. */
     export function pickPath({ mode, dir, name }) {
       if (!dir) return null;
       if (mode === 'open-folder') return dir;
       const n = String(name ?? '').trim();
       return n ? joinPath(dir, n) : null;
     }
     ```
   - `canLoad`: take `dir = null, name = ''` too; first line
     `if (PICK_MODES.has(mode)) return pickPath({ mode, dir, name }) !== null;`.
   - New method `pick({ mode, title, okLabel, name = '', onPick })`: the
     shared part of `open()` with these differences: `this._pick = { onPick }`;
     `#fb-title` = `title`; `#fb-load` text = `okLabel`; hide
     `#fb-dataset-fields`, `#fb-prediction-fields` and `#fb-recent`; show
     `#fb-pick-fields` unless `mode === 'open-folder'`; `#fb-name.value = name`;
     `this._focusFirstRow = mode === 'open-folder'`; after showing the modal
     focus `#fb-name` (select its text) unless `open-folder`. Pull the
     shared lines out of `open()` into a private `_show()` so both call it.
     `open()` must reset what `pick()` changes: `this._pick = null`,
     `#fb-load` text `Load`, `#fb-pick-fields` hidden, `#fb-recent` shown by
     `_renderRecent()` as today.
   - `bindControls()`: `#fb-name` `input` → `this._refreshLoadEnabled()`;
     `keydown` Enter → `e.preventDefault(); if (!$('fb-load').disabled) this._load();`.
   - `_selectFile(row, name)`: in `'save-file'` mode set `#fb-name.value = name`
     (no probe). Other pick modes: only mark the row.
   - `_refreshLoadEnabled()`: pass `dir: this._path, name: $('fb-name').value`.
   - `onDirListing`: on a successful listing, call `this._refreshLoadEnabled()`
     at the end (the folder on screen changed; in a pick mode that enables
     the button). Check this keeps the dataset mode disabled (no selection).
   - `_load()`: first,
     ```js
     if (this._pick) {
       const path = pickPath({ mode: this._mode, dir: this._path, name: $('fb-name').value });
       if (!path) return;
       const { onPick } = this._pick;
       this._pick = null;
       this.close();
       onPick(path);
       return;
     }
     ```
   - Update the file's top comment: it also picks where a session or an
     export goes.
3. `static/session_ops.js`:
   - Constructor ports: add `pick: (opts) => void` to the JSDoc; drop the
     path-dialog fields (`this._onConfirm`).
   - `saveSession()`: `this._ports.pick({ mode: 'save-folder', title: 'Save Session', okLabel: 'Save', name: 'ffast-session', onPick: (path) => { send SAVE_SESSION; status as today } })`.
   - `loadSession()`: `mode: 'open-folder', title: 'Load Session', okLabel: 'Load'`.
   - `exportSelectedDataset()`: `mode: 'save-file', title: 'Export Dataset', okLabel: 'Export', name: \`${base}.extxyz\``; `onPick` sends
     `EXPORT_SUBSET { fingerprint: fp, path }` as today.
   - Delete `bindControls`, `reset`, `_openPathModal`, `_closePathModal`,
     `_confirmPathModal` and the `trapFocus` import. Update the top comment
     (the paths are now chosen in the file browser).
4. `static/app.js`:
   - `new SessionOps({...})`: add `pick: (opts) => this._browser.pick(opts),`.
   - Remove `this._sessionOps.bindControls();` and `this._sessionOps.reset();`.
   - `_initShortcuts`: drop `, #path-modal:not(.hidden)` from the selector.
   - The comment above `_needsControl` that says "reuse one path prompt —
     the browser has no native server-side save dialog": say they use the
     file browser.

**Test.**
- `tests/ffast/renderers/web/test_web_pure_helpers.py` CASES:
  `"pick_open_folder": "rb.pickPath({mode: 'open-folder', dir: '/runs', name: 'x'})"` → `'/runs'`;
  `"pick_save_folder": "rb.pickPath({mode: 'save-folder', dir: '/runs/', name: ' s1 '})"` → `'/runs/s1'`;
  `"pick_no_name": "rb.pickPath({mode: 'save-file', dir: '/runs', name: ''})"` → `null`;
  `"pick_no_dir": "rb.pickPath({mode: 'save-file', dir: null, name: 'a'})"` → `null`;
  `"load_pick_mode": "rb.canLoad({selected: null, mode: 'open-folder', dir: '/runs'})"` → `true`.
  One `test_…` function per case, like the existing ones.
- `test_web_runtime.py`, update the four tests named in Facts:
  - Save: `_file_menu(page, "Save Session…")`; `#fb-title` has text
    "Save Session"; fill `#fb-path` with `str(tmp_path)`, press Enter, wait
    for `#fb-path` to have value `str(tmp_path)`; fill `#fb-name` with
    `"session"`; click `#fb-load`. `session_dir` stays `tmp_path / "session"`.
  - Load: `_file_menu(page, "Load Session…")`; fill `#fb-path` with
    `str(session_dir)`, Enter, wait for the value; click `#fb-load`.
  - Missing session: navigate to `str(tmp_path)` (needs `tmp_path`; the
    test takes `ffast_web_server` today, add `tmp_path`), click `#fb-load`;
    expect "Session load failed" and "No saved session at <tmp_path>".
    (Navigating to a folder that does not exist only shows a listing error.)
  - Export: fill `#fb-path` with `str(tmp_path)`, Enter, wait, fill
    `#fb-name` with `"sub.extxyz"`, click `#fb-load`.
  - Focus: after "Save Session…", expect `#fb-name` focused; Escape; expect
    `#fb-modal` hidden and the File button focused.
- New `test_web_export_overwrites_a_file_clicked_in_the_list(ffast_web_server, tmp_path)`:
  write `tmp_path / "old.extxyz"`; open Export; go to `tmp_path`; click the
  `old.extxyz` row; expect `#fb-name` value `old.extxyz`; click `#fb-load`;
  expect the status to contain `old.extxyz`, and `ase.io.read` of it has the
  dataset's frame count (`_preload_dataset` loads `DATASET_PATH`; compare
  with `len(ase.io.read(DATASET_PATH, index=":"))`).

Run `-k "session or export or closing_a_dialog or file_browser or loader"`
and `test_web_pure_helpers.py`.

**Commit:** `feat: session save, load and export choose their place in the file browser`.

---

## Task 3: export asks for the file format

**What the user sees:** the Export Dataset browser has a **Format** list:
Extended XYZ, XYZ, ASE database, ASE trajectory, VASP POSCAR (one
structure), DFTB+ gen (one structure), sGDML. Picking one changes the
name's ending (`.extxyz` → `.db`). The file is written in that format.

**Facts.**
- Desktop: `UI/SideBar.py` `saveDataset()` lists, for every dataset type
  with a `saveDataset`, each of its `saveFormats` (ASE:
  `["db", "xyz", "extxyz", "traj", "vasp", "dftb"]`, `ffast/loaders/ase.py`
  `aseDatasetLoader.saveFormats`; sGDML: one format, `None`), and writes
  through `env.persistence.taskSaveDataset(dataset, typ, form, path)`.
- The server already takes a format: `ExportSubsetRequest` has
  `format: Optional[str] = None`. `_on_export_subset`:
  ```python
  fmt = kwargs.get("format")
  if fmt is None:
      ext = os.path.splitext(resolved)[1].lower()
      fmt = "extxyz" if ext in (".xyz", ".extxyz", "") else None
  from ffast.loaders.ase import aseDatasetLoader as saver
  ...
  skipped = int(await asyncio.to_thread(saver.saveDataset, dataset, resolved, fmt) or 0)
  ```
- `ffast/plugins/models/sgdml.py` `sGDMLDatasetLoader` (`datasetName = "sGDML"`)
  has `saveDataset(dataset, path, format=None, taskID=None)` that writes an
  `.npz` and raises `ValueError` for a dataset whose structures differ. The
  server's environment holds dataset types by name in `env.datasetTypes`.
- ASE writes the `vasp` and `dftb` formats for one structure only; for
  more it raises, and the server reports the error as `SUBSET_EXPORTED`
  `{ok: False, error}` (the status line shows "Export failed: …").

**Change.**
1. `ffast/session/server_session.py` `_on_export_subset`: after `fmt` is
   decided, when `fmt == "sgdml"` use
   `saver = getattr(self.env, "datasetTypes", {}).get("sGDML")` and
   `fmt = None`; when that is `None` reply
   `await _reply(False, resolved, error="this server cannot write sGDML files")`
   and return. Otherwise keep the ASE saver. Add a docstring line: the
   format names are ASE's, plus `sgdml`.
2. `static/session_ops.js`:
   - New exported constant (pure data) and helper:
     ```js
     /** What Export can write (the desktop's list, UI/SideBar.py saveDataset). */
     export const EXPORT_FORMATS = [
       { format: 'extxyz', label: 'Extended XYZ', ext: '.extxyz' },
       { format: 'xyz', label: 'XYZ', ext: '.xyz' },
       { format: 'db', label: 'ASE database', ext: '.db' },
       { format: 'traj', label: 'ASE trajectory', ext: '.traj' },
       { format: 'vasp', label: 'VASP POSCAR (one structure)', ext: '.vasp' },
       { format: 'dftb', label: 'DFTB+ gen (one structure)', ext: '.gen' },
       { format: 'sgdml', label: 'sGDML', ext: '.npz' },
     ];

     /** `name` with its ending replaced by `ext` (one ending only). Pure. */
     export function withExtension(name, ext) {
       const stem = String(name ?? '').replace(/\.[A-Za-z0-9]+$/, '');
       return stem + ext;
     }
     ```
   - `exportSelectedDataset()`: pass `formats: EXPORT_FORMATS, format: 'extxyz'`
     to `pick`; `onPick: (path, { format }) => send(OUT.EXPORT_SUBSET, { fingerprint: fp, path, format })`.
3. `static/index.html`: inside `#fb-pick-fields`, after `#fb-name`:
   ```html
   <label for="fb-format" id="fb-format-label">Format</label>
   <select id="fb-format"></select>
   ```
4. `static/remote_browser.js`:
   - `pick({..., formats = null, format = null})`: when `formats`, fill
     `#fb-format` with one `<option value=format>label</option>` each
     (reuse `_fillSelect` with `{value: f.format, label: f.label}`), select
     `format`, show the select and its label; otherwise hide both (`hidden`).
     Keep `formats` on `this._pick`.
   - `bindControls()`: `#fb-format` `change` → set `#fb-name.value` to
     `withExtension(#fb-name.value, ext of the chosen format)`, then
     `_refreshLoadEnabled()`. Import `withExtension` from
     `./session_ops.js`.
   - `_load()` pick branch: `onPick(path, { format: this._pick.formats ? $('fb-format').value : null })`
     (read it before clearing `this._pick`).

**Test.**
- `test_web_pure_helpers.py` CASES: `"ext_swap": "so.withExtension('water.extxyz', '.db')"` → `'water.db'`;
  `"ext_none": "so.withExtension('water', '.traj')"` → `'water.traj'`;
  `"export_formats": "so.EXPORT_FORMATS.map(f => f.format)"` →
  `['extxyz','xyz','db','traj','vasp','dftb','sgdml']`.
- `tests/ffast/session/test_server_session.py`
  `test_dispatch_export_subset_writes_sgdml_with_the_sgdml_saver(tmp_path)`:
  `_FakeEnv(datasets={"ds1": _FakeExportDataset()})`, set
  `env.datasetTypes = {"sGDML": saver}` where `saver` is a
  `unittest.mock.MagicMock()` with `saver.saveDataset.return_value = None`;
  dispatch with `"format": "sgdml"`; assert
  `saver.saveDataset.assert_called_once_with(ds, str(target), None)` and
  `kwargs["ok"] is True`. And
  `test_dispatch_export_subset_without_sgdml_says_so`: no `datasetTypes`;
  `ok is False` and "sGDML" in the error.
- `test_web_runtime.py` `test_web_export_writes_the_chosen_format(ffast_web_server, tmp_path)`:
  open Export, go to `tmp_path`, select `traj` in `#fb-format`, expect
  `#fb-name` value to end with `.traj`, click `#fb-load`, expect the status
  to contain `.traj`, and `ase.io.read(path, index=":")` has the dataset's
  frame count.

**Commit:** `feat: export asks which file format to write`.

---

## Task 4: rename a dataset or prediction

**What the user sees:** the ⋯ menu of a rail row has **Rename…**. It opens
a small dialog with the name; OK renames it on the server, and every window
shows the new name (rail, plot legends, captions). A name with ".", "/" or
"\" is refused with a reason, as on the desktop.

**Facts.**
- Desktop: `UI/SideBar.py` `renameObject` / `onNameEdited` call
  `self.getObject().setName(text)`; the title field only accepts
  `^[^.\\\/]*$` (no ".", "/", "\").
- `ffast/loaders/dataset.py` and `ffast/loaders/model.py`:
  ```python
  def setName(self, name):
      if name == "":
          return self.setName(self.name)
      self.name = name
      if self.loaded:
          self.eventPush("OBJECT_NAME_CHANGED", self.fingerprint)
  ```
  The registries set `loaded = True` on add (`dataset_registry.py`,
  `model_registry.py`). No server message renames today, and the server does
  not announce `OBJECT_NAME_CHANGED` to windows.
- A saved session stores `o.getName()` for every dataset and model
  (`ffast/session/persistence.py` `save`), so a rename is kept.
- `server.py` `_main` announces datasets and models with two local
  functions: `_on_dataset_loaded_meta(fingerprint)` (subscribed to
  `DATASET_LOADED`, `SUBDATASET_INDICES_CHANGED`, `DATASET_STATE_CHANGED`) and
  `_on_model_loaded_meta(model_fp)` (subscribed to `MODEL_LOADED`). The meta
  carries `name` (`toMetaDict()` → `self.getName()`; `model_meta` →
  `getattr(model, "name", None)`).
- Web: `static/app.js` `_railRowActions(kind, fp)`:
  ```js
  const meta = kind === 'dataset' ? this._datasets.get(fp) : null;
  const why = () => this._needsControl();
  if (meta?.parent_frames && !meta.frozen)
    return [{ id: 'dataset-freeze', label: 'Freeze', run: () => this._freezeSubset(fp), unavailable: why }];
  return [{ id: `${kind}-delete`, label: 'Delete…', run: () => this._deleteObject(kind, fp), unavailable: why }];
  ```
- `_onDatasetMeta(fp, meta)` handles a repeated announcement (`before` set):
  it stores the meta and redraws (`_renderObjects()` also calls
  `this._analysis?.setAvailable(...)`, which renames plot series).
- `_onModelMeta(fp, meta)` on a repeated announcement may **select** that
  prediction ("Auto-select a freshly loaded prediction that applies to the
  current dataset"): a rename must not do that.
- `static/rail.js` puts the name in with `innerHTML`
  (`` `<span class="name">${meta.name || fp.slice(0,8)}</span>` ``) in both
  `_renderDatasets` and `_renderModels`: once names are typed by users this
  must be `textContent`.
- `static/dialogs.js` has `askDialog`, `unitPopover`, `textDialog`,
  `trapFocus`, `labelBy`; no dialog asks for a line of text.
- `test_web_closing_a_dialog_gives_focus_back_to_what_opened_it` presses
  Enter on a row's ⋯ and expects `dataset-delete` focused (the first item).

**Change.**
1. Server message `RENAME_OBJECT` (Rules 8; mutating):
   - `control.py`: `# Rename a dataset or prediction; it is announced again to every window.`
     `RENAME_OBJECT = "RENAME_OBJECT"`.
   - `messages.py`: `RenameObjectRequest` with `fingerprint: str`, `name: str`.
   - `server_session.py` handler row
     `control.RENAME_OBJECT: _Route(self._on_rename_object, ["fingerprint", "name"], RenameObjectRequest),`
     and
     ```python
     async def _on_rename_object(self, fingerprint, name, **kwargs) -> None:
         """Rename a dataset or prediction, as the desktop's rename does. The
         name follows the desktop's rule (no ".", "/" or "\\"). setName pushes
         OBJECT_NAME_CHANGED, which server.py turns into a new announcement."""
         obj = self.env.datasets.get(fingerprint) or self.env.models.get(fingerprint)
         if obj is None:
             logger.warning("RENAME_OBJECT: %r not found", fingerprint)
             return
         name = (name or "").strip()
         if not name or any(c in name for c in "./\\"):
             logger.warning("Cannot rename to %r: a name needs a character and no '.', '/' or '\\'", name)
             return
         obj.setName(name)
     ```
2. `server.py` `_main`, after `env.eventSubscribe("MODEL_LOADED", _on_model_loaded_meta)`:
   ```python
   # A rename (RENAME_OBJECT) announces the object again with its new name.
   def _on_name_changed(fingerprint):
       if env.datasets.get(fingerprint) is not None:
           _on_dataset_loaded_meta(fingerprint)
       elif env.models.get(fingerprint) is not None:
           _on_model_loaded_meta(fingerprint)

   env.eventSubscribe("OBJECT_NAME_CHANGED", _on_name_changed)
   ```
3. `static/events.js` `OUT`: `RENAME_OBJECT: 'RENAME_OBJECT',`.
4. `static/rail.js`:
   - Build the name span with `document.createElement('span')`,
     `className = 'name'`, `textContent = meta.name || fp.slice(0, 8)` in
     both lists (the `.meta` span of datasets the same way).
   - New exported pure function:
     ```js
     /** Why `name` cannot name a dataset or prediction, or '' (the desktop's rule). */
     export function nameProblem(name) {
       const n = String(name ?? '').trim();
       if (!n) return 'Type a name';
       if (/[./\\]/.test(n)) return 'A name cannot contain ".", "/" or "\\"';
       return '';
     }
     ```
5. `static/dialogs.js`: new exported
   `askText({ title, label, value = '', problem = () => '', okLabel = 'OK' })`
   returning `Promise<string|null>` (the trimmed text, or null on Cancel or
   Escape). Build it like `askDialog` (`warning-modal ask-dialog` classes,
   `labelBy`, `trapFocus`, focus and select the input). Under the input a
   `<p class="help-text">` shows `problem(input.value)`; the OK button is
   disabled while there is one. Enter = OK when enabled. Give the modal
   `id = 'text-ask'` so tests can find it.
6. `static/app.js`:
   - Import `askText` and `nameProblem`.
   - New method:
     ```js
     /** Rename a dataset or prediction on the server; every window then gets
      * it announced again with the new name. */
     async _renameObject(kind, fp) {
       const meta = (kind === 'dataset' ? this._datasets : this._models).get(fp);
       if (!meta) return;
       const name = await askText({
         title: kind === 'dataset' ? 'Rename dataset' : 'Rename prediction',
         label: 'Name', value: meta.name || '', problem: nameProblem, okLabel: 'Rename',
       });
       if (name === null || name === meta.name || this._needsControl()) return;
       this._conn.send(OUT.RENAME_OBJECT, { fingerprint: fp, name });
     }
     ```
   - `_railRowActions`: return
     `[rename, main]` where
     `rename = { id: \`${kind}-rename\`, label: 'Rename…', run: () => this._renameObject(kind, fp), unavailable: why }`
     and `main` is today's Freeze or Delete… entry. Put a `{ separator: true }`
     between them.
   - `_onModelMeta(fp, meta)`: at the top,
     ```js
     const before = this._models.get(fp);
     // A rename announces a known prediction again with the same datasets:
     // only the name changes, and the main view's choice stays.
     if (before && before.name !== meta?.name && sameItems(before.dataset_fingerprints, meta?.dataset_fingerprints)) {
       this._models.set(fp, meta || {});
       this._panes?.colorBy.refreshModels(this._models);
       this._renderObjects();
       this._syncCaptions();
       return;
     }
     ```
     with a small module-level helper `sameItems(a, b)` (same length, same
     items in order; both missing counts as equal).
7. Tests that press Enter on a row's ⋯ now land on Rename…: in
   `test_web_closing_a_dialog_gives_focus_back_to_what_opened_it`, expect
   `[data-action='dataset-rename']` focused, press `ArrowUp` (wraps to the
   last item), then expect `dataset-delete` focused and go on as before.

**Test.**
- `tests/ffast/session/test_server_session.py`:
  - `test_rename_object_renames_a_dataset_or_prediction`: a tiny fake
    `class _Named: def __init__(self): self.names = []; def setName(self, n): self.names.append(n)`;
    `_FakeEnv(datasets={"d": ds}, models={"m": model})`; dispatch
    `RENAME_OBJECT {"fingerprint": "d", "name": "  water "}` and
    `{"fingerprint": "m", "name": "MACE"}`; assert `ds.names == ["water"]`,
    `model.names == ["MACE"]`.
  - `test_rename_object_refuses_an_empty_or_dotted_name`: `""`, `"a.b"`,
    `"a/b"` leave `names == []`.
  - `test_rename_object_is_a_change_a_viewer_may_not_make`:
    `control.RENAME_OBJECT in control.MUTATING_CLIENT_EVENTS`.
- `test_web_pure_helpers.py`: import `rail.js` as `rl`; cases
  `"name_ok": "rl.nameProblem(' water ')"` → `''`,
  `"name_empty": "rl.nameProblem('  ')"` → `'Type a name'`,
  `"name_dot": "rl.nameProblem('a.b')"` (non-empty).
- `test_web_runtime.py` `test_web_the_rail_menu_renames_for_every_window(ffast_web_server)`:
  `_preload_dataset_and_prediction`; `_open_loupe`; open the dataset row's
  ⋯, click `.rail-menu [data-action='dataset-rename']`; in `#text-ask`
  fill the input with `a.b` → its OK button is disabled; fill `water` and
  press Enter; expect the row's `.name` to have text `water`; then
  `_replayed_datasets(ws_port)[dataset_fp]["name"] == "water"` (a new
  window gets the new name). Rename the prediction to `MACE`; expect the
  model row `.name` "MACE" and the selected prediction
  (`window.ffastApp._currentModelFp`) unchanged. Last, rename the dataset
  to `<i>x` (allowed: no ".", "/" or "\"): the row's `.name` has text
  `<i>x` and contains no `i` element (the name is text, not HTML).

Run `-k "rename or rail_menu or closing_a_dialog"` and the pure helpers.

**Commit:** `feat: datasets and predictions can be renamed from the rail menu`.

---

## Task 5: an Info entry shows what a dataset or prediction is

**What the user sees:** the ⋯ menu has **Info** first. It opens a dialog
with the name as title and rows: Kind, Frames, Atoms per structure,
Formula, Forces, Energy unit / Force unit (when named), File,
Cut from (for a subset), Fingerprint. For a prediction: Kind, Results for
(the dataset names), Fingerprint.

**Facts.**
- Desktop: `client/object_info.py` `describe(obj)` = display name, the
  loader's `datasetName` / `modelName`, and `getBaseInfo()` ("N. conf.",
  "N. atoms", "Chem. form.") plus `getInfo()`. Formula rule
  (`ffast/loaders/dataset.py` `zToChemicalFormula`): `C<n>` then `H<n>`
  (count always written), then the other symbols in alphabetical order, count
  written only when 2 or more.
- The web meta (`REMOTE_DATASET_META`, `DatasetMeta` in
  `ffast/protocol/messages.py`): `name, n, has_forces, is_sub, variable,
  elements, offsets, path, source_type, parent, parent_frames, active,
  frozen, units`. Checked by running `toMetaDict()`: a uniform dataset
  (same atoms in every structure) sends one structure's atomic numbers
  (`[1, 1, 8]`, `offsets: None`); a variable one sends every atom of every
  structure and the start of each structure (`[1,1,8,1,1,8,6,1,1,1,1]`,
  `offsets: [0, 3, 6, 11]`).
- A subset: `parent` is the dataset it was cut from; `parent_frames` is set
  for a frame subset and `null` for an atom or element subset; `frozen`
  marks a frozen one.
- Prediction meta: `{name, dataset_fingerprints}`.
- `static/panels.js` exports `elementSymbol(z)`. `static/units.js` exports
  `datasetUnits(fp, datasets)` (a subset reads its root's units).
  `static/rail.js` exports `framesText(n)`.

**Change.**
1. New `static/object_info.js` (pure, no DOM):
   - `export function chemicalFormula(zs)` following the desktop rule above.
   - `export function objectInfo(kind, fp, meta, datasets)` →
     `{ title, rows: [[label, value], ...] }`:
     - title: `meta.name || fp.slice(0, 8)`.
     - dataset rows, in this order, skipping a row whose value is empty:
       `Kind`: `Frozen subset` / `Subset` (has `parent_frames`) /
       `Atom subset` (has `parent`, no `parent_frames`) / else
       `${meta.source_type || 'Dataset'} file`;
       `Cut from`: the parent's name (`datasets.get(meta.parent)?.name`, else
       the first 8 characters of `meta.parent`);
       `Frames`: `framesText(meta.n)`;
       `Atoms per structure`: uniform → `elements.length`; variable → the
       smallest and largest of the `offsets` differences, `"3–5"` (or one
       number when equal);
       `Formula`: uniform → `chemicalFormula(elements)`; variable →
       `` `varies (first: ${chemicalFormula(elements.slice(offsets[0], offsets[1]))})` ``;
       `Forces`: `yes` / `no` from `has_forces`;
       `Energy unit`, `Force unit`: from `datasetUnits(fp, datasets)`;
       `File`: `meta.path`;
       `Fingerprint`: `fp`.
     - prediction rows: `Kind`: `Prediction`; `Results for`: the names of
       `meta.dataset_fingerprints` (via `datasets`, else 8 characters),
       joined with ", "; `Fingerprint`: `fp`.
2. `static/dialogs.js`: new exported `infoDialog({ title, rows })`: a
   `warning-modal` with `id = 'info-dialog'`, `labelBy`, a `<table>` with
   one `<tr><th>label</th><td>value</td></tr>` per row (text via
   `textContent`), a Close button, Escape closes, `trapFocus`. Small CSS in
   `index.html` next to `.text-dialog-panel`:
   `#info-dialog th { text-align: left; color: var(--muted); font-weight: normal; padding-right: 12px; }`
   `#info-dialog td { overflow-wrap: anywhere; }`.
3. `static/app.js`: import both; `_railRowActions` puts first
   `{ id: \`${kind}-info\`, label: 'Info', run: () => this._showInfo(kind, fp) }`
   (no `unavailable`: a read-only window may look), then Rename…, the
   separator, Freeze/Delete…. New method `_showInfo(kind, fp)`: look up the
   meta (`this._datasets` or `this._models`), return when missing, else
   `infoDialog(objectInfo(kind, fp, meta, this._datasets))`.
4. `test_web_closing_a_dialog_gives_focus_back_to_what_opened_it`: the
   first focused item is now `dataset-info`; `ArrowUp` still wraps to
   `dataset-delete`.

**Test.**
- `test_web_pure_helpers.py`: import `object_info.js` as `oi`; cases
  `"formula_aspirin": "oi.chemicalFormula([6,6,6,6,6,6,6,6,6,1,1,1,1,1,1,1,1,8,8,8,8])"` → `'C9H8O4'`;
  `"formula_water": "oi.chemicalFormula([1,1,8])"` → `'H2O'`;
  `"info_uniform": "oi.objectInfo('dataset', 'abcdef0123', {name: 'w', n: 4, elements: [1,1,8], variable: false, has_forces: true, source_type: 'ase', path: '/d/w.xyz', units: {energy: 'eV'}}, new Map([['abcdef0123', {units: {energy: 'eV'}}]])).rows"`
  → contains `['Atoms per structure', '3']`, `['Formula', 'H2O']`,
  `['Energy unit', 'eV']`, `['Kind', 'ase file']`;
  `"info_variable"` with `elements: [1,1,8,6,1,1,1,1], offsets: [0,3,8], variable: true`
  → `['Atoms per structure', '3–5']`, `['Formula', 'varies (first: H2O)']`;
  `"info_subset"` (`parent: 'P'`, `parent_frames: [1,2]`, `frozen: true`, the
  `FL` map from the test file for `datasets`) → `['Kind', 'Frozen subset']`
  and `['Cut from', 'P']`;
  `"info_prediction"`: `oi.objectInfo('model', 'm1', {name: 'MACE', dataset_fingerprints: ['T']}, FL).rows`
  → `['Results for', 'ethanol']`.
- `test_web_runtime.py` `test_web_the_rail_menu_shows_info(ffast_web_server)`:
  load dataset and prediction, open each row's ⋯ → Info; `#info-dialog`
  contains "Frames" and the frame count text, and "Fingerprint" with the
  fingerprint; for the prediction "Results for" and "dataset"; Escape
  closes it.

**Commit:** `feat: the rail menu's Info shows what a dataset or prediction is`.

---

## Task 6: the server names its tasks and can cancel one

**What the user sees:** nothing yet; this is the server half of the
tasks list.

**Facts.**
- `ffast/core/tasks.py` `TaskManager.newTask(func, ..., visual=False, name="?", ...)`
  stores `{visual, name, ...}` in `self.runningTasks[taskID]` and then pushes
  `TASK_CREATED` with only `taskID` (an int, or a string task key).
  `cancelTask(taskID)` (subscribed to `TASK_CANCEL`) cancels the asyncio
  task and pushes `TASK_DONE`. A threaded task's thread cannot be stopped:
  cancel stops the waiting, the thread finishes in the background (same as
  the desktop).
- `TASK_PROGRESS` kwargs: `prog`, `progMax`, `percent`, `message`, `error`,
  `title` (see `TaskManager.setTaskProgress`; `title` renames the task).
- `HeadlessEnvironment` has `env.getTask(taskID)` and `env.eventPush`.
- `server.py` `_main` forwards `TASK_CREATED`, `TASK_PROGRESS`,
  `TASK_DONE`, `TASK_FAILED` to every window through the loop
  `for evt in SERVER_TO_CLIENT: ... env.eventSubscribe(evt, make_sender(evt))`.
  `TASK_CREATED` carries no name, so a window cannot label a task.
- Desktop over a server: `cluster/inbound_router.py` makes a "Remote task"
  entry, and `cluster/connection.py` drops any event not in
  `CLIENT_ENV_SAFE` ("skipping non-local-safe event"), so a new
  announcement does not disturb the desktop. Do **not** add kwargs to
  `TASK_CREATED` itself: the desktop's `TasksList.onTaskCreated(taskID)` takes
  no keyword arguments.
- No message cancels a server task today (`TASK_CANCEL` is a local event).

**Change.**
1. Announcement `TASK_INFO` (Rules 9): in `control.py`:
   ```python
   # Sent to every window right after TASK_CREATED: args=(task_id,),
   # {name, visual}. TASK_CREATED carries only the id; the Tasks list shows
   # visual tasks by name. One-shot announcement, out of REPLY_EVENTS.
   TASK_INFO = "TASK_INFO"
   ```
2. `server.py` `_main`, right after the `for evt in SERVER_TO_CLIENT:` loop:
   ```python
   # Tasks by name (web Tasks list): TASK_CREATED carries only the id.
   def _on_task_created_info(task_id):
       task = env.getTask(task_id) or {}
       hub.broadcast(pack(control.TASK_INFO, (task_id,), {
           "name": str(task.get("name") or "Task"),
           "visual": bool(task.get("visual")),
       }))

   env.eventSubscribe("TASK_CREATED", _on_task_created_info)
   ```
   (`pack` and `control` are already imported at the top of `_main`.)
3. Client message `CANCEL_TASK` (Rules 8; mutating):
   - `control.py`: `# Cancel a running task, as the desktop Tasks list's Cancel does.`
     `CANCEL_TASK = "CANCEL_TASK"`.
   - `messages.py`: `CancelTaskRequest` with `task_id: Union[int, str]`
     (import `Union` from `typing` if the file lacks it).
   - `server_session.py`: row
     `control.CANCEL_TASK: _Route(self._on_cancel_task, ["task_id"], CancelTaskRequest),`
     and
     ```python
     async def _on_cancel_task(self, task_id, **kwargs) -> None:
         """Cancel a running task (the Tasks list's Cancel). The task manager
         cancels it and sends TASK_DONE, so every window drops its row. Work
         already running in a thread finishes in the background, as on the
         desktop."""
         if self.env.getTask(task_id) is None:
             logger.info("CANCEL_TASK: task %r is not running", task_id)
             return
         self.env.eventPush("TASK_CANCEL", task_id)
     ```
4. `static/events.js`: `OUT.CANCEL_TASK`, `IN.TASK_INFO` (with a comment
   each). Do not touch `ffast/protocol/notifications.py` or
   `SERVER_TO_CLIENT`: they list only the generic broadcasts, and
   `TASK_INFO` is an announcement.

**Test.**
- `tests/ffast/session/test_server_session.py`:
  - `test_cancel_task_asks_the_task_manager`: a fake env
    (`types.SimpleNamespace`) with `getTask=lambda i: {"name": "x"} if i == 7 else None`
    and `eventPush` that records `(event, args)`; dispatch
    `CANCEL_TASK {"task_id": 7}` → recorded `("TASK_CANCEL", (7,))`;
    dispatch `{"task_id": 8}` → nothing more.
  - `test_cancel_task_is_a_change_a_viewer_may_not_make`.
- `tests/ffast/test_task_cancel.py` (new) `test_a_cancelled_task_stops_and_reports_done`:
  build a real `TaskManager()`; inside one `asyncio.run(scenario())`:
  `async def slow(taskID=None): await asyncio.sleep(30)`;
  `tm.newTask(slow, name="Slow", visual=True)`; record `TASK_DONE` with a
  small `EventClass` subclass subscribed to it; `tm.eventPush("TASK_CANCEL", id)`;
  call `await tm.eventHandle()` (and the recorder's) a few times with
  `await asyncio.sleep(0)` between until `TASK_DONE` is recorded or 2 s
  pass; assert it was recorded and `tm.getTask(id) is None`. Read
  `ffast/core/events.py` first: the bus is module-global, so unsubscribe or
  delete the objects at the end (`deleteEvents()`), and if this cannot be
  made reliable in under 2 s, drop this test and say so in the report.
- `test_web_runtime.py` `test_the_server_names_each_task(ffast_web_server, tmp_path)`:
  `_connect_headless_client(ws_port)`, send `SAVE_SESSION` with
  `{"path": str(tmp_path / "s")}`, `_wait_for_event(ws, "TASK_INFO")` →
  kwargs `{"name": "Saving session", "visual": True}` and `args[0]` equal to
  the `TASK_CREATED` id seen before it (record both).
- Run `tests/ffast/test_control_events.py` and
  `tests/ffast/renderers/web/test_event_constants.py`.

**Commit:** `feat: the server names each task and cancels one on request`.

---

## Task 7: a Tasks button in the top bar opens the running tasks, with progress and Cancel

**What the user sees:** while a long job runs (loading a dataset or a
prediction, saving or loading a session, an example load), a **Tasks (1)**
button appears in the top bar, just left of the status label. The number is
how many tasks run. Nothing else changes on screen: the status line still
says "Loading …". A click on the button opens a drop-down list (a click
outside, Escape or a second click closes it). Each row has the task's name,
its message ("Loading 120/500"), a progress bar (moving stripes when the job
gives no numbers), and a × Cancel button. A row goes when its task ends. The
button disappears (and the list closes) when no task runs.

Chosen detail: the button is shown **only while at least one task runs**.
That is as simple as showing it always (one `hidden` toggle in the same
place that updates the count) and keeps the top bar quiet.

**Facts.**
- Desktop: `UI/SideBar.py` `TaskItem`: title = task name, message label
  ("Working..." until progress), progress text `"{prog}/{progMax}"` or
  `"{prog/progMax*100:.0f}%"` when `percent`, a progress bar, red message
  when `error`, title replaced when `title` comes; Cancel pushes
  `TASK_CANCEL`. `TasksList` shows only tasks with `visual` true and removes
  a row on `TASK_DONE`.
- Web today, `static/app.js` `_connect()`:
  ```js
  conn.on(IN.TASK_CREATED,  (kw) => console.debug('TASK_CREATED', kw));
  conn.on(IN.TASK_PROGRESS, (kw, args) => { this._metricOps.onProgress(kw, args); console.debug('TASK_PROGRESS', kw); });
  // No per-task correlation id travels back to the browser today (Qt's
  // equivalent is a generic "Tasks" sidebar list, out of scope here), so
  // ...
  conn.on(IN.TASK_DONE,    (kw) => { console.debug('TASK_DONE', kw); this._browser.onTaskDone(); });
  conn.on(IN.TASK_FAILED,  (kw) => console.warn('TASK_FAILED', kw));
  ```
  `conn.on(event, (kw, args) => …)`: `args[0]` is the task id.
  `TASK_PROGRESS` is also sent for a metric Try with a string `try_id`
  (`metric_ops.js` handles it); those ids are never in the tasks list, so
  they are ignored there.
- A failed task sends `TASK_DONE` then `TASK_FAILED`; its error already pops
  up through `SERVER_WARNING` (`TaskManager.handleTaskResult` logs it with
  `popup`), so the list only removes the row.
- `static/index.html` top bar (`#topbar`, a flex row) ends with:
  ```html
  <select id="density" aria-label="Density"> ... </select>
  <!-- The connection status; clicking it opens the connection dialog (ADR 0055). -->
  <button id="status" type="button" title="Connection…">Disconnected</button>
  ```
  `#view-mode` has `margin-left: auto`, so everything after it sits at the
  right edge. Drop-downs use `.menu` (`position: relative`) around a
  `.menu-list` (absolute, under the button; `.menu-list.hidden` hides it;
  `.menu-list.right` aligns it to the button's right edge, needed here so
  the list does not run off the window).
- `static/actions.js` `bindMenu(button, list, actions)` builds menu items
  from actions, so it does not fit a list of progress rows; copy its way of
  closing (Escape, `pointerdown` outside, a second click) instead.
- `static/icons.js` has `close`; `icon(name)` returns its SVG.
- `_needsControl()` returns '' or why this window may not change the session.

**Change.**
1. `static/index.html`:
   - In `#topbar`, right before the `#status` comment:
     ```html
     <!-- Running tasks with progress and Cancel (web parity; the desktop's
          Tasks list). Shown only while a task runs (tasks.js). -->
     <div id="task-menu" class="menu" hidden>
       <button id="task-btn" type="button" aria-expanded="false" aria-controls="task-panel"
               title="Running tasks">Tasks</button>
       <div id="task-panel" class="menu-list right task-panel hidden" role="list" aria-label="Running tasks"></div>
     </div>
     ```
     (`.menu` sets no `display`, so the `hidden` attribute works as is.)
   - CSS next to the `#status` rules:
     `.task-panel { width: 300px; max-height: 60vh; overflow-y: auto; gap: 2px; }`,
     `.task-row { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 2px 6px; padding: 5px 8px; }`,
     `.task-row .task-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`,
     `.task-row .task-count { color: var(--muted); font-size: var(--fs-s); }`,
     `.task-row .task-cancel { padding: 2px 4px; background: transparent; }`,
     `.task-row .task-message { grid-column: 1 / -1; color: var(--muted); font-size: var(--fs-s); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`,
     `.task-row .task-message.error { color: var(--danger); }`,
     `.task-row progress { grid-column: 1 / -1; width: 100%; height: 6px; accent-color: var(--hl2); }`.
2. New `static/tasks.js`:
   - Top comment: the running tasks of the server, behind the top bar's
     Tasks button (web parity with the desktop's Tasks list); the server
     names a task with `TASK_INFO` and cancels one on `CANCEL_TASK` (Task 6).
   - `export function progressText({ prog, progMax, percent } = {})`: `''`
     when either number is missing; `` `${Math.round(prog / progMax * 100)}%` ``
     when `percent`; else `` `${prog}/${progMax}` ``. Pure.
   - `export class TaskList` with constructor
     `({ menu, button, panel, onCancel, whyNoCancel })` (the `#task-menu`
     wrapper, `#task-btn`, `#task-panel`). The constructor binds:
     `button` click → `toggle()`; `panel` `keydown` Escape → `close()` and
     `button.focus()`; `document` `pointerdown` outside `menu` → `close()`.
     Methods:
     - `info(id, { name, visual })`: when `visual` and `id` is unknown, add a
       row to `panel` (`div.task-row`, `role=listitem`, `data-task-id`):
       `.task-name` (textContent `name`), `.task-count` (empty), the cancel
       button (`button.task-cancel`, innerHTML `icon('close')`,
       `title`/`aria-label` "Cancel <name>"; disabled with the reason as
       its title when `whyNoCancel()` is not ''), `.task-message`
       "Working…", `<progress>` with no `value` (shows moving stripes).
       Then `_refresh()`.
     - `progress(id, kw)`: ignore unknown ids; set the message
       (`kw.message || 'Working…'`), toggle `.error` on it from `kw.error`,
       set the count with `progressText(kw)`, set `progress.max`/`value`
       when both numbers come (else remove the `value` attribute), and the
       name (and the cancel button's label) when `kw.title`.
     - `done(id)`: remove the row; `_refresh()`.
     - `reset()`: remove all rows; `_refresh()`.
     - `toggle()`, `open()`, `close()`: show or hide `panel` with the
       `hidden` class and set `button`'s `aria-expanded`.
     - private `_refresh()`: `button.textContent = \`Tasks (${n})\``;
       `menu.hidden = n === 0`; when `n === 0` also `close()`.
     The cancel click calls `onCancel(id)` and disables the button (the
     row goes when `TASK_DONE` arrives).
     Keep rows in a `Map` by id; ids arrive as numbers or strings, so key by
     `String(id)` and send back the original `id` value.
3. `static/app.js`:
   - Import `TaskList` from `./tasks.js`. In the constructor (next to
     `this._rail = new Rail(...)`):
     ```js
     this._tasks = new TaskList({
       menu: document.getElementById('task-menu'),
       button: document.getElementById('task-btn'),
       panel: document.getElementById('task-panel'),
       onCancel: (id) => { if (!this._needsControl()) this._conn.send(OUT.CANCEL_TASK, { task_id: id }); },
       whyNoCancel: () => this._needsControl(),
     });
     ```
   - `_connect()`: add `conn.on(IN.TASK_INFO, (kw, args) => this._tasks.info(args[0], kw));`;
     in the `TASK_PROGRESS` handler also call `this._tasks.progress(args[0], kw)`;
     in the `TASK_DONE` handler also call `this._tasks.done(args[0])`.
     Replace the comment block above `TASK_DONE` ("No per-task correlation
     id travels back… out of scope here …") with one line: the Tasks button
     lists running tasks; `_browser.onTaskDone` still ends a pending
     save/load status.
   - `_disconnect()`: `this._tasks.reset();`.
   - Do not change any `_setStatus` call: the status line keeps its
     "Loading …" text.

**Test.**
- `test_web_pure_helpers.py`: import `tasks.js` as `tk`; cases
  `"progress_count": "tk.progressText({prog: 3, progMax: 10})"` → `'3/10'`;
  `"progress_percent": "tk.progressText({prog: 1, progMax: 4, percent: true})"` → `'25%'`;
  `"progress_none": "tk.progressText({message: 'x'})"` → `''`;
  and one DOM case (the page is a blank document), an IIFE:
  ```js
  (() => {
    const menu = document.createElement('div'); menu.hidden = true;
    const button = document.createElement('button');
    const panel = document.createElement('div'); panel.className = 'hidden';
    menu.append(button, panel); document.body.appendChild(menu);
    const cancelled = [];
    const t = new tk.TaskList({ menu, button, panel, onCancel: (id) => cancelled.push(id), whyNoCancel: () => '' });
    t.info(7, { name: 'Loading save', visual: true });
    t.info(8, { name: 'metric', visual: false });
    t.progress('try-1', { message: 'ignored' });
    t.progress(7, { prog: 2, progMax: 5, message: 'Reading' });
    const row = panel.querySelector('.task-row');
    const shown = { rows: panel.children.length, hidden: menu.hidden, label: button.textContent,
      text: row.textContent, value: row.querySelector('progress').value };
    button.click();
    const opened = !panel.classList.contains('hidden');
    row.querySelector('.task-cancel').click();
    t.done(7);
    return { ...shown, opened, cancelled, after: panel.children.length,
      hiddenAfter: menu.hidden, closedAfter: panel.classList.contains('hidden') };
  })()
  ```
  → `rows 1`, `hidden false`, `label 'Tasks (1)'`, text contains
  "Loading save", "Reading", "2/5", `value 2`, `opened true`,
  `cancelled [7]`, `after 0`, `hiddenAfter true`, `closedAfter true`.
- `test_web_runtime.py` `test_web_tasks_button_shows_a_running_task_and_cancels_it(ffast_web_server)`:
  open the page connected; expect `#task-menu` hidden. A server task ends
  too fast to click, so drive the list with `page.evaluate`:
  `window.ffastApp._tasks.info(99, {name: 'Saving session', visual: true})`;
  expect `#task-btn` visible with text "Tasks (1)" and `#task-panel`
  hidden (closed until clicked); click `#task-btn`; expect a row
  "Saving session" visible; capture outgoing messages by wrapping
  `window.ffastApp._conn.send` in `page.evaluate` (store `[event, kwargs]`
  in `window.__sent`); click the row's `.task-cancel`; expect
  `window.__sent` to contain `["CANCEL_TASK", {task_id: 99}]`; then
  `window.ffastApp._tasks.done(99)` hides `#task-menu`.
- No status code changes, so no new status test: run the existing
  `-k "loader or session"` browser tests to show the "Loading …" texts
  still pass.

**Commit:** `feat: a Tasks button in the top bar lists running tasks with progress and Cancel`.

---

## Task 8: Alt+Shift keyboard shortcuts for the file actions

**What the user sees:** Alt+Shift+S (Option+Shift+S on a Mac) opens Save
Session, Alt+Shift+L Load Session, Alt+Shift+D Load Dataset, Alt+Shift+P
Load Prediction, Alt+Shift+M Load Model, Alt+Shift+0 Load Zero Model,
Alt+Shift+N opens the 3D view in a new window, Alt+Shift+C Connect to
Cluster. The File menu shows each item's keys at its right edge (⌥⇧S on a
Mac, Alt+Shift+S elsewhere). A key whose action cannot run now shows the
reason in the status line. While the cursor is in a text box (search,
name, path, a formula) the keys do nothing, so typing is never disturbed.
The browser's own keys (Ctrl+S, Ctrl+D, Ctrl+N, …) keep their browser
meaning.

**Facts.**
- Desktop `UI/mainMenu.py`: Save Ctrl+S, Load Ctrl+L, Load Dataset Ctrl+D,
  Load Model Ctrl+M, Load Zero Model Ctrl+0, Load Prediction Ctrl+P,
  3D View → New Ctrl+N. The web uses the same letters with Alt+Shift
  (Decision 3): Chrome on Windows and Linux uses Alt+D, Alt+E, Alt+F
  itself, and a page can never take Ctrl+N.
- With Option (Alt) held, a Mac keyboard types a special character
  (Option+Shift+D gives "Î"), so `event.key` is useless; `event.code`
  names the physical key (`KeyD`, `Digit0`) on every system.
- Checked with the installed Playwright 1.60 Chromium:
  `page.keyboard.press("Alt+Shift+KeyD")` gives a `keydown` with
  `code 'KeyD'`, `altKey true`, `shiftKey true`, `ctrlKey false`,
  `metaKey false`; `"Alt+Shift+Digit0"` gives `code 'Digit0'`;
  `"Alt+Shift+KeyN"` gives `code 'KeyN'`.
- The page sets no `accesskey` attribute (searched `static/`); Firefox
  uses Alt+Shift+letter for access keys, so do not add any.
- `static/actions.js` top comment: "One action list (ADR 0055) feeds the
  File menu, the keyboard shortcuts and the command palette". Actions are
  `{id, label, run, unavailable?}`; `runAction(action)` runs it when
  `whyUnavailable(action)` is ''. `fillMenu(list, entries, close)` builds
  each `[role=menuitem]` with `dataset.action`, `textContent = label`.
- `static/app.js`: `this._actions = this._buildActions();` in `_initUI`;
  `_action(id)` finds one; ids today: `load-dataset`, `load-prediction`,
  `load-example`, `save-session`, `load-session`, `export-dataset`,
  `connect`, `style-…`. `_initShortcuts()` handles ←/→/Space/Esc/`/` and
  returns at once on `e.ctrlKey || e.metaKey || e.altKey`, so it never
  sees an Alt+Shift key; it is called before `_buildActions`.
- Actions added by other plans: `load-model` and `load-zero-model`
  (`2026-10-29-web-parity-1-loading.md`, tasks 8 and 9) and
  `connect-cluster` (`2026-10-29-web-parity-4-cluster.md`). If a plan is
  not built yet, `this._action(id)` returns nothing and the key does
  nothing.
- The pop-out: `#popout-btn` ("Open this 3D view in a new browser tab"),
  click → `this._openPopout()`; it is disabled until a view is open
  (`document.getElementById('popout-btn').disabled`).
- Assumption (not checked on every system): no browser or desktop takes
  Alt+Shift+S/L/D/P/M/0/N/C on Windows, Linux or macOS. On Windows,
  Alt+Shift alone may switch the keyboard language when several are
  installed; with a letter pressed too it reaches the page.

**Change.**
1. New `static/shortcuts.js` (pure):
   ```js
   /** The file shortcuts: the desktop's letters (UI/mainMenu.py) with
    *  Alt+Shift (Option+Shift on a Mac), so no key fights the browser.
    *  Matched by `code`, because Option on a Mac changes `key`. */
   export const FILE_SHORTCUTS = [
     { action: 'save-session', code: 'KeyS' },
     { action: 'load-session', code: 'KeyL' },
     { action: 'load-dataset', code: 'KeyD' },
     { action: 'load-prediction', code: 'KeyP' },
     // Added by the loading plan (tasks 8 and 9) and the cluster plan; until
     // those are built the keys do nothing.
     { action: 'load-model', code: 'KeyM' },
     { action: 'load-zero-model', code: 'Digit0' },
     { action: 'open-popout', code: 'KeyN' },
     { action: 'connect-cluster', code: 'KeyC' },
   ];

   /** Is this a Mac (Option and ⇧ symbols in the menu)? */
   export function isMac(nav = globalThis.navigator) {
     const p = nav?.userAgentData?.platform || nav?.platform || '';
     return /Mac|iPhone|iPad/.test(p);
   }

   /** The shortcut a key press means, or null. `e` is a KeyboardEvent (or a
    *  plain object with the same fields); `inText`: focus is in a text
    *  field, where keys belong to the typing. */
   export function matchShortcut(e, { inText = false } = {}) {
     if (inText || !e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey) return null;
     return FILE_SHORTCUTS.find((s) => s.code === e.code) || null;
   }

   /** How a menu shows the keys: "Alt+Shift+S", or "⌥⇧S" on a Mac. */
   export function shortcutLabel(s, mac = false) {
     const letter = s.code.replace(/^Key|^Digit/, '');
     return mac ? `⌥⇧${letter}` : `Alt+Shift+${letter}`;
   }
   ```
2. `static/actions.js` `fillMenu`: when `action.keys`, set
   `item.dataset.keys = action.keys` and
   `item.setAttribute('aria-keyshortcuts', action.ariaKeys || action.keys)`.
   Document `keys?` and `ariaKeys?` in the `Action` typedef. `index.html` CSS:
   `.menu-list [role=menuitem][data-keys] { display: flex; gap: 16px; }`
   `.menu-list [role=menuitem][data-keys]::after { content: attr(data-keys); margin-left: auto; color: var(--muted); }`
   (pseudo-element text is not part of `textContent`, so
   `test_web_file_menu_holds_the_session_actions` keeps passing).
3. `static/app.js`:
   - Import `FILE_SHORTCUTS`, `isMac`, `matchShortcut`, `shortcutLabel`
     from `./shortcuts.js`.
   - `_buildActions()`: add
     `{ id: 'open-popout', label: 'Open 3D View in New Window', run: () => this._openPopout(), unavailable: () => (document.getElementById('popout-btn').disabled ? 'Open a dataset first' : '') }`
     (no `menu`, so the File menu does not change).
   - Right after `this._actions = this._buildActions();`, give each action
     its keys:
     ```js
     const mac = isMac();
     for (const s of FILE_SHORTCUTS) {
       const a = this._action(s.action);
       if (!a) continue;   // its plan is not built yet
       a.keys = shortcutLabel(s, mac);
       a.ariaKeys = `Alt+Shift+${s.code.replace(/^Key|^Digit/, '')}`;
     }
     ```
     then call a new `this._initFileShortcuts()`.
   - `_initFileShortcuts()`:
     ```js
     /** Alt+Shift file shortcuts from the one action list (ADR 0055). A
      *  key whose action does not exist yet does nothing; one that cannot
      *  run now says why. Keys typed in a text field are left alone. */
     _initFileShortcuts() {
       document.addEventListener('keydown', (e) => {
         if (e.defaultPrevented || e.repeat) return;
         if (document.querySelector('.warning-modal:not(.hidden), #fb-modal:not(.hidden), #unit-popover')) return;
         const inText = !!e.target.closest?.('input, textarea, select, [contenteditable]');
         const s = matchShortcut(e, { inText });
         const action = s && this._action(s.action);
         if (!action) return;
         e.preventDefault();
         const why = whyUnavailable(action);
         if (why) { this._setStatus(why, 'error'); return; }
         runAction(action);
       });
     }
     ```
     Check: `.warning-modal` also matches the dialogs built by
     `askDialog`/`askText`/`infoDialog`/`textDialog` (they have no `hidden`
     class), so no shortcut fires under them.
   - Update the comment above `_buildActions` ("shortcuts and the command
     palette read the same entries") to say the Alt+Shift file shortcuts do.

**Test.**
- `test_web_pure_helpers.py`: import `shortcuts.js` as `sc`; cases
  `"key_alt_shift_s": "sc.matchShortcut({code: 'KeyS', altKey: true, shiftKey: true})?.action"` → `'save-session'`;
  `"key_digit_0": "sc.matchShortcut({code: 'Digit0', key: ')', altKey: true, shiftKey: true})?.action"` → `'load-zero-model'`;
  `"key_needs_both": "[sc.matchShortcut({code: 'KeyD', altKey: true}), sc.matchShortcut({code: 'KeyD', shiftKey: true})]"` → `[null, null]`;
  `"key_ctrl_free": "[sc.matchShortcut({code: 'KeyS', ctrlKey: true}), sc.matchShortcut({code: 'KeyS', metaKey: true, altKey: true, shiftKey: true})]"` → `[null, null]`;
  `"key_in_text": "sc.matchShortcut({code: 'KeyD', altKey: true, shiftKey: true}, {inText: true})"` → `null`;
  `"key_mac_char": "sc.matchShortcut({code: 'KeyD', key: 'Î', altKey: true, shiftKey: true})?.action"` → `'load-dataset'`;
  `"key_cluster": "sc.matchShortcut({code: 'KeyC', altKey: true, shiftKey: true})?.action"` → `'connect-cluster'`;
  `"key_labels": "[sc.shortcutLabel(sc.FILE_SHORTCUTS[0]), sc.shortcutLabel(sc.FILE_SHORTCUTS[0], true), sc.shortcutLabel(sc.FILE_SHORTCUTS[5])]"`
  → `['Alt+Shift+S', '⌥⇧S', 'Alt+Shift+0']`;
  `"key_is_mac": "[sc.isMac({platform: 'MacIntel'}), sc.isMac({platform: 'Win32'}), sc.isMac({userAgentData: {platform: 'macOS'}})]"` → `[true, false, true]`.
- `test_web_runtime.py` `test_web_alt_shift_shortcuts_open_the_file_actions(ffast_web_server)`
  (copy the setup of `test_web_four_shortcuts_step_frames_search_and_keep_out_of_inputs`).
  Press keys with `page.keyboard.press("Alt+Shift+KeyD")` (checked to work,
  see Facts); click the page body first so focus is not in a text field.
  - `_preload_dataset`, `_open_loupe`.
  - `Alt+Shift+KeyD` → `#fb-modal` visible, `#fb-title` "Load Remote
    Dataset"; Escape.
  - `Alt+Shift+KeyS` → `#fb-title` "Save Session"; Escape.
  - `Alt+Shift+KeyL` → "Load Session"; Escape.
  - With `#fb-modal` open, `Alt+Shift+KeyS` does not change the title.
  - Focus `#sidebar-search`, press `Alt+Shift+KeyD` → `#fb-modal` stays
    hidden (keys in a text field are left alone).
  - `Control+KeyD` does not open `#fb-modal` (the old desktop key is not
    taken).
  - Open the File menu: the "Save Session…" item has a `data-keys`
    attribute matching `/^(Alt\+Shift\+S|⌥⇧S)$/` and `aria-keyshortcuts`
    "Alt+Shift+S"; its text is still "Save Session…".
  - `Alt+Shift+KeyN`: `async with page.context.expect_page()` around the
    press → a new page opens (as
    `test_web_popout_opens_independent_live_controller` does with the
    button).
  - Disconnected window (new page without `?port=`): `Alt+Shift+KeyS`
    leaves `#fb-modal` hidden and `#status` says "Connect to a server first".
- If plan 1 (`load-model`) or plan 4 (`connect-cluster`) is already built
  when you do this task, also check `Alt+Shift+KeyM` opens the Load Model
  browser, and that the File menu's "Connect to Cluster…" item shows
  `data-keys`. If not, skip these checks and say so in the report.

Run `-k "shortcut or file_menu or popout"` and the pure helpers.

**Commit:** `feat: Alt+Shift shortcuts for the file actions`.

---

## Not covered by this plan

- **Load Config at run time** (Decision 1): no task.
- A window that connects while a task already runs does not list that task
  (the server announces tasks only when they start).
- Renaming from the desktop client while connected to a server does not
  reach the server (desktop is being retired; not touched).

## When done

Report: the eight commit hashes, the tests you added, anything in this plan
that did not match the code and what you did about it, whether the
`TaskManager` cancel test (Task 6) was kept, whether the Task 8 checks for
Alt+Shift+M (plan 1) and Alt+Shift+C (plan 4) ran or were skipped, and any
test that was red (with its output). Do not push.
