# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

Scope of this record: the browser client (`ffast/renderers/web/`). The Qt desktop
client keeps its own look and is out of scope for visual work.

## Users

Two groups, served equally, with no mode switch between them:

- **Force-field developers**: ML researchers training and comparing models. Long
  sessions, often against data on an HPC cluster. They need exact numbers they can
  quote.
- **Computational chemists**: people using a trained model who need to decide
  whether it can be trusted for their molecule. Less ML-specific vocabulary.

The shared job: find out where a machine-learned force field is wrong, and what the
molecule was doing when it went wrong.

## Product Purpose

FFAST takes a dataset and a set of predictions and shows the error along the
trajectory, the error distributions, and per-element and per-structure breakdowns.
Success is the loop "spot it in a plot, look at it in 3D": an odd region in a plot
opens the offending configurations in the 3D viewer, force vectors included.

## Positioning

Papers report one MAE; two models with the same MAE can fail in completely
different places. FFAST shows the distribution and lets you go to the outliers.
The compute runs where the data is (usually a cluster); only selected
configurations come back to the user's machine.

## Operating Context

- Server runs locally or on a cluster (SLURM job submitted and installed on first
  connect); the browser client talks to it over WebSocket.
- Several browser windows can share one server session.
- Screenshots of the 3D view and plots go into papers and talks.

## Capabilities and Constraints

- Datasets and predictions (one prediction belongs to one dataset), metrics
  (about 45 built-in plus user expression metrics), analysis tabs defined in TOML,
  3D pick tools (Info, Bonds, Align, Force, Extract), subsets, sessions.
- Energies and forces carry physical units (e.g. eV, eV/Å); numbers must be shown
  with their unit and enough precision to compare models.
- The browser client is plain HTML/CSS/JS modules with no build step.

## Brand Commitments

- Name: FFAST (Force Field Analysis and Screening Tool).
- Dark theme stays the default (long sessions on 3D data).
- Output must look right in papers and talks: exported or projected 3D views and
  plots need to read well outside the app.

## Evidence on Hand

- Example data: `examples/data/dataset.xyz`, `examples/data/prediction.xyz`.
- Hero screenshot: `docs/images/hero.png`.
- No user testimonials, benchmarks or adoption figures exist; do not invent any.

## Product Principles

1. The error is the subject. Every screen should make it easier to see where and
   how much the model is wrong.
2. Numbers are quotable: unit, precision and provenance are always visible.
3. Plot and molecule are one loop; moving between them should be one action.
4. Serve the developer and the chemist from the same screen: depth on demand, not
   by default.

## Accessibility & Inclusion

- Error meaning must not rely on hue alone (colour-blind users, greyscale print).
- Keyboard operable; text contrast at WCAG AA.
