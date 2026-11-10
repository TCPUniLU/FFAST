# LAMMPS tutorial examples

`recorded-alarm/` contains only the dump, LAMMPS log and include information
from `~/ffast-live-demo/cluster-runs/20261106-105509-mel0516-24400-6ff5`,
recorded on an HPC cluster with LAMMPS 2 Aug 2023 Update 2 on 2026-11-06.
It is the S4 alarm-without-stop proof: toy aspirin at 300 K for 10,000 steps,
then 3000 K. Replay uses the real wrapper and scorer; no scores ship here.
The historical paths in the log and include are evidence from the recording;
the replay replaces the bundle path with this copy's `toy.bundle`.

`toy.bundle/` is the DetectAna 0.3.0 reference from the same demo's
`references/toy.bundle` (manifest records its molecule, fit and calibration).
The launcher registers a working copy as **toy aspirin (tutorial)** and writes
its local include beside that copy under the practice-runs folder.

`aspirin.data` is copied from
`docs/research/lammps-live-2026-11-04/meluxina-trial/aspirin.data`, written for
a small LAMMPS test run. Its aspirin geometry comes from the MD17 dataset:

- S. Chmiela, A. Tkatchenko, H. E. Sauceda, I. Poltavsky, K. T. Schütt and
  K.-R. Müller, "Machine learning of accurate energy-conserving molecular force
  fields", *Science Advances* 3, e1603015 (2017),
  [doi:10.1126/sciadv.1603015](https://doi.org/10.1126/sciadv.1603015).
  Data: [sgdml.org](http://www.sgdml.org/#datasets).

`tutorial/in.tutorial` is the toy input for the second tutorial: Lennard-Jones,
a spring tether and Langevin heating, with tutorial-only pacing and a GO/STOP
wait. Its pacing and GO/STOP wait are verified on conda-forge LAMMPS
22 Jul 2025 Update 6 (serial and two MPICH processes); see
[the checks](../../docs/research/lammps-tutorials-2026-11-08/lammps-check.md). Open it with Run with FFAST to add monitoring;
that dialog also aligns the data's atom types with the reference.

`tutorial.json` holds the shared gate step and replay pace. The launcher reads
it when starting a practice run; the UI reads those settings from the run's
record. `FFAST_PRACTICE_SLEEP` changes replay pacing for browser tests.
