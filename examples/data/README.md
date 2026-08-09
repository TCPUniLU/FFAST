# Example data

## What is here now

`dataset.xyz` and `prediction.xyz` — 100 configurations of mixed organic
molecules, 4 to 50 atoms each, so atom count varies between configurations. Load
the first as a dataset and the second as a prediction against it.

This pair is load-bearing: `examples/headless/headless.py`, the demo `ffast.toml`
at the repository root and several tests refer to it by path, so moving or
renaming it breaks them.

- **Source:** TODO
- **Licence:** TODO
- **Sampling:** TODO

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

Outstanding before any of it ships:

- Source and licence for each file, recorded here, with the attribution the
  original terms require. MPtrj is distributed under CC BY, which asks for
  specific credit.
- How each sample was drawn from its source, so the numbers are reproducible.
- One prediction file needs checking: its forces average about half the
  magnitude of the corresponding reference, which reads as a units or
  normalisation error rather than a weak model.

If you are adding data here, record source, licence and sampling in this file at
the same time. A dataset with no provenance is not usable by anyone else.
