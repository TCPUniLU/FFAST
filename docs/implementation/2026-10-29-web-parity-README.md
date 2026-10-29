# Web parity: what the browser client lacks, and the order to build it

A code-read audit on 2026-10-29 compared the browser (web) client with the Qt
desktop client. The web client is already ahead on analysis tabs and close on
the 3D view. It is behind on **loading data**, on a few workspace tools, and on
**connecting to a cluster**. Until those gaps close, `ffast` keeps opening the
desktop when PySide6 is installed.

The work is split into four plans. Each one is self-contained: it quotes the
code to change, names the tests, and makes one commit per task.

| # | Plan | Tasks | What the user gets |
|---|------|-------|--------------------|
| 1 | [2026-10-29-web-parity-1-loading.md](2026-10-29-web-parity-1-loading.md) | 9 | Loader types from the server; energy/force key picker; extra keys loaded as predictions; every Nth frame; `.npz` predictions; Load Model; Load Zero Model |
| 2 | [2026-10-29-web-parity-2-view-and-plots.md](2026-10-29-web-parity-2-view-and-plots.md) | 12 | Atom index labels; Info read-out names atoms and elements; alignment and centre of mass exclude each other; plot PNG/SVG/CSV and table CSV; hover crosshair; axis titles, text sizes and legend position saved in the tab; hover highlight with no tool |
| 3 | [2026-10-29-web-parity-3-workspace.md](2026-10-29-web-parity-3-workspace.md) | 8 | `--workdir`; file browser for session save/load and export; export format menu; rename; Info dialog; task list with Cancel; Ctrl/⌘ file shortcuts |
| 4 | [2026-10-29-web-parity-4-cluster.md](2026-10-29-web-parity-4-cluster.md) | 8 | Connect to Cluster from the web: profiles, SSH, job start, reconnect, end job. Contains an ADR draft |

## Order

1. **Plan 1 first.** It holds the gaps that block switching the default.
2. **Plan 2 and plan 3 next, one at a time.** They are independent of each
   other. Plan 3's shortcut task (task 8) uses the `load-model` and
   `load-zero-model` actions from plan 1; built without plan 1, those two keys
   just do nothing.
3. **Plan 4 last.** It is the largest and needs its ADR accepted first.

Run one plan at a time, in one working copy. Plans 1 and 3 both change
`static/remote_browser.js`, `static/app.js` and `static/index.html`; each plan
says to find code by searching for the quoted text, not by line number, so a
later plan still applies after an earlier one moved lines.

## Decisions

All open questions were decided by the user on 2026-10-29. Each plan lists
its own under "Decisions (confirmed by the user 2026-10-29)". The ones that
cut across plans:

- **What is saved where.** Things the user creates live on the server under
  `~/.ffast/` (tabs, tab order, user metrics, and now a plot's axis labels,
  text sizes and legend position, stored in the tab's file). How this screen
  looks stays in the browser (layout, colours, units, recent files).
- **No legend renaming inside plots.** Legend text is the dataset or
  prediction name; rename the object itself (plan 3).
- **Shortcuts are Alt+Shift+letter** (Option+Shift on a Mac), so they never
  clash with the browser: S save session, L load session, D load dataset,
  P load prediction, M load model, 0 zero model, N new 3D view, C connect to
  cluster.
- **Load Config… is not ported.** A project config is given when the server
  starts (`--config`).

Plan 4's design is ADR 0071 (accepted 2026-10-29), and the user approved
its cluster dialog sketch (Task 5) the same day. No gates remain.

## After all four plans

Switch `ffast` to open the web client by default and keep `ffast-qt` for the
desktop (`pyproject.toml` `[project.scripts]` and top-level `launcher.py`).
One commit, so it is easy to undo.

## Found while planning, not yet planned

- `ffast/core/loading_coordinator.py` treats any path containing `npz`
  (`if "npz" in path:`) as an `.npz` file, so an `.xyz` prediction inside a
  folder such as `npz_runs/` fails. One-line fix: check the file extension.
- The desktop's File ▸ Load Config… calls `env.loadConfig`, which no longer
  exists, so the item fails today.
- The web page does not notice a server connection that drops after it was
  made (`static/connection.js` clears `onclose`).
- A freshly loaded real or zero model may show no 3D force arrows until a plot
  has computed its forces (the scene reads cached forces only). Unverified.
