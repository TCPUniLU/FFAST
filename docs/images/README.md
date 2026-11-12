# Documentation screenshots

These images were captured from the running browser client on 2026-11-09.
Open an image on GitHub to inspect it at full resolution.

| Image | Source and state |
| --- | --- |
| [hero.png](hero.png) | Custom comparison tab using MD22 Stachyose, SchNet predictions and a reference-plus-noise control. Atoms are coloured by SchNet force error. |
| [loading-data.png](loading-data.png) | File browser with the bundled SPICE reference selected, Frames ▸ Sample by energy (even, 40 frames, automatic bins, seed 42, REF_energy per atom) with its preview, and its stored units named as eV and eV/Å. Captured 2026-11-12. |
| [metric-formula.png](metric-formula.png) | Draft `mean(norm(pred_F - ref_F), by=structure)` tried on the bundled SPICE/MACE pair. The preview contains 100 values; the draft is unsaved. |
| [tab-editor.png](tab-editor.png) | Custom SPICE analysis tab with force-error density and timeline panels beside a 3D panel. Edit mode shows controls and resize handles; the camera was adjusted to show the molecule. |
| [lammps-live.png](lammps-live.png) | Bundled aspirin replay after Stop on alarm, inspected at the chemistry onset. Scores and attribution were computed by the real scorer; atom order remains unverified. |

Dataset sources and units: [example data](../../examples/data/README.md).
Replay provenance: [LAMMPS examples](../../examples/lammps/README.md).

To refresh a screenshot, open the corresponding workflow in an editable FFAST
checkout, wait for plots or previews to finish, and capture the relevant browser
window or dialog. Preserve the data labels and units, then update its caption
and the capture date here. Workflow diagrams are Mermaid blocks in the guides;
GitHub renders them directly.
