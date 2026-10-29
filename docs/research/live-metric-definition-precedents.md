# Live metric definition: what FFAST has, and what other tools do

Research note, 2026-10-14. Question: the user wants to define a **new metric while FFAST runs**,
in a dedicated window, and use it at once in plots and 3D atom colouring, with no restart.
Does FFAST already have any piece of this, what blocks it, and how do comparable tools do it?

Labels: **[Fact]** = checked in the cited file or page; **[Inference]** = my reasoning;
**[Recommendation]** = what I suggest.

Words used below:
- **Registry** — the server's in-memory table of metrics (`id → schema + function`).
- **Freeze** — the startup check that validates every metric's inputs, shapes and dependency order.
- **Worker pool** — a separate Python process the server runs metrics in, so a slow or crashing
  metric cannot hang the server.
- **Pickle** — Python's way of turning an object into bytes to send it to another process. A plain
  function pickles only as "a name to import", so a function typed into a text box cannot be
  pickled to another process.
- **Expression metric** — FFAST's existing one-line formula metric (`[[metrics.expr]]`, ADR 0042).

## 1. Short answer

- **FFAST has no runtime path today.** Every custom metric (Python, expression, dataset field,
  transform) is registered once at server start, before the freeze. There is no editor, console,
  "add metric" dialog, protocol event to add a metric, or reload. [Fact, §2]
- **The blockers are smaller than they look.** The registry itself does not refuse late
  additions. What actually breaks is: (a) the worker pool holds a copy of the registry made
  when it started; (b) the result cache is keyed by metric id only, so editing a metric under
  the same id would show stale numbers; (c) the browser receives the metric list only on
  connect; (d) re-registering an existing id is refused. [Fact, §3]
- **The expression metric is the natural engine.** It already parses a formula from a string,
  rejects anything outside a small safe whitelist with a clear message, needs no Python from the
  user, pickles safely to the worker pool, and changes its cache identity when the formula
  changes. [Fact, §4]
- **Outside tools converge on one pattern:** a safe formula language over named columns with
  the variable list shown next to the input, validated on apply, saved as text in the
  session/state file and recomputed on load; full Python is a separate "expert" option that no
  tool sandboxes. OVITO's Compute property is the closest match. [Fact + Inference, §5–6]
- **One gap to close first:** OVITO and ASE let users compare values and reach vector parts
  (`Velocity.X`, `F[n,0]`). FFAST's formulas cannot, so per-atom force length `|F|` cannot be
  written. Add comparisons and `norm` before building the window. [Fact + Recommendation, §6]

## 2. Part A — is there any runtime metric path in FFAST?

