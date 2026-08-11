# Example data

Five reference datasets, each with at least one prediction.

| Directory | Reference | Frames | Atoms | Periodic | Energy / force units |
| --- | --- | --- | --- | --- | --- |
| `variable-sized-molecular/` | `dataset.xyz` | 100 | 4–50 | no | eV, eV/Å |
| `fixed-sized-molecular/` | `md22_stachyose_sampled.xyz` | 273 | 87 | no | kcal/mol, kcal/mol/Å |
| `fixed-sized-periodic/` | `am26_subbed_100.extxyz` | 100 | 216 | yes | eV, eV/Å |
| `fixed-sized-subsystem/` | `graphene_sampled.xyz` | 100 | 114 | yes | kcal/mol, kcal/mol/Å |
| `variable-sized-periodic/` | `mptrj_sampled_over_200.extxyz` | 202 | 1–96 | yes | eV, eV/Å |

Notes that apply to all of them:

- Every prediction file holds the same frames, same order, same positions as its
  reference. Checked for all five pairs.
- Units are not uniform: two references in kcal/mol, three in eV. FFAST does not
  convert. In `fixed-sized-subsystem/` the reference and its prediction are in
  different units from each other.
- `random_prediction.xyz` is not a model. Reference plus noise, ~10% of the
  reference force standard deviation, used as a low-error control.
- `predictions_am26_subbed.xyz` copies the reference `energy_per_at` and `virial`
  verbatim; `predictions_mptrj_sampled.xyz` copies `energy_per_atom` and
  `corrected_total_energy`. Only `energy` and `forces` are predicted.
- AM26 and MPtrj hold more than one energy-like key, so the loader asks which to
  use. The other three load without asking.
- All twelve files parse with FFAST's ASE loaders and return the values below.

---

## `variable-sized-molecular/` — SPICE via MACE-OFF23

