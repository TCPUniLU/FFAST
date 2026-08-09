# Example data

## What is here now

`dataset.xyz` and `prediction.xyz` — 100 configurations of mixed organic
molecules, 4 to 50 atoms each, so atom count varies between configurations. Load
the first as a dataset and the second as a prediction against it.

This pair is load-bearing: `examples/headless/headless.py`, the demo `ffast.toml`
at the repository root and several tests refer to it by path, so moving or
renaming it breaks them.

- **Source:** the [SPICE dataset](https://github.com/openmm/spice-dataset) — the
  configurations carry SPICE's own `config_type` values (`PubChem`, `DES370K`)
  alongside `smiles`, `total_charge` and `REF_energy`.
- **Licence:** CC0 1.0 (public domain dedication), per the
  [Zenodo record](https://doi.org/10.5281/zenodo.7338495). No restriction on
  redistribution; citing Eastman et al., *Sci. Data* **9**, 11 (2022) is
  courtesy rather than obligation.
- **Sampling:** TODO — how these 100 configurations were drawn from SPICE

## What is coming

A wider corpus is prepared but not published yet: fixed-sized and
variable-sized systems, molecular, periodic and subsystem, each with a reference
dataset, a model prediction, and a perturbed-reference control for the low-error
end of the scale. Together they cover the cases that behave differently, which
makes them useful for checking a change rather than only for a first look.

It is held back because those files are samples of datasets that other people
published — MD22, MPtrj and others — and their provenance and licence terms have
to be established before this repository can redistribute them. The MIT licence
here covers FFAST's code; it cannot relicense someone else's data, and
republishing a dataset with no attribution is not something to do by accident.

What the files' own metadata says about where they came from:

| File | Source | Licence | Confidence |
|---|---|---|---|
| `mptrj_sampled_over_200.extxyz` | MPtrj (Materials Project trajectories), from its `mp_id` / `task_id` / `calc_id` / `ionic_step` keys | CC BY 4.0, plus the Materials Project terms of use — **attribution required** | Certain |
| `md22_stachyose_sampled.xyz` | MD22, sgdml.org | **None stated.** sgdml.org publishes no licence for its datasets; its terms cover uploads to the site, not downloads from it | Source certain, terms unclear |
| `am26_subbed_100.extxyz` | Very likely the amorphous-carbon set at [Zenodo 7905585](https://doi.org/10.5281/zenodo.7905585) (Minamitani, Osaka University): 216 atoms, melt-quench, densities including 1.5 g/cm³, which matches this file's `label=carbon-mq_1.5_N` and `system=carbon` | CC BY 4.0 — **attribution required** | Strong but circumstantial; confirm before relying on it |
| `graphene_sampled.xyz` | Unknown — the file carries no identifying metadata. Possibly generated in-house | Unknown | — |

Outstanding before any of it ships:

- Confirm the am26 identification, and settle what MD22 permits — the honest
  options there are to ask the authors, or to link to sgdml.org and have users
  download it themselves rather than redistributing it here.
- Add the attribution CC BY asks for, for every file that turns out to need it.
- How each sample was drawn from its source, so the numbers are reproducible.
- One prediction file needs checking: its forces average about half the
  magnitude of the corresponding reference, which reads as a units or
  normalisation error rather than a weak model.

If you are adding data here, record source, licence and sampling in this file at
the same time. A dataset with no provenance is not usable by anyone else.