| Searched for | Result | Source |
|---|---|---|
| Server start order | Built-ins → project Python modules → declarative metrics (fields, expressions, tab transforms) → user-tab transforms → freeze. All before any client connects. | [Fact] `server.py:560-579` |
| Freeze failure | Server refuses to start on unknown ref, cycle or bad shape. | [Fact] `server.py:510-537` |
| Add/edit metric protocol event | None. The client→server events include `REQUEST_METRIC_CATALOG`, `SAVE_TAB` etc., but nothing that registers a metric. | [Fact] `ffast/protocol/control.py:38-70`, route table `ffast/session/server_session.py:166-173` |
| User-built tabs (ADR 0056) | Users build panels in the browser, but only from metrics the server already has. ADR 0056 rule 11: "compiling a new transform on a running server is later work." The tab store says the same: "on a running server it [the registry] is fixed". | [Fact] `docs/adr/0056-browser-tabs-mix-2d-and-3d-and-users-build-them.md:84-89`; `ffast/config/user_tabs.py:202-205`; and `_ExistingOnly` (`:366-369`) refuses new metrics because "a running server's metric graph and worker pool are already set up, so a metric added now could not be computed" |
| `METRICS_UPDATED` event | Exists in the protocol and both clients listen to it: the browser re-asks for the metric list, Qt Loupe rebuilds its colour menu. **Nothing on the server ever sends it.** Its comment points to a `LOAD_CONFIG` event that does not exist anywhere in the repo or its history. | [Fact] `ffast/protocol/control.py:103`, `ffast/protocol/rpc.py:64-65`, `ffast/renderers/web/static/app.js:1221`, `UI/loupe/window.py:87,293-296`; `git log -S LOAD_CONFIG` finds only the comment's own commit (0955549) |
| Console / REPL | Only `Environment.startInteract`, a `code.interact` debugging helper with no callers. | [Fact] `ffast/core/environment.py:459-463` |
| `eval`/`exec` of user text | None, except `signature.py` evaluating type annotations from the metric author's own module. | [Fact] `ffast/metrics/signature.py:102` |
| Qt legacy | No metric editor or expression dialog in `UI/` or `modules/`. | [Fact] code search for "custom metric", "expression", "console", `eval(`, `exec(`, `reload(` |
| Docs | ADR 0042 names "a later metric **wizard** (Grafana-style visual builder)" that should emit `[[metrics.expr]]` rather than a new mechanism. No PRD or issue plans live definition. | [Fact] `docs/adr/0042-expression-metrics.md:42-43,157-158`; search of `docs/prd`, `docs/issues` |

## 3. Part A — what blocks a runtime metric

| Blocker | What happens today | Source | Size |
|---|---|---|---|
| **Worker pool snapshot** | The server runs metrics in a `WorkerProcessExecutor`. It pickles the whole registry **once, when it is created**, and each new worker process unpickles that copy. A metric added later exists in the parent (so planning works) but the worker answers "not registered". | [Fact] `ffast/metrics/pool.py:246` (pickle once), `:140` (worker unpickles), `:165` (`registry.get` in worker), `:295` (plan built from parent registry); server uses the pool: `ffast/core/data_service.py:241-244` | Small: re-pickle and recycle the worker after a change (workers already recycle every N tasks, `pool.py:12,181-183`) [Inference] |
| **Python typed into a window cannot reach the worker** | Pickle sends a function as "import this name". A function created with `exec` from a text box has no importable name, so it fails to pickle — the same class of bug as the earlier "3D colouring fell back to element colours" incident. Expression metrics avoid this by storing the formula as data in a module-level class `_ExprFn`. | [Fact] `ffast/metrics/expr.py:45-47,239-252`; ADR 0042 Consequences `docs/adr/0042-expression-metrics.md:133-138` | Large for Python, none for expressions |
| **Duplicate id refused** | `registry.metric` raises if the id exists; `compile_expr_metric` silently returns the old id if it exists ("idempotent"). So "edit and re-apply" would keep the old formula. There is no remove/replace. | [Fact] `ffast/metrics/registry.py:64-65`; `ffast/metrics/expr.py:322-323`; `ffast/metrics/fields.py:53` | Small: add `replace`/`unregister` |
| **Result cache keyed by id, not by formula** | The server's main result cache key is `metric_id[__p<params>]__model__dataset`. It has no implementation hash. Editing a formula under the same id would return the old cached numbers. (The worker pool's own inner cache *does* include the formula, via `implementation_source`.) | [Fact] `ffast/core/data_service.py:196-217`; `ffast/session/server_session.py:1125-1127` (cache checked first); inner cache `ffast/metrics/cache.py:17-26,47-63`, `ffast/metrics/expr.py:294-296` | Small: give each edit a new id (e.g. a version suffix) or purge that id's keys |
| **Browser learns metrics only on connect** | The metric list (`METRIC_CATALOG`) is sent during connect replay, per connection. A new metric would not appear in Colour-by or the panel builder until reconnect. | [Fact] `ffast/session/server_session.py:382-400`; browser handler `app.js:2056-2068` | Tiny: broadcast the unused `METRICS_UPDATED` (§2) |
| **Freeze is startup-only** | Freeze validation runs once; the dependency graph is "not consulted at run time". The registry does **not** lock after freeze — late registration is not blocked by code, only by convention. Cycle and ref checks would need re-running for a new metric. | [Fact] `ffast/metrics/graph.py:1-8`; no frozen check in `ffast/metrics/registry.py:43-68` | Small: run the same checks for the new entry |
| **No variable list for a picker** | Dataset metadata sent to the browser carries `name, n, has_forces, is_sub, variable, elements, offsets` — not the file's extra info/array keys. A window that lists available variables would need them. `ffast-cli dataset keys` already computes exactly this list, offline. | [Fact] `ffast/loaders/dataset.py:231`; `ffast/cli/main.py:21-58` | Small–medium |
| **Executing user code on a server / cluster** | The server can run on a cluster node and listen on all interfaces; any client with the token is "controlling". Project Python modules are "trusted" only in the sense of "listed in the config"; content-hash approval is not built. Accepting Python over the wire would let any controlling client run code as the server's user. | [Fact] `server.py:540-550`; `ffast/session/registry.py:22-34`; `server.py:376-379`; CONTEXT.md:478-479 | Avoided entirely if the window accepts only expressions [Inference] |
| **Concurrency** | Metrics compute on a 2-thread pool while handlers run on the event loop; the registry is a plain dict. Adding while a catalog is being built or a plan is running is unguarded. | [Fact] `ffast/session/server_session.py:84-90,1134-1139`; `ffast/metrics/registry.py:14` | Small: a lock, or swap in a new dict [Inference] |

