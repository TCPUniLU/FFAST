# Contributing

Thanks for looking. Bug reports and small fixes are the most useful things you
can send. For anything larger, open an issue first so we can agree on the shape
before you write code.

## Setting up

FFAST pins Python 3.11 (see `requires-python` in `pyproject.toml`). Anything
newer breaks at least one of the ML backends.

```bash
git clone https://github.com/TCPUniLU/FFAST.git
cd FFAST
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[gui,dev]"      # both clients, server, CLI, test tools
```

Use the `gui` extra unless you specifically want to work without a Qt/OpenGL
stack; part of the test suite exercises the desktop client and needs PySide6.
`pip install -e ".[dev]"` gives you the server, CLI and browser client alone.

Put the virtualenv somewhere that is not synced by iCloud or Dropbox. A synced
directory corrupts the Qt plugin layout and makes `.pyc` files disappear
mid-run.

## Running the tests

```bash
pytest -m "not integration"   # ~1200 tests, about 20 seconds
pytest                        # adds tests that spawn a real ffast-server
```

Qt-dependent tests need a display, or `QT_QPA_PLATFORM=offscreen`.

Two extra checks are worth running before you push, because CI runs them:

```bash
ffast-cli metrics validate    # freezes the metric graph, reports bad refs/shapes/cycles
ffast-cli metrics test        # runs the metrics' own declared test cases
```

## Style

- `black` and `isort`, both at line length 79. Config is in `pyproject.toml`.
- No new imports of PySide6, pyqtgraph or vispy from inside `ffast/`. That
  package has to stay importable on a compute node with no display, and
  `tests/ffast/test_ffast_core_boundary.py` will fail if you break it.
- Metrics must be picklable and deterministic. They run in a separate worker
  process, so no closures, no lambdas, no global mutation.

## A good place to start

The browser client is behind the desktop one, and closing that gap is the most
useful work going. It is unusually approachable: the server already does the
computing and the protocol events mostly exist, so a missing feature is usually
client-side wiring rather than new machinery. The code is plain ES modules in
`ffast/renderers/web/static/` with no build step — edit a `.js`, reload the tab.

Run both clients side by side (`ffast-qt` and `ffast`), find something the
desktop does that the browser doesn't, and open an issue saying you're taking it.

## Adding things

Before writing Python, check whether the declarative route covers you. New
metrics, plots and whole analysis tabs can be added from a project `ffast.toml`
with no code at all. See [docs/usage.md](docs/usage.md#custom-metrics-and-tabs).

For code changes, [CONTEXT.md](CONTEXT.md) defines the vocabulary the codebase
uses (Environment, Metric, Panel, Scene, and so on). Using those words in your
PR saves everyone a round trip.

## Design decisions

Anything that changes a seam between components gets an ADR in
[docs/adr/](docs/adr/): what the problem was, what we picked, what we gave up.
Copy the shape of a recent one, number it next in sequence, and link it from the
PR. There are 54 of them and they are the real documentation of why the code
looks like this. See [docs/adr/README.md](docs/adr/README.md) for the index.

## Pull requests

- Branch off `dev`.
- One logical change per PR.
- Say how you tested it. "Ran the suite" is fine for refactors; anything
  touching the UI needs a note on what you clicked and what you saw.
