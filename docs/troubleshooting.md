# Troubleshooting

## Install and launch

**Wrong Python.** FFAST pins 3.11 (`requires-python = "==3.11.*"`). If pip
resolved something odd or an import fails in a way that makes no sense, check
`python -V` first.

**`command not found: ffast-qt`, or `ffast` opens the wrong client.** Console
scripts are written at install time, not read from `pyproject.toml` at run time,
so an editable install made before the entry points changed keeps the old ones.
Check what your environment actually has:

```bash
python -c "from importlib.metadata import entry_points
print([f'{e.name} -> {e.value}' for e in entry_points(group='console_scripts') if 'ffast' in e.name])"
```

You should see five: `ffast` pointing at `launcher:main`, plus `ffast-qt`
(`main:cli`), `ffast-web`, `ffast-server` and `ffast-cli`. Anything else means a
stale install — reinstall to rewrite the scripts: `pip install -e . --no-deps`.

`ffast` runs the desktop client when PySide6 is importable and the browser
client when it is not. If it starts the browser on a machine where you expect
the desktop, PySide6 is missing or broken: `pip install -e ".[gui]"`.

**The browser tab opens but stays disconnected.** The web page and the server are
two ports. Run `ffast --no-browser`, read the URL it prints, and open that; if the
page loads but the status stays "Disconnected", the WebSocket port is the
problem, not the page. Pin both with `--ws-port` and `--web-port` and check
nothing else holds them.

**Blank page, or a page with no styling.** This means the static assets did not
ship with the install. It was a real packaging bug once. Reinstall, and if it
persists check that `ffast/renderers/web/static/` exists inside your site-packages
copy of the package.

**Anything involving a synced folder.** Do not install into a virtualenv that
lives in iCloud Drive, Dropbox or OneDrive. The sync client deletes and rewrites
`.pyc` files under you, and on macOS it breaks the Qt plugin layout badly enough
that the app cannot find its platform plugin. Move the venv out and reinstall.

## The desktop client specifically

Nearly every problem here is the native OpenGL/Qt stack rather than FFAST
itself, which is why the browser client exists as an alternative when the stack
cannot be fixed.

**"Could not find the Qt platform plugin 'cocoa'" (macOS).** Almost always the
synced-folder problem above. Recreate the venv somewhere local:

```bash
python3.11 -m venv ~/venvs/ffast && source ~/venvs/ffast/bin/activate && pip install -e ".[gui]"
```

**Segmentation fault at startup.** Usually a mismatched PySide6. Pin it:
`pip install "pyside6>=6.8,<6.9"`. Two Qt widget base classes cannot be mixed
under 6.8, so if you wrote a widget recently, suspect that.

**OpenGL errors, or a blank 3D window.** Update graphics drivers. On Linux,
`sudo apt install libgl1-mesa-glx` and check `glxinfo | grep OpenGL`.
`LIBGL_ALWAYS_SOFTWARE=1` sometimes gets you a picture. The vispy renderer has no
software fallback of its own, so if GL is broken, the 3D view is simply gone.

**Plots are sluggish when panning.** This has bitten twice, both times software
rasterisation of antialiased lines rather than anything about point counts.
If you hit it in new plot code, that is where to look.

## Data

**"Cannot load dataset".** Check the format is actually supported. For `.npz`,
confirm it has `R`, `E`, `F`, `z`. For ASE formats, confirm ASE itself can read
it: `python -c "import ase.io; ase.io.read('file')"`.

**"Fingerprint mismatch" when loading predictions.** The dataset file is not the
one the predictions were computed against. Byte-for-byte, not "the same
molecule". Regenerate the predictions or find the original file.

**Models with hash names appear in the sidebar.** Those are ghost models,
reconstructed from cached predictions when the original model file was not
available. They work for everything already computed and cannot compute anything
new. Delete them if you do not want them.

**Axes read `[energyUnit]` or `[forceUnit]`.** Working as intended: no unit has
been set, and FFAST won't guess one from your data. Double-click the label to
type one, or set `energyUnit` / `forceUnit` in `config/default.json` to apply it
everywhere. See [usage.md](usage.md#units-and-editing-labels-by-hand).

**Plots say there is no data.** Both a dataset and a prediction have to be
selected, and the computation has to have finished. Watch the task list.

**A metric silently produces nothing.** Run `ffast-cli metrics validate`. It
compiles the whole graph and reports bad references, shape mismatches and cycles,
which is faster than inferring the problem from an empty panel.

## Performance

**Predictions take forever.** They are cached by content fingerprint, so you pay
once. For anything large, compute them on the machine that has the GPU and load
the resulting file.

**The 3D view is slow.** Turn off bonds or force vectors, shrink atoms, or make a
subset. Dynamic bond detection is the expensive part on big systems.

**Large trajectories over a cluster connection.** Load with a stride. The dialog
offers one when the file is long. Then extract the subset you actually care about
and inspect that locally.

## Still stuck

The server prints its errors to the terminal it runs in; the Qt client writes
`debug.log`; the web client puts client-side errors in the browser console. All
three are worth reading before opening an issue, and worth pasting into it if you
do.