## 4. Part A — pieces that can be reused

| Piece | Why it fits | Source |
|---|---|---|
| `parse_and_validate(expr)` | Takes a plain string, uses Python's own parser in "expression" mode, and walks the tree allowing only `+ - * / **`, unary minus, numbers, names and calls to 10 whitelisted element-wise functions. Everything else is rejected with a precise, user-readable message (e.g. "function 'sum' is not allowed (permitted: …)"). No `eval()`. Ideal for live red-text feedback as the user types. | [Fact] `ffast/metrics/expr.py:63-86,112-186` |
| `compile_expr_metric(id, expr, vars, label, unit, registry)` | One call: checks namespaced id, unknown names, each variable's ref, and that all non-scalar variables share one shape; infers the output shape (so the UI knows if it is colourable per-atom or plottable per-structure); registers it. Callable at any time, not only from TOML. | [Fact] `ffast/metrics/expr.py:305-365` |
| Allowed variables | A variable may bind: a raw ref (`reference/prediction.energies`, `.forces`, `reference.positions`, `reference.masses`), any dataset field `{reference,prediction}.{info,atoms}.<key>`, the reserved `n_atoms`, or **any registered metric id** (so new metrics can build on old ones). | [Fact] `ffast/metrics/expr.py:99-106,210-230`; `ffast/metrics/inputs.py:17-74` |
| Picklable evaluator `_ExprFn` | Formula and variable map are data, so the worker pool can receive it. | [Fact] `ffast/metrics/expr.py:239-299` |
| Formula-aware identity | `implementation_source()` = formula + variable map + whitelist version. | [Fact] `ffast/metrics/expr.py:294-296` |
| Non-finite guard | Division by zero / `log` of negative raises a named Metric Failure instead of drawing `nan`. | [Fact] `ffast/metrics/expr.py:283-291` |
| `ExprMetricConfig` model | Same schema the window can send over the wire and save back to TOML. | [Fact] `ffast/config/models.py:48-77` |
| Fail-soft compile | `compile_project_metrics` already collects per-entry errors instead of aborting — the right shape for a UI error list. | [Fact] `ffast/config/tabs.py:147-193` |
| User tab store (ADR 0056) | Proven pattern for "the browser edits, the server saves one TOML file per item in `~/.ffast/tabs/`, broadcasts the new layout, and an item can be exported to a project's `ffast.toml`". A `~/.ffast/metrics/` twin fits. | [Fact] `ffast/config/user_tabs.py:1-17`; handlers `ffast/session/server_session.py:1185-1215` |
| `METRICS_UPDATED` | Already wired in both clients to refresh the metric list. Only a sender is missing. | [Fact] §2 |
| `ffast-cli metrics validate` | Headless strict check; the same compile + freeze sequence the window would run per entry. | [Fact] `ffast/cli/main.py:353-400` |

