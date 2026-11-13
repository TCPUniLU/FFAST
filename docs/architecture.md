# How FFAST is put together

This is the map. The reasoning behind each seam lives in the ADRs, which are
indexed in [adr/README.md](adr/README.md); this page just tells you what talks to
what and why the boundaries fall where they do.

## The one decision everything follows from

Datasets used for force-field work are large and they live on the machine that
trained the model, usually an HPC cluster. Copying a 200k-configuration
trajectory to a laptop so you can look at ten frames of it is absurd. So FFAST is
split into a compute half and a display half, and the split is a network socket
even when both halves are on the same machine.

```
   browser client                   WebSocket, msgpack           ffast-server
  ┌────────────────────────┐  ◄────────────────────────────►  ┌────────────────┐
  │ draws plots            │                                  │ Environment    │
  │ draws the 3D scene     │      scenes, metric arrays,       │ datasets       │
  │ handles picking        │      task progress, events        │ models         │
  │ owns nothing else      │                                   │ cache         │
  └────────────────────────┘                                  │ metric workers │
                                                              └────────────────┘
                                                               laptop, or a
                                                               SLURM compute node
```

The client holds no scientific state. It knows which dataset is selected and what
the camera is doing, and that is close to all. Every number it draws came over
the wire. That is what lets the same client drive a cluster node it has never
seen, and it is why "run it locally" is just the degenerate case where the server
happens to be on loopback (ADR 0027).

## The pieces

**`ffast/`** is the headless core, about 34k lines of Python. It imports no GUI toolkit and no
OpenGL, so it runs on a compute node with no display. This is a hard rule with a
test behind it (`tests/ffast/test_ffast_core_boundary.py`). The rule
exists because the whole remote story dies the moment something in the server's
import closure needs a display.

Inside it:

- `core/` is the Environment: the datasets, the models, the fingerprint-keyed
  cache, and the task manager that runs heavy work off the event loop. It was one
  god object and is now composed of named services (ADR 0020, 0034).
- `metrics/` is the registry, the built-in metrics, and the worker pool they run
  in. A metric is a pure function of arrays. It never sees the Environment, a
  Dataset or a Model (ADR 0011). Its inputs, output shape, parameters and label
  are read off its Python signature (`signature.py`), so declaring a metric is
  mostly writing the function.
- `visualization/` builds a renderer-neutral scene: atom positions, radii,
  colour *values*, bond pairs. It does not know Three.js from any other renderer. The client
  maps values to pixels (ADR 0010, 0016, 0052).
- `protocol/` is the wire format. Pydantic models both ways, msgpack on the
  socket (ADR 0001, 0006, 0033).
- `cache/` keys every computed result by a content fingerprint of the model and
  dataset that produced it, so restarting or reconnecting recomputes nothing.
- `config/` holds the TOML schema and the built-in analysis tabs, which are
  themselves just TOML files.
- `live/` watches LAMMPS runs: it reads the dump as it grows, scores frames
  against a reference, and writes the job wrapper and include file (ADR 0086,
  0087, 0091).
- `cli/`, `loaders/`, `plugins/`, `session/`, `renderers/`, `io/`, `chemistry/`.

**`server.py`** is the `ffast-server` entry point. It owns one `ServerSession`
per connection with an explicit handler table, so you can read the whole protocol
surface in one file.

**The browser client** is the only client, and `ffast` starts it (ADR 0090, 0098).
It lives in `ffast/renderers/web/static/`, about 21k lines of hand-written ES
modules. No build step, no bundler, no npm. Three.js and Plotly
are vendored as files. You edit a `.js` and reload the tab, which was a
deliberate trade: a scientific tool that needs a Node toolchain to change one
line will not get changed.

It needs no native GL or GUI toolkit install, which failed on too many Linux
setups and cannot be fixed on a shared cluster login node (ADR 0045, 0098).

**`cluster/`** submits a SLURM job, waits for it, opens an SSH tunnel to the
compute node, and connects the same protocol to it. It will also install itself
on the cluster if it is not there: build a wheel locally, push it over SSH, make
a venv, pip install, drop a checksum marker so it does not do it twice
(ADR 0028). Sessions survive disconnects; the cluster dialog
reconnects to a still-running job rather than starting a new one (ADR 0024).

## Two things are declarative rather than coded

Both grew out of the same observation: most requests for "a new plot" were the
same five plots against different numbers.

**Analysis tabs.** A tab is a grid of panels, a panel binds a metric to an axis,
and both are TOML. The tabs that ship (3D, Basic Errors, Subsystem Errors,
Atomic Errors, Gyration, Live run) are defined this way in `ffast/config/builtin_tabs/`,
with no privileged status. Yours sit alongside them in a project `ffast.toml`.
The server never learns what a plot is; it answers metric requests (ADR 0011,
0021).

**Dataset fields.** Any numeric key in an extxyz file, per-frame or per-atom,
can be named in TOML and becomes a first-class plottable metric. No Python
(ADR 0023). Expression metrics go one step further and let you do element-wise
algebra over existing metrics in the config file itself (ADR 0042).

## Where the bodies are buried

Honest notes, because the diagram above is tidier than the code.

- Live inference from a trained model file works server-side (ADR 0030) but is
  rough. The path everyone actually uses is pre-computed predictions.
- The result-buffer and zstd machinery in `visualization/buffers.py` is dormant
  by decision, not by accident. WebSocket permessage-deflate already compresses,
  so compressing again bought nothing (ADR 0031).

## Reading order

If you want to understand the codebase, read in this order:

1. [CONTEXT.md](../CONTEXT.md) for the vocabulary.
2. ADR 0011 (metrics are pure) and ADR 0021 (panels are config). Together they
   explain most of the analysis side.
3. ADR 0010 and ADR 0016 for how a 3D scene crosses the socket.
4. ADR 0026 and ADR 0047 for why `ffast/` looks the way it does.
5. ADR 0045, 0090 and 0098 for why the browser is the only client.
