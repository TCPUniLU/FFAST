# Example data

A small corpus for trying FFAST out and for checking a change against the cases
that behave differently: fixed-sized and variable-sized systems, molecular and
periodic, plus one subsystem case.

Everything here is a **sample** of a larger public dataset, cut down to
something that loads instantly. None of it is suitable for training or for
quoting numbers from.

> **Provenance is incomplete.** The `Source` and `Licence` fields below marked
> TODO still need filling in by whoever produced the file. Until they are
> filled, treat the affected files as third-party data under their original
> terms, not under this repository's MIT licence. See
> [Licensing](#licensing).

## What is here

| Directory | Frames | Atoms | Periodic | System |
|---|---|---|---|---|
| `fixed-sized molecular/` | 273 | 87 | no | stachyose, C24H42O21 |
| `fixed-sized periodic/` | 100 | 216 | yes | carbon, C216 |
| `fixed-sized subsystem/` | 100 | 114 | yes | graphene with adsorbate, C106H6N2 |
| `variable-sized molecular/` | 100 | 4–50 | no | mixed organics |
| `variable-sized periodic/` | 202 | 1–96 | yes | mixed inorganics |

Each directory holds a reference dataset and at least one prediction file. Load
the reference as a dataset and the others as predictions against it.

### `random_prediction.xyz`

Despite the name, these are **not** random: each is the reference forces with a
small perturbation added, so it behaves like a near-perfect model. It is the
control for the low-error end of the scale — its error distributions should be
narrow and featureless. Measured against its reference, the perturbed file
scores a force MAE of roughly 0.06 eV/Å for the periodic carbon set, against
0.97 for the model prediction in the same directory.

## Per-directory detail

### `fixed-sized molecular/`

Stachyose (C24H42O21, 87 atoms), 273 frames sampled from MD22.

- `md22_stachyose_sampled.xyz` — reference
- `predictions_md22_stachyose.xyz` — model prediction
- `random_prediction.xyz` — perturbed-reference control
- **Source:** MD22, http://www.sgdml.org/#datasets — Chmiela et al.
- **Licence:** TODO — check the MD22 terms and record them here
- **Sampling:** TODO — how the 273 frames were chosen

### `fixed-sized periodic/`

A 216-atom carbon cell, 100 frames.

- `am26_subbed_100.extxyz` — reference. Energies are under `energy_per_at`, not
  `energy`, so the key-selection dialog appears on load.
- `predictions_am26_subbed.xyz` — model prediction. **Known problem:** its
  forces average about half the magnitude of the reference (0.51 against 1.07
  eV/Å), which looks more like a units or normalisation error than a weak model.
  Do not use this pair as a reference point until that is resolved.
- `random_prediction.xyz` — perturbed-reference control
- **Source:** TODO — what "am26" is and where it came from
- **Licence:** TODO
- **Sampling:** 100 frames, subsampled ("subbed") from a longer trajectory

### `fixed-sized subsystem/`

Graphene with an adsorbate (C106H6N2, 114 atoms), 100 frames. The subsystem case:
the interesting error is on a handful of atoms, not the sheet.

- `graphene_sampled.xyz` — reference
- `graphene_prediction.npz` — prediction in sGDML `.npz` form (`R`, `E`, `F`,
  `z`, `lattice`, `unit_cell`), which also makes this the example of the npz
  loader path
- **Source:** TODO
- **Licence:** TODO
- **Sampling:** TODO

### `variable-sized molecular/`

Mixed organic molecules, 4 to 50 atoms, 100 frames. The variable-size case:
every configuration has a different atom count, which FFAST detects on load.

- `dataset.xyz` — reference. Also used by `examples/headless/headless.py`, the
  test suite and the demo `ffast.toml` at the repository root, so its path is
  referenced from code — moving it breaks those.
- `prediction.xyz` — model prediction
- **Source:** TODO
- **Licence:** TODO
- **Sampling:** TODO

### `variable-sized periodic/`

Mixed inorganic structures from MPtrj, 1 to 96 atoms, 202 frames. Variable size
*and* periodic, which is the hardest combination for anything that assumes a
fixed cell or a fixed atom count.

- `mptrj_sampled_over_200.extxyz` — reference
- `predictions_mptrj_sampled.xyz` — model prediction
- `random_prediction.xyz` — perturbed-reference control
- **Source:** MPtrj (Materials Project trajectories), as used to train CHGNet
- **Licence:** TODO — MPtrj is distributed under CC BY 4.0; confirm and record
  the attribution this repository owes
- **Sampling:** 202 frames with more than 200 atoms in the source structure,
  per the filename — confirm

## Licensing

The MIT licence in this repository covers FFAST's own code. It does **not**
cover the files in this directory, which are samples of datasets published by
other people under their own terms. Each file keeps whatever licence its source
carries, and any use of them owes attribution to the original authors.

If you are adding a file here, record its source, licence and how you sampled it
in this README at the same time. A dataset with no provenance is not usable by
anyone else, and republishing one is not something this repository can license
on the original author's behalf.