Limits of the expression language that a user will hit quickly [Fact, `ffast/metrics/expr.py`]:
- No comparisons (`>`, `<`) at `:138-142`, so `where(...)` is listed but cannot take a condition
  built in the formula.
- No subscripts (`:182-185`), so a force array `(N_atoms, 3)` cannot be reduced to its length
  `|F|` inside the formula.
- No reducers (mean, sum) by design: reductions belong to transform metrics (ADR 0042 Decision 1).

[Inference] For a live window these limits matter more than for a config file, because the user
will try things interactively. Adding comparisons and a `norm` (vector length) function would be
cheap and stays inside the same safety model; it bumps `_WHITELIST_VERSION`.

## 5. Part B — comparable tools

Checked against official docs or source code only. Where a claim rests on something weaker, the
row says so. "Sandbox" below means a fence that stops user code from touching files, network or
the rest of the program; none of the Python options found has one.

### 5.1 Tool by tool

**OVITO — Compute property modifier** (closest match to FFAST's need)
- Entry: a formula, one per output component, in a "C-like" language, e.g.
  `sqrt(Velocity.X^2+Velocity.Y^2+Velocity.Z^2)`; it has comparisons and a ternary
  (`(ReducedPosition.Z > 0.5) ? 0.7 : 0.0`) and functions such as `abs`, `sqrt`, `exp`, `ln`,
  `min`, `max`. Vector parts are reached by name (`Velocity.X`). Custom vector outputs since
  3.12.1. [Fact] https://docs.ovito.org/reference/pipelines/modifiers/compute_property.html
- Variables: every existing per-particle (or per-bond) property, plus global values such as box
  size and animation time. Discovery: "A list of available input variables is provided in the
  modifier's user interface." Property names are cleaned up (spaces dropped, other bad
  characters → `_`). [Fact] same page
- Neighbour mode: result = F(i) + Σⱼ G(i,j) over neighbours in a cutoff or bonded, with `@` for
  the central particle and a `Distance` variable. [Fact] same page
- Preview: the pipeline "automatically recalculates the output and updates the visualization in
  real time". [Fact] https://www.ovito.org/docs/current/usage/pipeline.html
- Errors: the sister modifier Expression selection (same language) reports a parser error for an
  ambiguous name. [Fact] https://docs.ovito.org/reference/pipelines/modifiers/expression_select.html.
  Live as-you-type error text is not described. [Not verified]
- Persistence: the whole pipeline, formula included, is saved in the `.ovito` session state
  file and recomputed on load. [Fact] https://docs.ovito.org/usage/miscellaneous.html
- Granularity: per-particle or per-bond; numeric only (strings need the Python modifier).
  [Fact] compute_property page
- Safety: a closed formula language; no user code runs. [Inference]

**OVITO — Python script modifier** (the "expert" escape hatch)
- Entry: a `modify(frame, data)` function typed into a built-in editor that starts with example
  code; it takes effect on "Commit and run script"; `print()` goes to a log window. It can also be
  a `.py` file or a pip-installed extension found at restart. [Fact]
  https://docs.ovito.org/reference/pipelines/modifiers/python_script.html
- Persistence: embedded code "may be lost when you close OVITO Pro – unless you save them as part
  of a session state file or a modifier template". [Fact] same page
