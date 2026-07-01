# Loupe 3D View — Feature Reference

Feature inventory for the Vispy-based 3D molecular viewer. The server sends
renderer-neutral `RenderScene` objects (`SCENE_SNAPSHOT` / `SCENE_PATCH`) and the
`VispySceneAdapter` owns all drawing — the client holds no geometry state
(rationale: ADR 0014).

---

## Rendering

| Feature | Server stage | Setting key(s) |
|---------|-------------|---------------|
| **Atoms** — CPK spheres with element colors | `ffast.atom_colors` | `atomColorType`, `atomColorSource`, `atomColorMap` |
| **Bonds** — line segments, fixed or dynamic topology | `ffast.bond_positions` | `bondType`, `bondWidth`, `bondColor`, `fixedBondIndices` |
| **Force vectors** — 3D mesh arrows | `ffast.force_arrows` | `showForceVectors`, `forceVectorsLength`, `forceVectorsNormalised`, `forceVectorsModelKey` |
| **Unit cell** — box edges for periodic systems | `ffast.unit_cell_edges` | `showUnitCell` |
| **Atom index labels** — text overlay | `ffast.atom_labels` | `showSceneLabels` (VIEW SETTINGS pane) |
| **Selection overlay** — highlight picked atoms | server `SET_SELECTION` | `sceneSelectIndices` (VIEW SETTINGS pane) |
| **Colorbar** — value legend for metric coloring | client-drawn from `ColorBy` in patch | auto-shown when coloring active |

---

## Atom coloring

The `atomColorSource` setting selects the color source; the ATOMS pane "Coloring"
control drives it.

| Mode | `atomColorSource` value | Entry point |
|------|------------------------|-------------|
| By element (CPK) | `"element"` | Coloring → Elements |
| By metric (per-atom shape) | `"metric:<metric_id>"` | Coloring → Metric |
| Force MAE (mean or per-frame) | `"metric:ffast.force_mae"` | Coloring → Mean/Force Error |
| Acceleration norm error | `"metric:ffast.accel_mae_per_atom"` | Coloring → Acceleration Norm Error |
| Displacement (total / mean) | `"metric:ffast.displacement_stats"` | Coloring → Total/Mean Displacement |

Colormaps: `viridis`, `inferno`, `plasma`, `coolwarm`, `hot`, `bwr`, `force_error`.
The prediction-model selector lists models with cached force predictions for the
active dataset.

---

## Camera — client-local

| Control | Mechanism |
|---------|----------|
| Azimuth / elevation / distance | TurntableCamera, via LineEdit or drag |
| Orthographic ↔ perspective | FOV = 0 vs 45° |
| Center-of-mass tracking | Camera center follows molecule centroid each patch |
| Preset views: Top / Front / Side | Buttons in CAMERA pane |
| Orientation axes gizmo | Screen-corner XYZ axis, updated each camera change |

---

## Picking — client-local (ADR 0015)

Ray-cast picking detects the atom under the cursor and maps it to its scientific
atom id.

| Interaction | Result |
|-------------|--------|
| Left-click on atom | Toggle atom into the working selection; committed as the `"picked"` selection |
| Ctrl+drag | Rubber-band rectangle; all enclosed atoms added |
| Hover | Transient highlight, client-only (not sent to the server) |

---

## Atom selection tools — client-local

One tool is active at a time; activating one shows a toolbar with its label and info
readout.

| Tool | Module | Max select | Rectangle | Purpose |
|------|--------|-----------|-----------|---------|
| Info | loupeInfoSelect | 4 (cycle) | — | Position / distance / angle / dihedral readout |
| Filter | loupeAtomFilter | unlimited | ✓ | Build atom filter list |
| Bond | loupeBonds | 2 | — | Toggle bonds in fixed bond list |
| Align | loupeAtomAlign | 3 | — | Pick 3 atoms for frame alignment |
| Force vector | loupeForceVectors | unlimited | ✓ | Choose atoms that show force arrows |

---

## Frame navigation / video

Frame slider + play/pause/prev/next (`loupeVideo.py`). The video loop waits for each
`SCENE_PATCH` before advancing (server-synced); `SET_FRAME` is debounced so rapid
slider drags send only the latest frame.

| Setting | Default | Description |
|---------|---------|-------------|
| `videoFPS` | 30 | Target playback rate (fps) |
| `videoSkipFrames` | 0 | Extra frames to skip each step |

---

## Alignment

| Feature | Setting key | Server stage |
|---------|------------|-------------|
| Kabsch alignment to frame 0 | `alignKabsch` (VIEW SETTINGS) | `ffast.kabsch_alignment` |
| Heavy-atoms-only Kabsch | `alignKabschHeavyOnly` (VIEW SETTINGS, default on) | `ffast.kabsch_alignment` (`heavy_only` param) |
| 3-atom frame alignment | `alignAtoms` / `alignAtomsIndices` (VIEW SETTINGS) | `ffast.atom_align` |

---

## Atom filter

`sceneFilterIndices` (VIEW SETTINGS pane) drives `ffast.atom_filter`. Tokens are
integers or element symbols (`C`; `-H` to exclude). The ATOM FILTER pane's "Create"
button makes a new atom-filtered dataset client-side.

---

## Export — client-local

PNG export from the EXPORT menu: **Opaque** (chosen RGB background) or **Transparent**
(alpha mask).

---

## Not in the 3D view

- **Radius of gyration** — a server-side metric (`ffast.gyradius`, structure geometry
  only), shown as a distribution in the **Plots** panel, not in the Loupe. Listed here
  because it is often mistaken for a 3D-view feature.

---

## Superseded legacy controls (deleted)

Render-Path-era panels whose checkboxes fired the old client-only `updateGeometry`
action never reached the server; the VIEW SETTINGS pane provides the server-wired
equivalents. Retired: the ATOMS-pane Kabsch checkboxes (`loupeKabschAlign.py`, →
`alignKabsch` + `alignKabschHeavyOnly`) and the INDICES pane (`loupeIndices.py`, →
`showSceneLabels`).
