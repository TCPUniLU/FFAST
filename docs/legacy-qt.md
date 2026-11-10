# Legacy Qt desktop client

[README](../README.md) · [Documentation](README.md) · [Using FFAST](usage.md)

`ffast-qt` is being retired and is planned for removal. Use the browser client
for current features. The legacy client uses the same Python server.

## Launch and shortcuts

```bash
uv pip install -e ".[gui]"
ffast-qt --workdir /path/to/data
```

It starts a managed local `ffast-server` in the background and connects to it,
so a local session runs the same code path as a remote one. Menu-driven:

| Shortcut | Action |
|----------|--------|
| `Ctrl+d` | Load dataset |
| `Ctrl+m` | Load model |
| `Ctrl+p` | Load prediction |
| `Ctrl+0` | Load zero model |
| `Ctrl+l` | Load session |
| `Ctrl+s` | Save session |
| `Ctrl+n` | New 3D view window |
| `Ctrl+Shift+C` | Connect to cluster |
| `Ctrl+Shift+L` | Connect to a local server |
| `Ctrl+Shift+D` | Load remote dataset |
| `Ctrl+Shift+P` | Load remote prediction |

Loaded datasets and models appear in the left sidebar; analysis tabs run across
the top; the 3D view opens in its own window with the settings panes on its
right. Debug output goes to `debug.log` in the working directory.

## Plot labels and layout

The desktop supports built-in and project tabs. A 3D panel is shown as a
placeholder because the desktop opens 3D views in separate windows; tabs made
only of 3D panels are omitted. Browser-created user tabs and user metrics are
not loaded by the desktop.

Double-click an axis label or legend entry to edit it. The context menu offers
font size and legend position. Cosmetic edits are stored locally in
`~/.ffast/display_overrides.json`; they do not affect metric calculations.
`energyUnit` and `forceUnit` in `config/default.json` set default unit labels.

For installation and Qt/OpenGL issues, see
[legacy troubleshooting](troubleshooting.md#legacy-qt-desktop-client).
