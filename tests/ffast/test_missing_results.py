"""
A file without forces or energies says so; it never invents zeros
(ADR 0058, section 1). "Says so" means the same RuntimeError the uniform
loader raises today, which callers already catch.
"""
import numpy as np
import pytest
from ase import Atoms

from ffast.core.loading_coordinator import LoadingCoordinator



def _uniform():
    return [Atoms("H2O", positions=[[0, 0, 0], [0, 0, 1], [1, 0, 0]]) for _ in range(3)]

def _variable():
    return [Atoms("H2O", positions=np.zeros((3, 3))), Atoms("H2", positions=np.zeros((2, 3))), Atoms("He", positions=np.zeros((1, 3)))]


@pytest.mark.parametrize("make", [_uniform, _variable], ids=["uniform", "variable"])
@pytest.mark.parametrize("ask", ["getForces", "getEnergies"])
def test_missing_results_are_absent_not_zeros(make, ask):
    """A file without forces or energies says so; it never invents zeros."""
    dataset = LoadingCoordinator._aseLoaderFor("none.xyz", make())
    dataset.initialise()
    with pytest.raises(RuntimeError):
        getattr(dataset, ask)(0)