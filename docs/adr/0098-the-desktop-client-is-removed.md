Status: Proposed 2026-11-13. Built on the local branch `remove-qt`; merged to
`main` only after a hand test of the browser's cluster connect on a real
cluster. Ends the coexistence that [ADR 0045](0045-web-client-replaces-qt.md)
and [ADR 0090](0090-ffast-starts-the-browser-client.md) left open.

# The desktop client is removed

## The problem

Since ADR 0090, `ffast` starts the browser client and `ffast-qt` starts the
desktop (Qt) one. Nobody chose the desktop any more, but it still cost:

- **Two clients for every change.** A protocol or metric change had to keep
  `UI/`, `client/`, `modules/` and the vispy renderer working, or break them
  silently: most of their tests skip without PySide6.
- **Core code shaped by the desktop.** `ffast/core` carried a client-side
  connection layer (`ConnectionManager`, a remote dataset proxy, an inbound
  message router) that only the desktop used, and plugin discovery scanned
  `modules/` on every server start.
- **Packaging.** A `gui` extra, the `ffast-qt` script, `style.qss` and
  `resources.rcc` shipped for a client nobody started.

## What was checked first

Before anything was deleted, every desktop feature was compared with the
browser. Three gaps were built in the browser first: log-scale axes, a typed
axis range and flip, and the size of an exported picture. The browser also
took over the two things the desktop's connection layer did better
([ADR 0097](0097-every-reply-names-the-request-it-answers.md)): each reply is
matched to its own request, and a dropped connection is noticed and retried.

Seven small gaps were left for later. All seven were built in the browser
after the deletion (2026-11-13):

| Feature | What the desktop did | Status |
|---------|----------------------|--------|
| Grid on/off | A plot's grid lines could be hidden. | built (a931b517): a Grid column in Axes…, per axis |
| Link X axes | Zooming one plot's X axis zoomed the others in the tab. | built (a0657b2b): "Link X axes" in the tab menu, for the tab's timelines |
| Unit in a TOML label | A unit written in a TOML label showed in the axis label. | built (b1731853); `forcesUnit` typo fixed (3ecb295e) |
| Rename the colour-bar title | The colour bar's title could be edited. | built (c0c906c4): right-click ▸ Rename…, one name per colouring, kept in the browser |
| SSH key Browse button | The cluster form picked the key file with a file dialog. | built (98bc6fa2): the keys in `~/.ssh` offered, a typed path checked |
| Ctrl+Shift+L | Shortcut for "Connect to Local Server…". | built (16c7712a): Alt+Shift+O for Connect to Server… |
| Export from a sidebar row | A dataset exported from its sidebar row. | built (94b82711): Export… in a dataset or prediction row's ⋯ menu |

## The decision

Delete everything Qt:

- The client code: `UI/`, `client/`, `modules/`, `main.py`, `utils.py`, the
  `theme/` icons, `style.qss`, `resources.rcc`, `ffast/renderers/vispy`.
- The desktop's client-side connection code: `ConnectionManager`, the remote
  dataset proxy, the inbound router, and the request machinery in
  `cluster/connection.py`. What the browser's cluster launcher uses stays:
  submitting the job, the SSH tunnel, the hello check and the Session Records.
- The tests that only covered deleted code. Test files that mixed desktop and
  core tests keep the core ones.
- The `gui` extra, the `ffast-qt` script, and the Qt parts of CI, pixi, the
  conda recipe and `uv.lock`.

What moved instead of going:

- The seven force and acceleration colourings that the desktop's force-error
  pane registered are now made when the built-in metrics load
  (`ffast/metrics/builtin/__init__.py`), so the browser still lists them.
- Plugin discovery has two roots: the bundled plugins and entry points.
- The server compiles the bundled tabs before it records which metrics are
  built-in. Before, the `modules/` scan did that, and it also compiled the
  project's metrics there, so they were wrongly labelled built-in.
- `ClientCapabilities.renderer` accepts `webgl` and `headless`; `webgl` is the
  default. A client that says `vispy` is refused.

What stays on purpose: `Environment(headless=False)`. Nothing uses it today,
but it costs little and may be needed again; a test keeps it building.

## What it gives up

- The seven features above, until the browser has them.
- A client that runs without a web browser.
- The way back is the local tag `last-qt` (the last commit with the desktop
  client). It is not pushed.

## Superseded

These records were about the desktop client only. Each now carries a
"Superseded by ADR 0098" line and is otherwise left as written: 0002, 0003,
0009, 0014, 0017, 0027, 0029, 0032, 0037, 0039, 0040. ADR 0004, 0045, 0048
and 0090 keep their server and browser parts; their desktop parts are
superseded.
