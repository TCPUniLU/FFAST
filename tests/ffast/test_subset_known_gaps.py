"""
Open bugs in subsets, kept as expected failures until the shared dataset
interface lands.

Each test fails today with the error named in its marker. When a bug is
fixed, strict mode turns its test red: remove the marker, add a real
assert, and move the test to test_dataset_frame_signatures.py.
"""
import numpy as np
import pytest
from ffast.loaders.dataset import AtomFilteredDataset, SubDataset, VariableDatasetLoader
from ffast.protocol.rpc import pack_arrays
from ffast.core.environment import Environment
import types

COUNTS = [3, 1, 2, 5]
FRAMES = [3, 0]
ATOMS = [0, 2]


class _Variable(VariableDatasetLoader):
    def __init__(self):
        super().__init__("variable.xyz")
        rng = np.random.default_rng(0)
        self.molecule_offsets = np.concatenate([[0], np.cumsum(COUNTS)])
        total = int(self.molecule_offsets[-1])
        self.R_flat = rng.normal(size=(total, 3))
        self.F_flat = rng.normal(size=(total, 3))
        self.z_flat = np.array([1, 6, 8, 7, 1, 1, 6, 6, 8, 8, 16])
        self.E = rng.normal(size=len(COUNTS))
        self.N = len(COUNTS)
        self.name = "variable"
        self.fingerprint = "parent-fp"
        self.loaded = True


@pytest.fixture
def parent():
    dataset = _Variable()
    dataset.initialise()
    return dataset


@pytest.fixture
def sub(parent):
    s = SubDataset(parent, None, np.array(FRAMES), "Subset")
    s.initialise()
    return s


@pytest.fixture
def atom_sub(parent):
    s = AtomFilteredDataset(parent, np.array(ATOMS))
    s.initialise()
    return s

@pytest.mark.xfail(raises=TypeError, reason="atom subset of a variable parent has no bond sizes", strict=True)
def test_atom_subset_of_a_variable_parent_finds_bonds(atom_sub):
    atom_sub.getBondMatrix(0)

@pytest.mark.xfail(raises=ValueError, reason="A frame subset of a variable dataset sends its arrays to the client as a list of differently-sized arrays, which cannot be packed", strict=True)
def test_subset_of_a_variable_parent_packs_its_arrays(sub):
    pack_arrays("fp", sub.to_transfer_arrays())

@pytest.mark.xfail(raises=AttributeError, reason="Freezing an atom subset reads modelDep, which atom subsets don't have", strict=True)
def test_atom_subset_cannot_be_frozen_by_mistake(atom_sub):
    env = types.SimpleNamespace(datasets=types.SimpleNamespace(get=lambda fp: atom_sub))
    Environment.freezeSubDataset(env, "x")
