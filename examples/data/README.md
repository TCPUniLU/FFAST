# Example datasets and predictions

This directory contains small atomistic datasets and associated prediction
files for trying FFAST's loading, visualisation, and error-analysis features.

These are demonstration samples, not complete copies of the upstream
benchmarks. Results obtained from them should not be presented as results
on the complete upstream datasets.

## Licensing and provenance

FFAST's software licence does not replace the licences or permissions
applicable to third-party data.

Dataset sources, modifications, and applicable terms are documented below.
Upstream notices must be retained where required. Inclusion here does not
imply endorsement by the original dataset authors.

Files whose redistribution permission remains unresolved must not be
included in a public release.

## Summary

| Example | Upstream dataset | Selection | Redistribution status |
| --- | --- | --- | --- |
| Fixed-size molecular | MD22 Stachyose | 100 configurations, stride 100 | Pending confirmation for the source data |
| Fixed-size periodic | AM26, amorphous carbon | First 100 configurations of the downloaded source file | Pending permission/licence |
| Fixed-size subsystem | Organic molecule and graphene | One configuration every 150 frames | Origin and permission unresolved |
| Variable-size molecular | SPICE | Existing 100-configuration sample; method unknown | Upstream CC0 statement; archive metadata discrepancy noted below |
| Variable-size periodic | MPtrj via MACE | Whole randomly selected files appended until count exceeded 200 | Underlying Materials Project data: CC BY 4.0; retain source-package notices |

## Fixed-size molecular: MD22 Stachyose

Files:
- `md22_stachyose_sampled.xyz`
- `predictions_md22_stachyose.xyz`
- `random_prediction.xyz`

Source: MD22 Stachyose, downloaded from https://www.sgdml.org/.

Original authors: Stefan Chmiela, Valentin Vassilev-Galindo, Oliver T.
Unke, Adil Kabylda, Huziel E. Sauceda, Alexandre Tkatchenko, and
Klaus-Robert Müller.

Selection: 100 configurations selected at a stride of 100 source records (starting from index `0`).

