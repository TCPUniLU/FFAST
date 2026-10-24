# Every warning and error is also a pop-up

Status: Accepted 2026-10-24.

## The problem

A **warning** tells the user that the screen does not show exactly what they
asked for, or that a number rests on something doubtful; the work still
happened. An **error** tells them that something they asked for did not
happen. The web client shows both in several ways, and some are easy to miss
or never reach the user at all.

Warnings today:

| Warning | Example | Shown today |
|---|---|---|
| A warning the server logs (`ffast.session.warning_relay`) | "atom-color: displacement unavailable — structures differ in size; falling back to element colors" | **Pop-up** in the bottom-right corner (`toasts.js`) |
| A metric result's warning (ADR 0064 amendment) | "1 bad value in rows left out by where= was ignored (frame 41)" | Small ⚠ beside the panel title; the text only on hover |
| Try in the Metrics pane | the same sentence | A line under the Try preview |
| Check of a formula | (none yet; `by=atom` on a dataset whose frames differ is the first, see rule 5) | — |

Errors today:

| Error | Example | Shown today |
|---|---|---|
| An error the server logs | "Metric ffast.x failed: MetricFailure(metric_id=…, traceback=…)" | **Red pop-up**, but the text is the program's, not a sentence |
| A metric that failed, in its panel | — | "No data for this selection.", as if the data were missing |
| The answer to an action in a pane | "x is not an input", "The id … is already taken", "Could not save" | Next to the button that was pressed |
| The status line at the bottom of the window | "Could not load ‹dataset›: …", "Export failed", "The tab could not be changed", "Select a dataset to colour" | One line, replaced by the next message ("Connected") |
| A metric a panel names that the server lacks | "Metric X is not on this server." | Inside the panel |
| A background task that failed (`TASK_FAILED`), a view command that failed (`VIEW_COMMAND`) | a session save that failed | **Browser console only**: the user never sees it |

A user who does not hover over a small ⚠ never reads a panel's warning. A
status-line error can be overwritten before it is read. A failed task is
silent. A failed metric looks like missing data.

## Decision

**1. Every warning and every error the user can act on is shown as a pop-up**,
through the one function that already draws them, `showWarning` in
`toasts.js`. Warnings are drawn yellow, errors red, as the function already
does by level. The pop-up keeps its present behaviour: it does not block the
page, closes by its × or by itself after 10 s (not while the pointer is on
it), the same text while open adds "×2" instead of a second pop-up, and at
most 4 are open at once.

**2. The pop-up is added, not a replacement.** The ⚠ beside a panel title, the
line under Try, and a panel's own message stay: they say *where* the problem
applies after the pop-up has gone. The pop-up says *that* there is one.

**3. A pop-up is a sentence that says what it is about.** It names the thing
affected, then the reason, in words, not program output:

- a result warning: "Force Difference · MAE · per frame: 1 bad value in rows
  left out by where= was ignored (frame 41)";
- a failed metric: "Could not compute Force Difference · MAE · per frame:
  by=atom needs structures with the same atoms in the same order; the
  structures of this dataset differ".

A server log warning or error keeps its text, as now; a logged metric failure
is written as the sentence above, not as the failure object.

**4. Once per change, not per redraw.** Panels re-receive their results on
every refresh (frame change, resize, another window's edit). A window shows a
result's warning or failure as a pop-up once per (metric, dataset,
prediction, text); it pops up again only when that text changes or the window
reloads. Server log messages keep their rule: the same text at most once a
minute.

**5. Check gives warnings.** `CHECK_METRIC` carries the dataset the pane has
open (`dataset_fp`, optional). The reply `METRIC_CHECKED` gains `warnings`, a
list of sentences. A warning does not stop Save: the formula may be right for
another dataset. The pane shows each warning under the formula and as a
pop-up. The first one:

- the formula uses `by=atom` and the open dataset's structures do not have
  the same atoms in the same order: "by=atom needs structures with the same
  atoms in the same order; this dataset's structures differ". The metric still
  refuses to run on such a dataset (ADR 0065), as now.