- Safety: no sandbox is described. OVITO 3.11.0 added "Environment variable `OVITO_SAFE_MODE=1`
  can be set to effectively block execution of Python scripts embedded in .ovito session state
  files from untrusted sources". So OVITO treats a session file carrying Python as a risk.
  [Fact] https://docs.ovito.org/new_features.html (3.11.0 entry)

**ASE GUI — Graphs window**
- Entry: a Python expression; a comma-separated list gives several curves, e.g. `i, e - E[-1]`.
  [Fact] https://gitlab.com/ase/ase/-/raw/master/ase/gui/graphs.py (help text)
- Variables, listed in the window's help text: `e`, `epot`, `ekin`, `fmax`, `fave`, `R[n,0-2]`,
  `F[n,0-2]`, `V[n,0-2]`, `M[n]`, `A[0-2,0-2]`, `i`, `E[i]`, `s`, `T`, and functions
  `d(n1,n2)`, `a(n1,n2,n3)`, `dih(n1,n2,n3,n4)`. [Fact] same file
- Evaluation: the window passes the text to `images.graph(expr)`, which compiles it and runs
  Python `eval` once per image with a name table that includes the full Python built-ins — so it
  is unrestricted Python. [Fact, reported by a parallel check of
  https://gitlab.com/ase/ase/-/raw/master/ase/gui/images.py; I confirmed only the `graphs.py` half]
- Preview / errors: plot on button click; no live preview. [Fact, same report]
- Persistence: the expression is not saved; "Save" writes the numbers with the expression as the
  file header (`np.savetxt(filename, data.T, header=expr)`). [Fact] graphs.py
- Granularity: one value per frame (atoms are reached by index, e.g. `F[3,0]`). [Fact]

**ParaView — Calculator, Python Calculator, Programmable Filter**
- Calculator: formula language with calculator-style buttons; drop-downs list the available
  scalar and vector arrays; works per point or per cell; a "Replace Invalid Results" option.
  [Fact] https://docs.paraview.org/en/latest/UsersGuide/filteringData.html §6.9
- Python Calculator: one NumPy expression; variables are the array names, plus
  `inputs[i].PointData['x']`, `t_value`, `t_index`; invalid names are cleaned up; runs on Apply.
  [Fact] https://www.paraview.org/paraview-docs/v5.13.0/python/paraview.simple.PythonCalculator.html
- Programmable Filter: full Python script, run where the data lives (the server side of
  ParaView's client/server mode). [Fact]
  https://docs.paraview.org/en/latest/ReferenceManual/pythonProgrammableFilter.html
- Persistence: the `.pvsm` state file saves every pipeline module with its properties, so the
  formula or script text. [Fact] https://docs.paraview.org/en/latest/UsersGuide/savingResults.html §9.4
- Safety: no sandbox documented. [Not found]

**ChimeraX — `setattr` and attribute files**
- Entry: values, not formulas: `setattr spec level attr value create true type float`, or many
  values at once from an attribute file opened with `open`. [Fact]
  https://www.cgl.ucsf.edu/chimerax/docs/user/commands/setattr.html,
  https://www.cgl.ucsf.edu/chimerax/docs/user/formats/defattr.html
- Use: a new attribute works at once in `color byattribute` and in selections like
  `select ::X>2.0`; "Attributes are saved in sessions". [Fact]
  https://www.cgl.ucsf.edu/chimerax/docs/user/attributes.html
- Granularity: per atom, residue or model. [Fact] setattr page

**Apache Superset — metrics**
- Entry: in the chart editor, "Simple" (pick column + aggregate) or "Custom SQL" (an SQL
  aggregate expression such as `SUM(weight*observations)/SUM(weight)`). [Fact]
  https://superset.apache.org/developer-docs/api/schemas/chartdataadhocmetricschema/
- Saved metrics live on the dataset (aggregates), separate from calculated columns (row-level,
  no aggregates); both can be marked certified. [Fact]
  https://superset.apache.org/user-docs/using-superset/creating-your-first-dashboard/
- Runs in the database, i.e. server-side. Errors appear when the query runs. [Inference]

**Grafana**
- "Add field from calculation": click-to-build, no free text: choose a mode (Reduce row, Binary,
  Unary, Cumulative, Window, Row index), pick fields from lists, set an alias; a debug (bug icon)
  view shows input and output. [Fact]
  https://grafana.com/docs/grafana/latest/panels-visualizations/query-transform-data/transform-data/
- Math expressions (`$A + $B`) run on the server: "expressions can operate without a browser
  session". [Fact]
  https://grafana.com/docs/grafana/latest/panels-visualizations/query-transform-data/expression-queries/

**Weights & Biases — line plot expressions**
- Entry: an Expressions tab with X and Y formulas, e.g. `${val/accuracy}`, `${config:...}`, with
  JavaScript Math functions, "evaluated at each step that you log"; Apply button. [Fact]
  https://docs.coreweave.com/models/app/features/panels/line-plot/reference
- Persistence: stored as `custom_expressions` in the panel config. [Fact]
  https://docs.wandb.ai/models/ref/wandb_workspaces/reports

**Briefly** (lower priority, not checked in depth)
- PyMOL `alter`/`iterate`: a Python expression run per atom with names like `name`, `resn`,
  `b`, `q`; no safety note. [Fact] https://pymolwiki.org/index.php/Alter
- VMD: `user`…`user4` per-atom fields filled from Tcl and usable in selections and colouring.
  [Weak: mailing-list source only] https://tcbg.illinois.edu/Research/vmd/mailing_list/vmd-l/25976.html
- TensorBoard Custom Scalars: arranges already-logged tags; no in-app metric definition. [Fact]
  https://raw.githubusercontent.com/tensorflow/tensorboard/master/tensorboard/plugins/custom_scalar/README.md

### 5.2 Comparison

| Tool | Entry | Variables shown | Preview | Saved as | Runs user code? | Granularity |
|---|---|---|---|---|---|---|
| OVITO Compute property | C-like formula, comparisons, ternary, `.X` parts | List in the panel | Live pipeline update | Formula text in `.ovito` session | No | Per particle / bond |
| OVITO Python modifier | Python `modify()` in editor | Python API | On "Commit and run" | Code in session or template | Yes; `OVITO_SAFE_MODE` blocks it in untrusted sessions | Any |
| ASE GUI Graphs | Python expression, comma list | Help text | On click | Not saved (numbers only) | Yes, unrestricted `eval` | Per frame |
| ParaView Calculator | Formula + buttons | Array drop-downs | On Apply | Text in `.pvsm` state | No | Per point / cell |
| ParaView Python Calc / Programmable | NumPy expr / Python script | Array names | On Apply | Text in `.pvsm` state | Yes, no sandbox documented | Per point / cell |
| ChimeraX | Values (`setattr`, file) | — | Immediate | Values in session | No | Per atom / residue / model |
| Superset | Pick or SQL aggregate | Column picker | On query | Text on chart or dataset | SQL in the database | Row vs aggregate (formal split) |
| Grafana | Click-to-build; Math `$A+$B` | Field lists | Debug view | Panel / query config | No (Math is server-side) | Per row / series |
| W&B | `${metric}` + JS Math | Metric names | On Apply | `custom_expressions` in panel config | Formula only | Per logged step |

### 5.3 Patterns that recur

1. **Safe formula over named fields, with the variable list shown next to the input.** OVITO
   Compute property, ParaView Calculator, W&B, Grafana Math (also Tableau calculated fields,
   https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_create.htm,
   and Power BI measures, https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-measures).
   [Fact]
2. **Full Python as a separate, trusted "expert" option, never sandboxed.** OVITO Python
   modifier, ParaView Python Calculator / Programmable Filter, ASE, PyMOL. Only OVITO guards
   against Python arriving inside a session file. [Fact]
3. **The definition is saved as text and recomputed on load,** not the numbers. OVITO,
   ParaView, W&B, Superset. (ChimeraX saves values; ASE saves nothing.) [Fact]
4. **User text runs on the server where the data lives.** Superset (database), Grafana Math,
   ParaView Programmable Filter in client/server mode. This is FFAST's shape: browser client,
   remote Python server. [Fact + Inference]
5. **Click-to-build instead of free text.** Grafana "Add field from calculation", Superset
   "Simple". [Fact]
6. **Granularity is explicit.** OVITO, ChimeraX, PyMOL, ParaView act per atom/point; ASE and W&B
   per frame/step; only Superset formally splits row-level (calculated column) from aggregate
   (metric). [Fact] This matches FFAST's split between expression metrics (element-wise) and
   transform metrics (reductions). [Inference]

What no checked source documents: error text shown live while the user types. [Not found]

## 6. Fit for FFAST

### Facts that bear on the choice
- FFAST's expression metric already gives what pattern 1 needs: string in, whitelist check, clear
  error text, picklable to the worker pool, cache identity that changes with the formula. (§4)
- The FFAST server may run on a cluster and accepts any client with the token as controller. (§3)
- In outside tools, Python entry is always unsandboxed, and the one tool that addresses safety
  (OVITO) does so by letting users block Python in session files. (§5.1)
- OVITO and ASE both let users compare values (`>`, ternary) and reach vector parts (`Velocity.X`,
  `F[n,0]`). FFAST's expression language allows neither (§4), so `|F|` per atom cannot be written.

### Assumptions
- FFAST users are ML force-field researchers who will first try things like "force error length
  per atom", "energy error per atom", or "flag atoms where error > 0.1". [Assumption]
- A remote server with Python entry would be reachable by anyone with the token. [Inference, §3]

### The 2–3 best-fitting patterns
1. **OVITO Compute property: safe formula, variable list beside the box, live result in the
   view, formula saved as text.** Fits the existing expression metric directly. The inferred
   shape decides where it can be used (per-atom → colour atoms; per-structure → plot), like
   OVITO's per-particle output feeding colouring at once.
2. **Save the definition, not the numbers, and recompute on load** (OVITO, ParaView, W&B,
   Superset). Fits the ADR 0056 user-tab store: one TOML per metric in a server-side folder,
   exportable to `ffast.toml`.
3. **Run on the server, return results only** (Superset, Grafana Math, ParaView Programmable).
   The browser sends formula text; the server validates and computes. Matches FFAST's split.

Superset's row-level vs aggregate split is a useful naming hint (element-wise formula vs
transform reduction) but not a separate pattern to build.

### Recommendations
- [Recommendation] Keep Part A's plan: build the window on expression metrics, not on typed-in
  Python. No outside tool contradicts this. Every tool that accepts Python treats it as
  trusted local code, and FFAST's server is not necessarily local.
- [Recommendation] Show the variable list next to the input (refs, dataset fields, `n_atoms`,
  existing metric ids), as OVITO and ParaView do. This needs the dataset key list sent to the
  browser (§3, "No variable list").
- [Recommendation] Use `parse_and_validate` for red text while typing. No checked tool documents
  this, so it would be better than the precedents, not a copy of them.
- [Recommendation] Widen the expression language before shipping the window: add comparisons
  (so `where(err > 0.1, 1, 0)` works) and a per-atom vector length such as `norm(F)`. OVITO and
  ASE both offer the equivalent, and without it the most obvious per-atom metric, `|F|`, is not
  expressible. This sharpens Part A's note in §4 from "cheap" to "needed for a useful first
  version". It stays inside the same safety model and bumps `_WHITELIST_VERSION`.
- [Recommendation] Keep Python metrics as files loaded at start. If an in-app Python editor is
  ever wanted, copy OVITO's two guards: run only on an explicit "commit", and never execute
  Python found inside a shared or loaded session file without the user's consent.
