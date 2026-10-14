Status: Accepted — implemented (all 7 steps done 2026-10-14). Decided 2026-10-14 in a design interview.

# Users define new metrics while the app runs, as formulas, in a Metrics pane

The request: "introduction of new metrics in real time in a special window".

What FFAST does today:

- **Every metric exists from server start.** Built-in metrics, project Python
  modules and project `[[metrics.expr]]` formulas are all registered before the
  server checks the metric graph (`ffast/server.py`, the compile pass before
  `registry.freeze()`). Nothing adds, changes or removes a metric afterwards.
  ADR 0056 rule 11 left "compiling a new metric on a running server" as later
  work, and `_ExistingOnly` in `ffast/config/user_tabs.py` refuses a user tab
  that needs one.
- **The worker pool copies the metric table once**, when it starts
  (`ffast/metrics/pool.py`). A metric registered later is unknown to the
  workers.
- **Two result caches, one blind to formulas.** The inner metric cache is keyed
  by the formula text and the input values (`ffast/metrics/cache.py`), so an
  edited formula already gets new numbers there. The outer cache in the
  DataService is keyed by metric id + model + dataset only
  (`make_metric_cache_key` in `ffast/core/data_service.py`), so it would keep
  serving the old numbers.
- **`METRICS_UPDATED` is half built.** Both clients listen for it (the browser
  re-requests the metric list, Qt rebuilds its colour menu); nothing sends it.
- **The browser does not know a file's extra keys.** Dataset info sent to it
  leaves them out; only `ffast-cli dataset keys` lists them.