**6. A failed metric says so in its panel.** `METRIC_RESULT` with `ok` false
gains `error`: the reason in words (the failure's message, without the
traceback). A panel whose every series failed shows "Could not compute
‹metric name›: ‹reason›" instead of "No data for this selection."; "No data"
stays for a selection that has no data. A colouring that failed falls back to
element colours, as now, and pops up the same sentence.

**7. Status-line errors and console-only errors pop up.** Every call that puts
an error on the status line (`_setStatus(text, 'error')`) also shows it as a
red pop-up; the status line keeps it too. `TASK_FAILED` and a failed
`VIEW_COMMAND` show a red pop-up with their error, in addition to the console.

**8. What stays where it is.**

- An error that answers what the user just did in a pane ("x is not an
  input", "The id … is already taken", "Could not save") stays next to its
  button, without a pop-up: they are looking there.
- A question that waits for an answer (the "Only show force vectors" dialog,
  confirm delete) stays a dialog.
- A note that confirms an action ("Saved: the Metrics pane lists it.",
  "Connected") stays inline or on the status line.

**9. New warnings and errors follow the rule.** Code that warns the user does
it through a result's `warnings`, a reply's `warnings`, or the server log; an
error that is not the answer to a pane's own button goes through a reply's
`error`, the server log, or `_setStatus(…, 'error')`. Each of these reaches
`showWarning`. No new place draws a warning or error of its own.

## Considered options

- **Pop-up replaces the inline marks.** Fewer things on screen, but once the
  pop-up closes nothing says which panel is affected. Rejected.
- **Pop-ups for the errors next to a pane's button too.** Two messages for one
  click, the second one in the corner the user is not looking at. Rejected.
- **A list of past warnings and errors (a bell with a count) instead of
  pop-ups.** Keeps every message for later, but it is a new pane to build and
  to look at; the pop-up exists and was asked for. Can be added later on top
  of rule 1.
- **Refuse `by=atom` at Check.** Rejected: a formula is not tied to one
  dataset, and `mean(D, by=atom)` is right on a dataset whose structures have
  the same atoms.

## Consequences

- `analysis.js` (`_showWarnings`, the "No data" message), the colouring path,
  `panes/metrics.js` (Try and Check) and `app.js` (`_setStatus`,
  `TASK_FAILED`, `VIEW_COMMAND`) call `showWarning`.
- `CHECK_METRIC` gains `dataset_fp`, `METRIC_CHECKED` gains `warnings`,
  `METRIC_RESULT` gains `error`. All optional: an older window that sends no
  dataset gets no dataset warnings, and an older server sends no `error`, so
  its panel says "No data for this selection." as before: without a reason
  the window cannot tell a failure from missing data.
- Only the window that asked shows a failed metric's pop-up: the data service
  logs the sentence with `extra={"popup": False}`, which the warning relay
  keeps out of `SERVER_WARNING`; the traceback goes to the log at debug level.
- The data service logs a failed metric as the sentence of rule 3.
- `TASK_FAILED` carries only a task id, so its pop-up comes from the log: the
  task manager logs "‹task name› failed: ‹error›" and the warning relay sends
  it. A load whose window was told by `LOAD_FAILED` (it shows the status-line
  error and its pop-up) keeps its log lines out of the relay, so one failed
  load is one pop-up.
- The desktop client is left as it is: it is being retired.

## Steps

1. Result warnings: a pop-up once per (metric, dataset, prediction, text),
   naming the metric (rules 3, 4).
2. Failed metrics: `error` in `METRIC_RESULT`, the panel message, the pop-up,
   the logged sentence (rules 3, 4, 6).
3. Status-line errors, `TASK_FAILED` and `VIEW_COMMAND` failures pop up
   (rule 7).
4. Try warnings pop up as well as the line (rule 1).
5. Check warnings: `dataset_fp` in, `warnings` out; the `by=atom` warning
   (rule 5).
6. Docs: a usage guide section on warnings and errors.
