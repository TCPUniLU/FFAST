"""Bonds come from each structure's own elements, on every kind of file
(ADR 0058, decision 1). Uniform files used to keep one bond-size table built
from frame 0; now both kinds compute it per structure."""
import numpy as np
import pytest
from ase import Atoms

from ffast.loaders.ase import aseDatasetLoader

WATER = Atoms("OH2", positions=[[0, 0, 0], [0.96, 0, 0], [-0.24, 0.93, 0]])
METHANE = Atoms("CH4", positions=[[0, 0, 0], [0.63, 0.63, 0.63], [-0.63, -0.63, 0.63],
                                  [-0.63, 0.63, -0.63], [0.63, -0.63, -0.63]])


@pytest.mark.parametrize("frames", [[WATER, WATER], [METHANE, WATER]], ids=["uniform", "variable"])
def test_each_structure_is_bonded_by_its_own_elements(frames):
    dataset = aseDatasetLoader("b.xyz", atomsList=[a.copy() for a in frames])
    dataset.initialise()

    water = dataset.getBondMatrix(1)
    assert water[0, 1] and water[0, 2]          # O-H
    assert not water[1, 2]                      # H...H, 1.5 Å apart
