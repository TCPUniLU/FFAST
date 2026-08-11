# Example data

Five reference datasets, each with at least one prediction to compare against it.
Together they cover the shapes FFAST treats differently: fixed and variable atom
count, molecular and periodic, and one subsystem case where an adsorbate sits on
a slab.

| Directory | Reference | Frames | Atoms | Periodic | Energy / force units |
| --- | --- | --- | --- | --- | --- |
| `.` | `dataset.xyz` | 100 | 4–50 | no | eV, eV/Å |
| `fixed-sized-molecular/` | `md22_stachyose_sampled.xyz` | 273 | 87 | no | kcal/mol, kcal/mol/Å |
| `fixed-sized-periodic/` | `am26_subbed_100.extxyz` | 100 | 216 | yes | eV, eV/Å |
| `fixed-sized-subsystem/` | `graphene_sampled.xyz` | 100 | 114 | yes | kcal/mol, kcal/mol/Å |
| `variable-sized-periodic/` | `mptrj_sampled_over_200.extxyz` | 202 | 1–96 | yes | eV, eV/Å |

Every prediction file holds the same frames in the same order as its reference,
with identical positions and atom ordering — checked for all five pairs.

Three things to know before loading anything:

- **Units are not uniform.** Two references are in kcal/mol, three in eV, and in
  the subsystem case the reference and its prediction disagree with each other
  (see below). FFAST does not convert.
- **`random_prediction.xyz` is not a model.** It is the reference with noise
  added, used as a low-error control.
- **Predictions copy some reference keys verbatim.** `predictions_am26_subbed.xyz`
  carries the reference's `energy_per_at` and `virial`, and
  `predictions_mptrj_sampled.xyz` carries the reference's `energy_per_atom` and
  `corrected_total_energy`, all unchanged. Only `energy` and `forces` are
  predicted values.
- **Two of them trigger the key-selection dialog.** The AM26 and MPtrj files hold
  more than one energy-like key, so the loader asks which to use. The other three
  have a single energy and load without asking.

All twelve files were checked against FFAST's ASE loaders: each parses, and each
returns the frame count, energies and forces listed below.

Licences: `dataset.xyz` is CC0, the graphene and MPtrj data are CC BY 4.0. The
MD22 and AM26 sources state no licence, so redistribution of those two is not
settled — see their sections.

`dataset.xyz` and `prediction.xyz` are load-bearing: `examples/headless/headless.py`,
the demo `ffast.toml` at the repository root and several tests refer to them by
path.

---

## `dataset.xyz` + `prediction.xyz` — variable-sized molecular

**Source.** The [SPICE dataset](https://github.com/openmm/spice-dataset). The
frames keep SPICE's own `config_type` values (`PubChem`, `DES370K Monomers`,
`DES370K Dimers`) alongside `smiles`, `total_charge` and `REF_energy`. Which
SPICE release, and how the 100 frames were drawn, is not recorded.