**Source.** MACE-OFF23 training set — a filtered, eV-converted copy of
[SPICE](https://github.com/openmm/spice-dataset), not SPICE as published.
Archives `train_large_neut_no_bad_clean.tar.gz` and `test_large_neut_all.tar.gz`
in Apollo record [doi:10.17863/CAM.107498](https://doi.org/10.17863/CAM.107498).
SPICE version 1 (MACE-OFF paper, Table 2). Reference level
ωB97M-D3(BJ)/def2-TZVPPD. Neutral configurations only, ion pairs dropped, ten
elements, ~85% of full SPICE.

- Archive:train_large_neut_no_bad_clean.xyz
- Point release: SPICE v1

**Licence.** SPICE data CC0; Apollo record MIT. Redistribution unrestricted.
Cite Eastman et al., *Sci. Data* **9**, 11 (2022) and Kovács, Moore, Browning
*et al.*, *J. Am. Chem. Soc.*,
[doi:10.1021/jacs.4c07099](https://doi.org/10.1021/jacs.4c07099).

**Contents.** 100 frames, 4–50 atoms, non-periodic, 10 elements
(H, C, N, O, F, P, S, Cl, Br, I). Energy `REF_energy` in `info`, forces in
`REF_forces`, plus `smiles`, `total_charge`, `config_type`. Mean |F| 0.72 eV/Å.
Subsets: 69 `PubChem`, 28 `DES370K Dimers`, 3 `DES370K Monomers`.

**Sampling.** Unrecorded. Subset split matches the training file's proportions
(68.0 / 27.6 / 1.8%) and rare elements appear in 1–2 frames each, consistent
with an unstratified uniform draw.

- Method: randomly selected using np.random.default_rng(seed=42).choice.

**Prediction** `prediction.xyz`. Keys `MACE_energy`, `MACE_forces`. Force MAE
14.7 meV/Å, RMSE 30.8, worst component 703 meV/Å; energy MAE 0.83 meV/atom;
r = 0.9996. Force figures here and in the table below are per force component,
matching the MACE-OFF paper. FFAST's own `ffast.force_mae_global` reports
29.3 meV/Å for this pair because it averages per-atom vector norms instead.
Per-subset force MAE against MACE-OFF paper Table S1 test values (meV/Å):

| Subset | here | 23(S) | 23(M) | 23(L) |
| --- | --- | --- | --- | --- |
| PubChem | 16.3 | 35.7 | 20.6 | 14.8 |
| DES370K Dimers | 7.0 | 16.3 | 9.0 | 6.6 |
| DES370K Monomers | 9.8 | 17.6 | 9.4 | 6.6 |

Monomers rests on 3 frames. Best match is 23(M) if these are training frames,
23(L) if they are test frames.

- Checkpoint: MACE-OFF23_medium.model

MACE-OFF23 weights are under the Academic Software License; the training data is
not. Stored here is model output, not weights.

**Control.** None.

## `fixed-sized-molecular/` — MD22 stachyose

**Source.** MD22, stachyose, from [sgdml.org](https://sgdml.org).

- File: `[download name, e.g. md22_stachyose.npz, source page, fetch date]`

**Licence.** Not established. sgdml.org publishes no dataset licence; its terms
of use cover uploads, not downloads.

- Decision: `["permission from the MD22 authors on <date>, keep the file", or
  "no permission, delete and download at runtime from sgdml.org"; plus who
  decided]`
- Citation: `[the Chmiela et al. MD22 reference the authors require]`

**Contents.** 273 frames, 87 atoms (C, H, O), non-periodic. Per-atom `forces`,
`energy` in kcal/mol. No other `info` keys. Mean |F| 19.1 kcal/mol/Å (0.83 eV/Å).

**Sampling.** Every 100th frame of the full stachyose trajectory from index 0.

**Prediction** `predictions_md22_stachyose.xyz`. SchNet, per the commit that
added it. Force MAE 2.67 kcal/mol/Å, slope 0.98, r = 0.990. Energy biased
2.2 kcal/mol low.

- Model: `[SchNetPack version, cutoff, interaction blocks, feature width]`

**Control** `random_prediction.xyz`. Force deviation SD 2.71 kcal/mol/Å (~10% of
reference force SD). Heavy-tailed, not a fixed percentage: half of force
components move under 1%, 14% move over 20%. Energy relative deviation ~3 × 10⁻⁶.

## `fixed-sized-periodic/` — AM26 amorphous carbon

**Source.** Data supporting "Amorphous materials as a frontier challenge for
universal interatomic potentials",
[arXiv:2607.11384](https://arxiv.org/abs/2607.11384). Reference forces from
C-GAP-17, per that paper. The file carries no key naming its source.

- Record: `[URL or DOI of the data behind that paper, and its version]`
- File: `[name of the file inside the record that am26.extxyz came from, and
  fetch date]`

**Licence.** Unknown.

- Licence: `[licence stated at that record]`
- Citation: `[reference the record or paper requires]`

**Contents.** 100 frames, 216 C atoms, periodic cubic cell. Keys `forces`,
`energy`, `energy_per_at`, `virial`, `cell_origin`, `label`, `system`
(`carbon`). Five melt-quench densities (1.5, 2.0, 2.5, 3.0, 3.5 g/cm³), 20
frames each, readable from `label=carbon-mq_<density>_<n>`. Mean |F| 1.07 eV/Å.

**Sampling.** First 100 frames of the source `am26.extxyz`.

**Prediction** `predictions_am26_subbed.xyz`. SchNet, per the commit that added
it. Does not reproduce the reference forces: over 64 800 components, predicted
magnitudes average 47% of reference, slope 0.199, r = 0.420. Directions are
wrong, so not a unit or normalisation error. Energy MAE 29 meV/atom. High-error
case, not a working model.

- Model: `[SchNetPack version, hyperparameters, and which densities it trained on]`
- Cause: `[why r = 0.420 — undertrained, wrong system, wrong checkpoint, wrong
  frame of reference — and whether the file is kept deliberately as a bad-model
  example or should be regenerated]`

**Control** `random_prediction.xyz`. Force deviation SD 0.14 eV/Å (10% of
reference force SD), energy MAE 0.011 meV/atom. Keeps only `energy`; drops the
reference's other `info` keys.

## `fixed-sized-subsystem/` — naphthyridine on graphene

**Source.** TEA Challenge 2023, Challenge III,
[doi:10.5281/zenodo.14138387](https://doi.org/10.5281/zenodo.14138387).
1,8-naphthyridine on graphene, six independent NVT MD runs at 500 K, 1 fs
timestep, 15 000 structures at PBE + MBD-NL.

**Licence.** CC BY 4.0. Attribution:

> Poltavsky, Igor; Puleva, Mirela; Charkin-Gorbulin, Anton; Fonseca, Grégory;
> Tkatchenko, Alexandre (2024). *TEA Challenge 2023.* Zenodo.
> https://doi.org/10.5281/zenodo.14138387

Paper:

> I. Poltavsky, A. Charkin-Gorbulin, M. Puleva *et al.*, "Crash testing machine
> learning force fields for molecules, materials, and interfaces: model analysis
> in the TEA Challenge 2023", *Chem. Sci.* **16**, 3720–3737 (2025).
> [doi:10.1039/d4sc06529h](https://doi.org/10.1039/d4sc06529h)

**Contents.** 100 frames, 114 atoms, periodic non-orthogonal cell. Per-atom
`forces` and `energy` in kcal/mol. Formula C106H6N2 in every frame. By bond
connectivity in frame 0: a C98 sheet at z = 15.46–16.36 Å and a C8H6N2 fragment
at z = 19.21–20.26 Å, ~3 Å above. Adsorbate is indices 98–113, contiguous at the
end.

**Sampling.** Every 150th frame of the 15 000 in the source, from index 0.

**Prediction** `graphene_prediction.npz`. Only `npz` prediction here. Keys `R`,
`F`, `E`, `z`, `lattice`, `unit_cell`.

- Model: MACE, trained on the largest TEA Challenge III training set.

Two defects:

- **Units differ from the reference.** Reference kcal/mol and kcal/mol/Å,
  prediction eV and eV/Å. Over 34 200 force components the ratio is a uniform
  0.0433663 against the exact kcal/mol → eV factor 0.0433641 — four parts in
  100 000, the residual explained by `float32` storage of `F`. r = 0.999997.
  Converted, this is the most accurate prediction in the directory: 0.0020 eV/Å
  force MAE, 0.09 meV/atom energy, no constant offset. Loaded as stored it looks
  like the worst, 21.65 eV/Å.
- **`z`, `lattice`, `unit_cell` are truncated.** The sampling script applied
  `[::150]` to every key, not just per-frame ones. `z` is `[6]` (carbon alone,
  for a three-species system) and `lattice`/`unit_cell` are one `(1, 3)` row
  holding the first cell vector where the reference cell is 3 × 3. `R` and `E`
  are unaffected. Loading as a *prediction* is fine (reads only `E` and `F`);
  loading as a *dataset* through the `sGDML` loader reports the formula as `C1`.

**Control.** None.

## `variable-sized-periodic/` — MPtrj

**Source.** MPtrj (Materials Project trajectories), local copy of the
`mptrj-gga-ggapu` per-material files. Frames carry `mp_id`, `task_id`, `calc_id`,
`ionic_step`; first is `mp-100`.

- Record: `[URL or DOI the local copy came from — usually the CHGNet Figshare
  record — and fetch date]`
- Version: `[MPtrj release identifier stated at that record]`

**Licence.** CC BY 4.0 plus the Materials Project terms of use.

- Attribution: `[the attribution line the record requires, plus the CHGNet/MPtrj
  citation]`

**Contents.** 202 frames, 1–96 atoms (one frame is a single atom), periodic, 27
elements. Per-atom `forces`, `energy`, `stress`, plus `bandgap`,
`energy_per_atom`, `corrected_total_energy`, `ef_per_atom`, `e_per_atom_relaxed`,
`ef_per_atom_relaxed`. Mean |F| 0.29 eV/Å, max 34.8 eV/Å.

**Sampling.** Source files walked in directory order, each accepted with
probability 0.3, all frames of an accepted file appended until the list passed
200 — hence `over_200` and the count of 202. No fixed seed, so not reproducible.

- Script: `[path in this repository, or the script text if never committed]`
- Accepted files: `[the `mp_id` values or file names the draw accepted — the only
  way to reconstruct the selection]`

**Prediction** `predictions_mptrj_sampled.xyz`. SchNet, per the commit that added
it. Force MAE 0.046 eV/Å, slope 0.997, r = 0.996.

- Model: `[SchNetPack version, hyperparameters, MPtrj training subset, and
  whether these 202 frames were held out]`

**Control** `random_prediction.xyz`. Force deviation SD 0.15 eV/Å, energy MAE
0.009 meV/atom. Noise scale is global while force magnitudes vary widely across
frames, so relative error per frame varies more than in the fixed-size sets.

---

If you add data here, record source, licence and sampling at the same time.
