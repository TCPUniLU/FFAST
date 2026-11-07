"""Published pairs load the intended quantities with no field overrides."""
from pathlib import Path

import ase.io
import numpy as np
import pytest

from ffast.core.data_types import AtomsList
from ffast.loaders.ase import aseDatasetLoader


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "examples" / "data"
PAIRS = [
    ("fixed-sized molecular", "md22_stachyose_sampled.xyz",
     "predictions_md22_stachyose.xyz", 273, None, None),
    ("fixed-sized molecular", "md22_stachyose_sampled.xyz",
     "reference_plus_noise.xyz", 273, None, None),
    ("variable-sized molecular", "dataset.xyz",
     "prediction.xyz", 100, "REF", "MACE"),
    ("variable-sized periodic", "mptrj_sampled_over_200.extxyz",
     "predictions_mptrj_sampled.xyz", 202, None, None),
    ("variable-sized periodic", "mptrj_sampled_over_200.extxyz",
     "reference_plus_noise.xyz", 202, None, None),
]


def expected_arrays(frames, prefix):
    """Independent ASE read of the intended values, before FFAST selection."""
    if prefix:
        energies = np.array([a.info[f"{prefix}_energy"] for a in frames])
        forces = np.concatenate([a.arrays[f"{prefix}_forces"] for a in frames])
    else:
        energies = np.array([a.get_potential_energy() for a in frames])
        forces = np.concatenate([a.get_forces() for a in frames])
    return energies, forces


@pytest.mark.parametrize("folder,reference,prediction,count,ref_prefix,pred_prefix", PAIRS)
def test_pair_defaults_match_intended_quantities(
        folder, reference, prediction, count, ref_prefix, pred_prefix):
    paths = [DATA / folder / name for name in (reference, prediction)]
    frames = [ase.io.read(path, index=":") for path in paths]
    assert len(frames[0]) == len(frames[1]) == count
    for a, b in zip(*frames):
        np.testing.assert_array_equal(a.numbers, b.numbers)
        np.testing.assert_array_equal(a.positions, b.positions)
        np.testing.assert_array_equal(a.pbc, b.pbc)
        np.testing.assert_array_equal(a.cell, b.cell)
    expected = [expected_arrays(f, prefix)
                for f, prefix in zip(frames, (ref_prefix, pred_prefix))]
    for path, (energies, forces) in zip(paths, expected):
        # The actual indexed-file path, with the same default selection as
        # loading through the app. Never pass selected_energy/force_key here.
        loader = aseDatasetLoader(str(path), atomsList=AtomsList(str(path)))
        loader.initialise()
        np.testing.assert_array_equal(loader.getAllEnergies(), energies)
        np.testing.assert_array_equal(loader.getAllForces(), forces)
        assert np.isfinite(energies).all() and np.isfinite(forces).all()
    assert not np.array_equal(expected[0][0], expected[1][0])
    assert not np.array_equal(expected[0][1], expected[1][1])