Source filename/version/checksum: [md22_stachyose.zip](http://sgdml.org?datasetID=md22_stachyose).

Changes: Subsampling and export to XYZ.

Licence/permission: Not yet confirmed for the source download.
Do not redistribute these files until this entry is updated with
the applicable licence or permission.

Scientific reference: [1].

## Fixed-size periodic: AM26 amorphous carbon

Files:
- `am26_subbed_100.extxyz`
- `predictions_am26_subbed.xyz`
- `random_prediction.xyz`

Source: https://github.com/vldgroup/AM26
Original authors: Natascia L. Fragapane and Volker L. Deringer.

This example contains only amorphous-carbon configurations from AM26.
It does not represent all chemical systems in the full benchmark.
The upstream repository identifies its amorphous-carbon structures as
generated using C-GAP-17.

Selection: First 100 configurations of the downloaded source file.
Source filename, repository commit, and checksum: [am26.extxyz](https://github.com/vldgroup/AM26/blob/main/data/am26.extxyz) (Initial commit: `
e88472a`).

Changes: Subsampling and export to extended XYZ.

Licence/permission: Redistribution permission has not yet been established.
Do not redistribute these files until the rights holder supplies applicable
terms or permission.

Scientific references: [2, 3].

## Fixed-size subsystem: organic molecule and graphene

Files:
- `graphene_sampled.xyz`
- `graphene_prediction.npz`

Source: Supplied by the project supervisor; original provenance unresolved.

Selection: One configuration every 150 source configurations (starting from index `0`).
Starting index, source filename, and output count: [record].

Changes: Subsampling and [record any conversion or field changes].

Licence/permission: Unresolved.
Do not redistribute these files until their source and permission
have been established.

## Variable-size molecular: SPICE

Files:
- `dataset.xyz`
- `prediction.xyz`

### Source and sampling

`dataset.xyz` contains 100 configurations sampled from SPICE version 1.1.2
and distributed here in XYZ format. The original sampling procedure was
not recorded. The configurations carry SPICE's own `config_type` values (`PubChem`, `DES370K`) alongside smiles, `total_charge` and `REF_energy`.

Dataset release:
https://doi.org/10.5281/zenodo.7338495

Official project:
https://github.com/openmm/spice-dataset

SPICE version 1.1.2:
https://doi.org/10.5281/zenodo.7338495

`prediction.xyz` contains predictions corresponding to this sample.

### Licence

The original SPICE data are used and redistributed in reliance on the
SPICE authors' explicit statement in the official repository:

"All data in the SPICE dataset may be used under the public domain
equivalent CC0 license."

CC0 1.0 Universal:
https://creativecommons.org/publicdomain/zero/1.0/

The Zenodo release record displays CC BY 4.0 (for version 1.1.2), whereas the official project
README states CC0 for all SPICE data. This distribution relies on the
authors' CC0 statement.

The sample has been selected from the original dataset and exported to XYZ.
The accompanying predictions are separate from the original SPICE
reference data.

Scientific reference: [4, 5].

## Variable-size periodic: MPtrj through MACE

Files:
- `mptrj_sampled_over_200.extxyz`
- `predictions_mptrj_sampled.xyz`
- `random_prediction.xyz`

Source distribution:
https://github.com/ACEsuit/mace-foundations

Exact training-data release/archive and checksum: https://github.com/ACEsuit/mace-mp/releases/download/mace_mp_0/training_data.zip (Release: `mace-mp-0`, last commit hash of the release: `a2e6dec 
`).

Underlying data: Materials Project Trajectory (MPtrj) dataset, compiled
by Bowen Deng from Materials Project calculation trajectories.


Selection: Source extended-XYZ files were selected randomly at file level.
All configurations in each selected file were appended, preserving their
within-file order, until the accumulated count exceeded 200.

Changes: File-level selection and concatenation into extended XYZ.

### MPtrj — licence and attribution

This example is a subset of the Materials Project Trajectory (MPtrj)
dataset, compiled by Bowen Deng from Materials Project calculation
trajectories.

The original dataset is published under the MIT licence:
https://doi.org/10.6084/m9.figshare.23713842

We downloaded the MPtrj training-data distribution provided through:
https://github.com/ACEsuit/mace-foundations

The upstream MIT license text and any accompanying copyright notices
are provided below.

<details>
<summary>MIT license text</summary>

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

</details>

Scientific reference: [6].

MACE is the source of the downloaded repackaging; the prediction files
were generated with SchNet, not with a pretrained MACE model.

## Prediction files and perturbed-reference controls

The newly added model-prediction files were generated by [Amirarsalan](https://github.com/Amirarsalan-sn) by training and using a simple SchNet models.

The existing SPICE prediction file has separate provenance (available since FFAST v1.0).

Files named `random_prediction.xyz` are perturbed-reference controls:
reference values plus noise. They are not independent model predictions.

Prediction and control files may retain structures and metadata from their
source datasets. Applicable upstream notices remain relevant to those
copied components.

These files are intended for demonstrating FFAST, not for establishing
model accuracy or suitability for molecular dynamics.

## References

[1] Chmiela, S. et al. Accurate global machine learning force fields for
molecules with hundreds of atoms. Science Advances 9, eadf0873 (2023).
https://doi.org/10.1126/sciadv.adf0873

[2] Fragapane, N. L. and Deringer, V. L. Amorphous materials as a frontier
challenge for universal interatomic potentials. arXiv:2607.11384 (2026).
https://arxiv.org/abs/2607.11384
Data: https://github.com/vldgroup/AM26

[3] C-GAP-17: upstream method used to generate AM26 amorphous-carbon
structures.
https://doi.org/10.1103/PhysRevB.95.094203

[4] Peter Eastman, Pavan Kumar Behara, David L. Dotson, Raimondas Galvelis,
John E. Herr, Josh T. Horton, Yuezhi Mao, John D. Chodera,
Benjamin P. Pritchard, Yuanqing Wang, Gianni De Fabritiis, and
Thomas E. Markland.
"SPICE, A Dataset of Drug-like Molecules and Peptides for Training
Machine Learning Potentials."
Scientific Data 10, 11 (2023).
https://doi.org/10.1038/s41597-022-01882-6

[5] Peter Eastman, Benjamin P. Pritchard, John D. Chodera, and
Thomas E. Markland.
"Nutmeg and SPICE: Models and Data for Biomolecular Machine Learning."
Journal of Chemical Theory and Computation 20(19), 8583–8593 (2024).
https://doi.org/10.1021/acs.jctc.4c00794

[6] Deng, Bowen; Zhong, Peichen; Jun, KyuJung; Riebesell, Janosh;
Han, Kevin; Bartel, Christopher J.; and Ceder, Gerbrand.
"CHGNet as a pretrained universal neural network potential for
charge-informed atomistic modelling."
Nature Machine Intelligence (2023).
https://doi.org/10.1038/s42256-023-00716-3