What other tools do is in `docs/research/live-metric-definition-precedents.md`.
In short: OVITO, ParaView, W&B, Tableau and Superset let users type a formula
over named fields, show the list of names beside the box, and save the formula
text (not the numbers). Tools that accept Python (ASE GUI, ParaView's
Programmable Filter, OVITO's script modifier) run it with no sandbox; OVITO had
to add a "safe mode" because opening someone's session file ran their code.

## Decision

Browser and server only. The Qt desktop and the command line do not load user
metrics (rule 11).

### What a user metric is

1. **A formula, never Python.** A user metric is an Expression metric (ADR
   0042): an `expr` string over named variables, each bound to a metric input.
   Python metrics stay files loaded at server start. A formula can only do
   arithmetic over named inputs, so anyone holding the server's access token can
   define one without being able to run code on the cluster account.
2. **Stored on the server machine, one TOML file per metric**, in
   `~/.ffast/metrics/`, in exactly the `[[metrics.expr]]` format. It survives
   restarts and every browser connected to that server sees it, as user tabs do
   (ADR 0056 rule 9). **Export to project** copies it into the project's
   `ffast.toml` unchanged, the same way user tabs are exported.
3. **The id is typed in full,** with `user.` pre-filled; `mylab.e_per_atom` is
   allowed. A taken id is refused. If a project or built-in metric later takes
   the same id, that one wins at start-up and the user metric is listed as
   "Hidden: the project defines `<id>`", to rename or delete. Export keeps the
   id, so tabs keep working.
4. **Only user metrics are editable.** Built-in and project metrics are shown
   read-only; a formula one offers **Duplicate**, which makes a user copy under a
   new id. There is no "replaces" for metrics, unlike tabs: an id must mean one
   calculation, because a metric is numbers, not layout.

### Changing the set of metrics on a running server

5. **Edit keeps the id.** Save validates, re-registers the metric, gives the
   worker pool the new metric table, clears the outer cache for that metric and
   for every metric built on it (found through the metric graph), and sends
   `METRICS_UPDATED`. Open plots and atom colourings recompute.
6. **Save carries the version it started from.** If the server's copy is newer,
   the pane says "Changed in another window" and offers **Reload** (drop my
   edits) or **Overwrite** (save mine anyway, one click, no second prompt). A
   browser with unsaved edits is told as soon as `METRICS_UPDATED` arrives.
7. **Delete is refused while anything uses the metric.** The pane lists the
   users: tab and panel, other metrics, and the main view's colouring. The same
   lookup gives the pane a "Used by" line for every metric.
8. **Bad values still fail the whole metric** (ADR 0042 Decision 7 unchanged).
   One NaN or infinity on a dataset makes the metric fail there; Try (rule 12)
   is what finds them before saving.

### The Metrics pane

9. **A side pane next to "Color by",** resizable, so plots and the 3D view stay
   visible while editing (the OVITO arrangement: pipeline beside viewport). It
   lists all metrics by origin (built-in, project, user) with the editor below.
10. **Inputs are found in a searchable list** for the selected dataset and
    prediction, grouped as Reference, Prediction, File keys and Metrics, with
    inputs present on only one side marked. Clicking one inserts a short name
    (`pred_E`) at the cursor and adds the `vars` row behind it; the name can be
    renamed. The formula box is plain text: typing offers the same list as
    suggestions, and a name with no input gets "`ref_E` is not an input" plus
    a one-click **Fix** when exactly one input matches. The server starts
    sending each dataset's extra file keys.
11. **Checked as you type, computed on demand.** Every change is validated by the
    server's `parse_and_validate` and shape check, which touch no data. The pane
    shows the error in red, or the result shape in grey ("per structure → can be
    plotted"). Nothing shared changes until Save.
12. **Try computes without saving,** on all frames of the selected dataset and
    prediction, in chunks of frames so it can report progress (`TASK_PROGRESS`)
    and stop cleanly on **Cancel**. Chunking gives the same answer because a
    formula is element-wise. It shows min / mean / max, the count of bad values
    and the first bad frames (clickable), and a small histogram. Nothing is
    registered; other browsers see nothing. Save computes as any metric does.
13. **One shortcut by result shape.** Per atom: **Colour atoms by this** sets the
    main view's colouring. Per structure: **Plot it** adds a panel, with the
    default panel kind for that shape, to the current tab, or to a "My metrics"
    user tab when the current one has no room. It is an ordinary panel in an
    ordinary user tab, edited like any other. Later saves update it live (rule
    5).

### Who loads user metrics

14. **Only the server.** The Qt desktop ignores them, as it ignores user tabs
    (ADR 0056 rule 17). `ffast-cli metrics run` and `metrics validate` ignore
    them, so a command gives the same numbers on every machine; a metric that
    must be reproducible is exported to the project first.

### Sessions

15. **A saved session carries the formulas of the user metrics it uses,**
    including the user metrics those are built on. On load, metrics this server
    lacks are offered: "This session uses 2 metrics this server doesn't have:
    Add them?". Yes saves them to `~/.ffast/metrics/`; No behaves as if the
    session carried nothing, and the missing metric is reported where it was
    used. Carrying formulas is safe because a formula cannot run code.
16. **A clash keeps both.** If the server has the same id with a different
    formula, the session's version is added as `<id>.2` (the next free number)
    and, inside this opened session, everything that used `<id>` uses `<id>.2`.
    The server's metric is untouched. An identical formula is not a clash and
    adds nothing.

### The formula language grows first

17. **Comparisons, `norm`, named parts and positions** are added to the
    Expression metric language, as a step before the pane. Bumping
    `_WHITELIST_VERSION` changes every Expression metric's cache identity.
    - `> < >= <= ==`, mainly for `where(...)`.
    - **Named parts**, the normal way: `x(F)`, `y(F)`, `z(F)` for a vector; once
      stress is loaded, `xx(S)`, `xy(S)` … `zz(S)` and `trace(S)` for a tensor.
      A name means the same part whatever layout the input arrives in.
    - **Positions**, for anything names do not cover: `F[2]`, `S[0,1]`. Only
      whole-number literals, only over the part axes (vector, 3×3, Voigt), never
      over atoms or frames; an out-of-range position is a check error.
    - **`norm(v)`** is the square root of the sum of squares over *all* part
      axes: a vector's length, a 3×3 tensor's Frobenius norm. So the force error
      per atom is `norm(pred_F - ref_F)`, and later the stress error per
      structure is `norm(pred_S - ref_S)`.
    - **Stress is held as 3×3 inside FFAST**, whatever the file or ASE gives.
      ASE reads the extxyz 3×3 stress into 6-number Voigt order
      (xx, yy, zz, yz, xz, xy), where the norm undercounts the off-diagonal
      parts and position 1 means yy, not xy.
    - **The position trap is documented for users** (`docs/usage.md`, in the
      same commit that adds positions): positions follow the layout FFAST
      holds, not the file's; a Voigt-shaped input's `S[1]` is yy while a 3×3
      input's `S[0,1]` is xy; prefer names.

    **Totals over atoms** (`max_atoms`, `mean_atoms`) are not added: they would
    reverse ADR 0042's "element-wise only" and duplicate the Transform layer.

## Alternatives rejected

- **Python typed into the pane.** Anyone with the access token could run any
  code on the cluster account; a typed-in function has no import path, so it
  cannot reach the worker pool without new machinery; errors show only when it
  runs. The external tools that allow it do not sandbox it either.
- **Formula pane plus a "reload Python files" button.** Two features, and the
  reload meets the same worker-snapshot problem. Can still be added later.
- **Metrics only in session files, or only in memory.** A user tab that plots the
  metric would break in another session or after a restart.
- **A new id on every edit.** Every tab using the metric would have to be
  re-pointed, and ids pile up. Duplicate covers "compare two versions".
- **Overriding project formula metrics, as tabs override.** The same id would
  mean different numbers on two machines.
- **Deleting anyway, or deleting everything that uses it.** The first hides
  breakage (a tab with a missing metric is silently dropped today); the second
  can remove a tab someone built.
- **A pop-up dialog, a second browser window, or an editor panel inside tabs.**
  A pop-up hides the plots you want to watch; a second window needs the
  cross-window link ADR 0051 removed; an editor panel mixes metrics with tab
  layout.
- **Saving automatically after a pause in typing.** Half-written formulas would
  be saved, caches cleared and every browser refreshed over and over.
- **Named parts only, or positions only.** Names alone leave no way to reach a
  part nobody named. Positions alone tie a formula's meaning to the input's
  layout (the Voigt trap in rule 17). Both are kept, names first, and the trap
  is documented.
- **Inputs written inside the formula** (`${prediction.info.pred_E}`). Long
  formulas and a syntax that differs from `ffast.toml`.
- **Last save wins.** A colleague's change to the numbers could vanish unseen.
- **A fixed `user.` prefix, or generated ids.** Exported metrics would carry
  `user.` into project files, or ids nobody can read in a tab file.
- **Creating a "My metrics" panel for every metric automatically.** Every test
  formula becomes clutter.
- **Try on a sample of frames.** It misses the rare bad frames, which are the
  main reason to try.
- **Bad values as gaps.** Every average and plot would have to handle gaps; if
  wanted, it is a decision for all metrics, not just user ones.
- **On a session clash, keeping the server's version or asking.** The first shows
  different numbers from when the session was saved; the second puts a question
  most users cannot answer into loading.

## What this gives up

- **The metric registry is no longer fixed after start-up.** Registering,
  replacing and removing a metric on a running server, and refreshing the
  workers' copy, are new paths through code that assumed a frozen table.
- **Two new on-disk formats people will keep:** the `~/.ffast/metrics/*.toml`
  files (low risk: the existing `[[metrics.expr]]` format) and the session
  section carrying formulas. Both are hard to take back once shared.
- **Laptop and cluster metrics differ.** User metrics live on the server's
  machine. Export, or a saved session, is the bridge.
- **The command line and the desktop do not see user metrics.** A metric must be
  exported before a script can use it.
- **Formulas stay limited.** No totals over atoms; the largest force error per
  structure still needs a Python metric or a Transform.
- **Two ways to name a part** (`z(F)` and `F[2]`), and positions can mislead
  when an input's layout is not the one the user assumes. Documentation, not
  the checker, guards against that.
- **New server surface:** validate, try (with progress and cancel), save, delete,
  export, the list of inputs, and the extra file keys in dataset info.

## Found while deciding

- **A tab with a missing metric disappears silently.** `UserTabs.layout()` drops
  it with only a log warning. It should say "tab X could not load: …". Its own
  fix, independent of this ADR.
- **Tab saves ignore their version stamp.** `revision` is sent to the browser but
  never checked on save. The rule 6 check could be applied to tabs too, as its
  own fix.

## Found while building

- **A hidden file cannot be edited.** A user file whose id a project or
  built-in metric holds is listed "Hidden"; saving it would have replaced the
  winner. It can be duplicated under a new id or deleted (rule 3).
- **The sidebar only exists in a tab with a 3D panel,** so the Metrics pane
  does too. A new "My metrics" tab made by **Plot it** therefore holds a linked
  3D panel beside the plot, and "no room" (rule 13) means: the tab on screen
  has no plot (it shows only the main view), or there is none.
- **Sessions store no use of a metric.** A session is `info.json` and the
  array cache (datasets and predictions); views, tabs and colourings are not in
  it. So "the user metrics it uses" is taken to be the ones something outside
  the metrics uses *now* (a tab's panel, an open view's colouring — the lookup
  behind delete-while-used, rule 7) plus the user metrics those are built on,
  written to `user_metrics.toml` in the session folder. Rule 16's "everything
  that used `<id>` uses `<id>.2`" has nothing stored to re-point today; the
  session's formulas that build on a renamed one are re-pointed. If sessions
  ever store views or tabs, they should be re-pointed there too.
- **The Colour By list is rebuilt from the catalog** each time, not appended
  to: a renamed or deleted user metric used to stay in it.
- **A transform of a user metric in a tab** (a density of it, say) still needs
  a metric compiled on the running server, which ADR 0056 rule 11 left as later
  work; "Plot it" uses a plain timeline for that reason.
- **Colouring usage covers every open connection's views,** and tab usage
  counts hidden tabs, so showing a hidden tab again never finds its metric gone.

## Build order

One commit per step, so each can be reverted alone:

1. Formula language: comparisons, `norm`, `x`/`y`/`z`, positions over part
   axes; whitelist version bump; `docs/usage.md` gains a formula-metrics
   section with the position trap.
2. Server user-metric store: load `~/.ffast/metrics/` at start after project
   metrics (project wins, "Hidden"); register, replace and remove on a running
   server; refresh the worker pool; clear the outer cache for a metric and its
   dependents; send `METRICS_UPDATED`; version check; refuse delete while used;
   export.
3. Server: the list of inputs for a dataset and prediction, including extra file
   keys.
4. Server: validate-as-you-type and Try in chunks with progress and cancel.
5. Browser Metrics pane: list by origin, editor, input list, suggestions and Fix,
   check text, Try, Save, Reload/Overwrite, Duplicate, Delete with "Used by",
   Export.
6. Browser shortcuts: Colour atoms by this, Plot it.
7. Sessions carry user-metric formulas; Add them?; clash as `<id>.2` with the
   session re-pointed.

Later, with loading stress (not part of this ADR): stress held as 3×3, tensor
names `xx`…`zz` and `trace`, and the usage docs' trap example updated to stress.
