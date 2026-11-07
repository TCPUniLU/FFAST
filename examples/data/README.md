# Example datasets and predictions

These small samples demonstrate FFAST loading, visualisation and error analysis.
They are not the full upstream benchmarks. Model predictions may be inaccurate;
the reference and prediction must nevertheless compare the same quantities in
matching units. Every bundled pair works with default energy and force selection.

## Bundled examples

| Folder | Reference | Frames | Atoms per frame | Energy | Force |
| --- | --- | --- | --- | --- | --- |
| `fixed-sized molecular/` | MD22 Stachyose | 273 | 87 | kcal/mol | kcal/(mol·Å) |
| `variable-sized molecular/` | SPICE | 100 | 4–50 | eV | eV/Å |
| `variable-sized periodic/` | MPtrj | 202 | 1–96 | eV | eV/Å |

Each folder contains its reference and matching model prediction. MD22 and
MPtrj also contain `reference_plus_noise.xyz`, labelled **reference + noise**:
these are perturbed-reference controls, not independently trained models.
They retain the reference structures and use the same units as their reference.

**Load Example** opens the SPICE pair in `variable-sized molecular/`. The
headless example and tests use that folder too. There are no duplicate SPICE
files at the root of `examples/data/`. Quote paths with spaces in shell commands:

```sh
ffast-cli dataset keys "examples/data/variable-sized molecular/dataset.xyz"
```

FFAST reads the stored values directly. Unit labels do not convert numbers.
Different examples may use different units; each bundled pair is internally
consistent. No unit conversions were applied during this publication.

## MD22 Stachyose: `fixed-sized molecular/`

Files:

- `md22_stachyose_sampled.xyz`: reference.
- `predictions_md22_stachyose.xyz`: SchNet prediction.
- `reference_plus_noise.xyz`: reference + noise.

