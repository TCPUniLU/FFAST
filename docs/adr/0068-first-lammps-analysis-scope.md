# First LAMMPS analysis scope: one nonperiodic molecule

**Status:** Accepted. Superseded in part by ADR 0086 (2026-11-04): live watching
and out-of-distribution scoring are now in scope; the one-molecule,
no-periodic-boundary limit and "DetectAna owns the science" still hold.

The first LAMMPS-to-FFAST analysis path imports a completed local aspirin run
with a fixed atom order and no periodic boundaries. This matches
DetectAna's current chemistry and out-of-distribution methods, so the first
integration can reuse a checked scientific method instead of inventing periodic
bond rules at the import seam. Live monitoring and simulation control are later
work; the first path has no second owner of a running LAMMPS process.

The first analysis reports DetectAna chemistry flags, including broken initial
bonds and close contacts. Calibrated out-of-distribution scoring is deferred: it
requires separate training and validation reference sets and makes a different
scientific claim. A chemistry flag must not be presented as an OOD result.

FFAST calculates these flags on demand through an optional DetectAna adapter.
FFAST owns the request and presentation; DetectAna owns the scientific rules.
If DetectAna is unavailable, the analysis is unavailable with an explicit
explanation. FFAST's dynamic bonds for display are not a fallback detector.

The trajectory record must still retain atom identity, simulation step, cell,
boundary conditions and units when present. Unsupported systems are identified
as such before running the initial detector; a periodic or multi-molecule run
must not receive a plausible-looking result from nonperiodic distance maths.
This keeps later system-specific detectors possible without redefining earlier
results. General periodic and multi-molecule detection is deferred, not ruled
out.