**Licence.** CC0 1.0, per the
[Zenodo record](https://doi.org/10.5281/zenodo.7338495). Redistribution is
unrestricted; citing Eastman et al., *Sci. Data* **9**, 11 (2022) is courtesy.

**Contents.** 100 frames, 4 to 50 atoms, non-periodic, 10 elements. Forces are in
the `REF_forces` array rather than `forces`; energy is `REF_energy` in `info`.
Mean |F| is 0.72 eV/Å.

**Prediction** `prediction.xyz`. Keys are prefixed `MACE_` (`MACE_forces`,
`MACE_energy`), so this is MACE output; the model and version are not recorded.
Force MAE 0.015 eV/Å, Pearson r = 0.9996 against the reference.

## `fixed-sized-molecular/` — MD22 stachyose

**Source.** MD22, stachyose, from [sgdml.org](https://sgdml.org).

**Licence.** Not established. sgdml.org publishes no licence for its datasets;
its terms of use cover uploads to the site, not downloads. Redistributing this
sample needs either permission from the MD22 authors plus the citation they ask
for (Chmiela et al.), or a decision to point users at sgdml.org instead.

**Contents.** 273 frames, 87 atoms each (C, H, O), non-periodic, with per-atom
`forces` and `energy` in kcal/mol as sGDML publishes them. Mean |F| is
19.1 kcal/mol/Å (0.83 eV/Å). No other `info` keys.

**Sampling.** Every 100th frame of the full stachyose trajectory, starting at
index 0, which is what gives 273.

**Prediction** `predictions_md22_stachyose.xyz`. SchNet, per the commit that
added it; the training set and hyperparameters are not recorded. Force MAE
2.67 kcal/mol/Å, slope 0.98, r = 0.990. Energy is biased 2.2 kcal/mol low on
average.

**Control** `random_prediction.xyz`. Reference forces plus noise: deviation
standard deviation 2.71 kcal/mol/Å, about 10% of the reference force standard
deviation, and the same 10% figure holds for the other two controls. The noise
is heavy-tailed rather than a fixed percentage — half of all force components
move by under 1%, but 14% move by more than 20%. Energies are barely touched
(relative deviation ~3 × 10⁻⁶).

## `fixed-sized-periodic/` — AM26 amorphous carbon

**Source.** Reported as the data supporting "Amorphous materials as a frontier
challenge for universal interatomic potentials",
[arXiv:2607.11384](https://arxiv.org/abs/2607.11384); the file itself carries no
key naming the source. The reference forces come from C-GAP-17, per that paper.

**Licence.** Unknown. The repository or Zenodo record holding the paper's data,
and the licence stated there, still have to be identified before this file can be
redistributed.

**Contents.** 100 frames, 216 carbon atoms each, periodic in a cubic cell, with
per-atom `forces`, `energy`, `energy_per_at`, `virial`, `cell_origin`, `label`
and `system` (`carbon` throughout). Five melt-quench densities — 1.5, 2.0, 2.5,
3.0 and 3.5 g/cm³ — 20 frames each, readable from `label=carbon-mq_<density>_<n>`.
Mean |F| is 1.07 eV/Å.

**Sampling.** The first 100 frames of the source `am26.extxyz`.

**Prediction** `predictions_am26_subbed.xyz`. SchNet, per the commit that added
it. It does not reproduce these forces: over all 64 800 components the predicted
magnitudes average 47% of the reference, best-fit slope 0.199, r = 0.420. That is
not a unit or normalisation error, which would scale magnitudes while keeping
directions — these forces point the wrong way. Energies are closer, at 29 meV/atom
mean absolute error. Useful as a high-error case, not as a working model.

**Control** `random_prediction.xyz`. Same construction as above: force deviation
standard deviation 0.14 eV/Å (10% of the reference force standard deviation),
energy MAE 0.011 meV/atom. Drops the reference's extra `info` keys, keeping only
`energy`.

## `fixed-sized-subsystem/` — naphthyridine on graphene

**Source.** Challenge III of the TEA Challenge 2023 dataset,
[Zenodo 14138387](https://doi.org/10.5281/zenodo.14138387): 1,8-naphthyridine
adsorbed on graphene, from six independent NVT molecular-dynamics runs at 500 K
with a 1 fs timestep, 15 000 structures with forces and energies at the
PBE + MBD-NL level.

**Licence.** CC BY 4.0. Attribution:

> Poltavsky, Igor; Puleva, Mirela; Charkin-Gorbulin, Anton; Fonseca, Grégory;
> Tkatchenko, Alexandre (2024). *TEA Challenge 2023.* Zenodo.
> https://doi.org/10.5281/zenodo.14138387

The paper the record supports, and the better reference for the data:

> I. Poltavsky, A. Charkin-Gorbulin, M. Puleva *et al.*, "Crash testing machine
> learning force fields for molecules, materials, and interfaces: model analysis
> in the TEA Challenge 2023", *Chem. Sci.* **16**, 3720–3737 (2025).
> [doi:10.1039/d4sc06529h](https://doi.org/10.1039/d4sc06529h)

**Contents.** 100 frames, 114 atoms each, periodic in a non-orthogonal cell, with
per-atom `forces` and `energy` in kcal/mol. The formula is C106H6N2 in every
frame. Splitting frame 0 by bond connectivity gives a C98 sheet spanning
z = 15.46–16.36 Å and a separate C8H6N2 fragment at z = 19.21–20.26 Å, about 3 Å
above it. The adsorbate is indices 98–113, contiguous at the end, which is what
makes this the subsystem example.

**Sampling.** Every 150th frame of the 15 000 in the source, starting at index 0.

**Prediction** `graphene_prediction.npz` — the only prediction here in `npz`
rather than `xyz`. Keys are `R`, `F`, `E`, `z`, `lattice`, `unit_cell`. Which
model produced it is not recorded.

Two defects to know about:

- **The two files are in different units.** The reference is kcal/mol and
  kcal/mol/Å; the prediction is eV and eV/Å. Over all 34 200 force components the
  prediction is a uniform 0.0433663 times the reference against the exact
  kcal/mol → eV factor of 0.0433641, agreeing to four parts in 100 000, with the
  residual explained by `float32` storage of `F`. Pearson r is 0.999997.
  Converting the reference to eV leaves 0.0020 eV/Å force MAE and 0.09 meV/atom
  in energy, with no constant offset — the most accurate prediction in this
  directory. Loaded as stored it looks like the worst, at 21.65 eV/Å.
- **`z`, `lattice` and `unit_cell` are truncated.** The sampling script applied
  the same `[::150]` slice to every key in the source `npz`, not just the
  per-frame ones. So `z` is `[6]`, listing carbon alone for a three-species
  system, and `lattice`/`unit_cell` are a single `(1, 3)` row holding the first
  cell vector where the reference cell is a 3 × 3. `R` and `E` are unaffected and
  match the reference frame for frame. Loading the file as a *prediction* is
  therefore fine — that path reads only `E` and `F`. Loading it as a *dataset*
  through the `sGDML` loader is not: it reports the formula as `C1`.

**Control.** None.

## `variable-sized-periodic/` — MPtrj

**Source.** MPtrj (Materials Project trajectories), from a local copy of the
`mptrj-gga-ggapu` per-material files. The frames carry `mp_id`, `task_id`,
`calc_id` and `ionic_step`; the first is `mp-100`. Which record that copy came
from, and its version, is not recorded — needed because MPtrj is CC BY 4.0 plus
the Materials Project terms of use, and the citation the licence asks for lives
with the record.

**Contents.** 202 frames, 1 to 96 atoms (one frame is a single atom), periodic,
27 elements, with per-atom `forces`, `energy` and `stress`, plus `bandgap`,
`energy_per_atom`, `corrected_total_energy`, `ef_per_atom`, `e_per_atom_relaxed`
and `ef_per_atom_relaxed`. Mean |F| is 0.29 eV/Å, with a maximum of 34.8 eV/Å.

**Sampling.** Source files were walked in directory order, each accepted with
probability 0.3, and all frames of an accepted file appended to one list until it
passed 200 records — hence `over_200` and the exact count of 202. The draw used
no fixed seed, so the selection is not reproducible.

**Prediction** `predictions_mptrj_sampled.xyz`. SchNet, per the commit that added
it. Force MAE 0.046 eV/Å, slope 0.997, r = 0.996.

**Control** `random_prediction.xyz`. Force deviation standard deviation
0.15 eV/Å, energy MAE 0.009 meV/atom. Because the noise scale is global while
force magnitudes vary widely across these frames, its relative error per frame
varies far more than in the two fixed-size sets.

---

If you add data here, record source, licence and sampling in this file at the
same time. A dataset with no provenance is not usable by anyone else.
