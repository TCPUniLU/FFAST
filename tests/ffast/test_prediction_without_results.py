"""
Predictions load when the dataset or the prediction lacks energies or forces.

ADR 0058 makes an absent result absent (a RuntimeError) on both dataset kinds,
where the variable loader used to invent zeros. Two spots in the Loading
Coordinator assumed both results were always there:

- In-file prediction columns (``_loadPredictionsFromKeys``) asked for energies
  and forces together, so a column with forces but no energies was skipped
  whole. A standalone prediction file already loads "forces only".
- The frame-count check (``_predictionMatchesDataset``) read the *dataset's*
  energies to count its frames, so a prediction could not be attached to a
  dataset of bare geometries.
"""
import types

import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.core.loading_coordinator import LoadingCoordinator

KINDS = ["uniform", "variable"]


def _bare(kind):
    """Three structures with positions only: no energies, no forces."""
    if kind == "uniform":
        return [Atoms("H2O", positions=np.zeros((3, 3))) for _ in range(3)]
    return [Atoms("H2O", positions=np.zeros((3, 3))),
            Atoms("H2", positions=np.zeros((2, 3))),
            Atoms("H2O", positions=np.zeros((3, 3)))]


def _forces_only(kind):
    frames = _bare(kind)
    for atoms in frames:
        atoms.calc = SinglePointCalculator(atoms, forces=np.ones((len(atoms), 3)))
    return frames


@pytest.mark.parametrize("kind", KINDS)
def test_prediction_fits_a_dataset_without_energies(kind):
    dataset = LoadingCoordinator._aseLoaderFor("ref.xyz", _bare(kind))
    assert LoadingCoordinator._predictionMatchesDataset(np.arange(3.0), dataset, "model")


@pytest.mark.parametrize("kind", KINDS)
def test_prediction_with_the_wrong_frame_count_is_refused(kind):
    """The fix must still refuse a mis-picked file, not accept everything."""
    dataset = LoadingCoordinator._aseLoaderFor("ref.xyz", _bare(kind))
    assert not LoadingCoordinator._predictionMatchesDataset(np.arange(5.0), dataset, "model")


@pytest.mark.parametrize("kind", KINDS)
def test_force_only_prediction_column_loads(kind, monkeypatch):
    coordinator = LoadingCoordinator(types.SimpleNamespace())
    ingested = []

    def record(dataset, E, F, **kwargs):  # stands in for the real ingest
        ingested.append((E, F))
        return True

    monkeypatch.setattr(coordinator, "_ingestPrediction", record)
    coordinator._loadPredictionsFromKeys(
        None, "pred.xyz", [("", "", "model")], atomsList=_forces_only(kind))

    assert len(ingested) == 1, "the column was skipped"
    E, F = ingested[0]
    assert E is None
    assert F is not None