Source: [MD22 at sgdml.org](https://www.sgdml.org/),
[Stachyose download](https://www.sgdml.org/?datasetID=md22_stachyose).
Authors: Stefan Chmiela, Valentin Vassilev-Galindo, Oliver T. Unke,
Adil Kabylda, Huziel E. Sauceda, Alexandre Tkatchenko and Klaus-Robert Müller.

Sampling: every 100th source configuration starting at index 0, giving
273 configurations of 87 atoms (`C24H42O21`). Exported to extended XYZ.
Reference, model prediction and control contain `energy` and `forces` in
kcal/mol and kcal/(mol·Å). Their numerical values are unchanged from `dev`.

Permission: redistribution permission obtained from the authors, confirmed by
Anton Charkin-Gorbulin on 2026-11-07. This permission does not relicense the
source dataset under FFAST's software licence. Retain this attribution.

Reference: Chmiela et al., *Accurate global machine learning force fields for
molecules with hundreds of atoms*, Science Advances 9, eadf0873 (2023).
[doi:10.1126/sciadv.adf0873](https://doi.org/10.1126/sciadv.adf0873).

## SPICE: `variable-sized molecular/`

Files:

- `dataset.xyz`: reference, `REF_energy` and `REF_forces`.
- `prediction.xyz`: model prediction, `MACE_energy` and `MACE_forces`.

Source: the [SPICE dataset](https://github.com/openmm/spice-dataset).
Sampling: random selection of 100 configurations, distributed as extended XYZ.
The precise upstream release is unconfirmed; the earlier assumption of version
1.1.2 is not treated as an identification. The sampled files already contain
energies in eV and forces in eV/Å; they are retained without numerical changes.
The prediction pair has been included since FFAST v1.0.

The different reference and prediction field names are unambiguous in their
respective files, so the default loader selects the right values. `smiles`,
`total_charge` and SPICE `config_type` metadata are preserved. The project
Dataset Fields demo continues to expose `REF_energy` and `total_charge`.

Licence: CC0 1.0 Universal, relying on the SPICE authors' explicit
[data-licence statement](https://github.com/openmm/spice-dataset#spice-a-dataset-for-training-machine-learning-potentials).
[CC0 terms](https://creativecommons.org/publicdomain/zero/1.0/).

References:

- Eastman et al., *SPICE, A Dataset of Drug-like Molecules and Peptides for
  Training Machine Learning Potentials*, Scientific Data 10, 11 (2023).
  [doi:10.1038/s41597-022-01882-6](https://doi.org/10.1038/s41597-022-01882-6).
- Eastman et al., *Nutmeg and SPICE: Models and Data for Biomolecular Machine
  Learning*, JCTC 20, 8583–8593 (2024).
  [doi:10.1021/acs.jctc.4c00794](https://doi.org/10.1021/acs.jctc.4c00794).

## MPtrj: `variable-sized periodic/`

Files:

- `mptrj_sampled_over_200.extxyz`: reference.
- `predictions_mptrj_sampled.xyz`: SchNet prediction.
- `reference_plus_noise.xyz`: reference + noise.

Source: Materials Project Trajectory (MPtrj), compiled by Bowen Deng from
Materials Project calculation trajectories. Original dataset:
[Figshare 23713842](https://doi.org/10.6084/m9.figshare.23713842).
Downloaded through the [MACE foundation-model data distribution](https://github.com/ACEsuit/mace-foundations):
[training_data.zip](https://github.com/ACEsuit/mace-mp/releases/download/mace_mp_0/training_data.zip),
release `mace-mp-0`, recorded commit `a2e6dec`.
MACE supplied the repackaging; these prediction files were generated with
SchNet, not a pretrained MACE model.

Sampling: randomly select source files and append all their configurations in
within-file order until the count exceeds 200. This sample contains 202
configurations with 1–96 atoms per frame. `mp_id`, `task_id`, `calc_id` and
`ionic_step` are retained to identify the source records.

The intended comparison is total `energy` in eV and `forces` in eV/Å.
For publication, `energy_per_atom` and `corrected_total_energy` were removed
from **both** the reference and model-prediction files. These redundant or
inconsistent auxiliary fields could be selected ahead of the intended total
energy. In particular, the prediction's `corrected_total_energy` held a
per-atom value after the earlier `dev` correction. Removing these fields
makes default loading correct. Positions, elements, cells, total energies,
forces, stresses and other metadata were retained; no numerical values were
converted or rescaled. The control values are unchanged; its file is renamed from
`random_prediction.xyz` to `reference_plus_noise.xyz`.

Licence: **MIT**, as stated on the original
[Figshare record](https://doi.org/10.6084/m9.figshare.23713842), which also
specifies eV for total energies and eV/Å for forces. The former CC BY summary
was incorrect for this source record. Retain the attribution and MIT notice.

Attribution: Deng, Bowen (2023), *Materials Project Trajectory (MPtrj) Dataset*,
Figshare. Scientific reference: Deng et al., *CHGNet as a pretrained universal
neural network potential for charge-informed atomistic modelling*,
Nature Machine Intelligence (2023).
[doi:10.1038/s42256-023-00716-3](https://doi.org/10.1038/s42256-023-00716-3).

## Prediction provenance and preparation

Amirarsalan Sanati generated the MD22 and MPtrj model predictions using simple
SchNet models and supplied the perturbed-reference controls. The SPICE
prediction has separate provenance from FFAST v1.0. These files demonstrate
the application; they do not establish performance on the complete datasets.

The source files for this publication are from `dev` commit
`44dc0ca5d0856dd042c656dd8bb31e61eec62683`. The only data edits are the removal
of the two auxiliary MPtrj energy fields described above. Controls are renamed
from `random_prediction.xyz` to `reference_plus_noise.xyz` so their file names
also identify their origin. The other bundled
files are byte-for-byte copies of those source samples, relocated where needed.

FFAST's MIT software licence does not replace data permissions or licences.
Any later changes to sampling, fields or units must be recorded here.

## Deferred examples

These files remain on `dev` and are not bundled in this publication:

- **AM26 amorphous carbon:** redistribution permission is still pending.
  Source: [Fragapane and Deringer's AM26](https://github.com/vldgroup/AM26).
- **Graphene system:** redistribution permission has been obtained. Source:
  Charkin-Gorbulin et al., *Atomic orbits in molecules and materials for
  improving machine learning force fields*, MLST 6, 035005 (2025),
  [doi:10.1088/2632-2153/adea0b](https://doi.org/10.1088/2632-2153/adea0b),
  [data archive](https://doi.org/10.5281/zenodo.15041256).
  The stored reference and prediction show a scale difference consistent with
  an eV/kcal/mol mismatch, but their source units have not been established.
  Add the pair only once its units are verified and aligned; a fitted scale
  factor alone is not sufficient evidence for conversion.

## MPtrj MIT notice

```text
MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the “Software”), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
