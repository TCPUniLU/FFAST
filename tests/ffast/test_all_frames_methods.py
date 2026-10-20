"""
The all-frames methods mean the same on every kind of dataset (ADR 0058, step 4).

Flat arrays with one row per atom, frame after frame, plus offsets saying where
each frame's atoms start. Every kind is checked against its own per-frame
answers, which is what the flat arrays must add up to.
"""
import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import AtomFilteredDataset, SubDataset, stackFrames

UNIFORM = ["H2O"] * 4
VARIABLE = ["H2O", "CH4", "NH3", "C2H6"]


def _ase(formulas, forces=True, energies=True):
    rng = np.random.default_rng(3)
    frames = []
    for formula in formulas:
        atoms = Atoms(formula, positions=rng.normal(size=(len(Atoms(formula)), 3)))
        results = {}
        if energies:
            results["energy"] = float(rng.normal())
        if forces:
            results["forces"] = rng.normal(size=(len(atoms), 3))
        if results:
            atoms.calc = SinglePointCalculator(atoms, **results)
        frames.append(atoms)
    dataset = aseDatasetLoader("x.xyz", atomsList=frames)
    dataset.initialise()
    dataset.loaded = True
    return dataset


def _frame_subset(parent, frames=(3, 0)):
    sub = SubDataset(parent, None, np.array(frames), "Subset")
    sub.initialise()
    return sub


def _atom_subset(parent):
    sub = AtomFilteredDataset(parent, np.array([0, 2]))
    sub.initialise()
    return sub


KINDS = {
    "uniform file": lambda: _ase(UNIFORM),
    "variable file": lambda: _ase(VARIABLE),
    "frame subset of uniform": lambda: _frame_subset(_ase(UNIFORM)),
    "frame subset of variable": lambda: _frame_subset(_ase(VARIABLE)),
    "atom subset of uniform": lambda: _atom_subset(_ase(UNIFORM)),
    "atom subset of variable": lambda: _atom_subset(_ase(VARIABLE)),
    "subset of a subset": lambda: _frame_subset(_frame_subset(_ase(VARIABLE)), frames=(1, 0)),
}


@pytest.fixture(params=KINDS)
def dataset(request):
    return KINDS[request.param]()


def _per_frame(dataset, name):
    return np.concatenate([np.asarray(getattr(dataset, name)(i)) for i in range(dataset.getN())])


def test_offsets_add_up_to_each_frames_atom_count(dataset):
    offsets = dataset.getOffsets()
    assert offsets[0] == 0 and len(offsets) == dataset.getN() + 1
    for i in range(dataset.getN()):
        assert offsets[i + 1] - offsets[i] == len(dataset.getElements(i))


def test_flat_arrays_are_the_frames_one_after_another(dataset):
    assert np.array_equal(dataset.getAllCoordinates(), _per_frame(dataset, "getCoordinates"))
    assert np.array_equal(dataset.getAllForces(), _per_frame(dataset, "getForces"))
    assert np.array_equal(dataset.getAllElements(), _per_frame(dataset, "getElements"))
    assert dataset.getAllEnergies().shape == (dataset.getN(),)
    assert dataset.getAllCoordinates().shape == (dataset.getOffsets()[-1], 3)


def test_flat_arrays_refuse_writes(dataset):
    for array in (dataset.getAllCoordinates(), dataset.getAllForces(),
                  dataset.getAllElements(), dataset.getAllEnergies(), dataset.getOffsets()):
        with pytest.raises(ValueError):
            array[0] = 0


def test_a_file_keeps_its_flat_arrays_instead_of_reading_the_file_again():
    dataset = _ase(VARIABLE)
    first, second = dataset.getAllCoordinates(), dataset.getAllCoordinates()
    assert np.shares_memory(first, second)


def test_stacking_needs_every_structure_the_same_size():
    same = _ase(UNIFORM)
    stacked = stackFrames(same.getAllCoordinates(), same.getOffsets())
    assert stacked.shape == (4, 3, 3)
    assert np.array_equal(stacked[2], same.getCoordinates(2))

    differ = _ase(VARIABLE)
    with pytest.raises(ValueError, match="differ in size"):
        stackFrames(differ.getAllCoordinates(), differ.getOffsets())


def test_stacking_works_on_an_atom_subset_of_a_variable_file():
    sub = _atom_subset(_ase(VARIABLE))
    assert stackFrames(sub.getAllForces(), sub.getOffsets()).shape == (4, 2, 3)


@pytest.mark.parametrize("formulas", [UNIFORM, VARIABLE])
def test_missing_results_raise_instead_of_being_filled_in(formulas):
    dataset = _ase(formulas, forces=False, energies=False)
    with pytest.raises(RuntimeError):
        dataset.getAllForces()
    with pytest.raises(RuntimeError):
        dataset.getAllEnergies()
    sub = _frame_subset(dataset)
    with pytest.raises(RuntimeError):
        sub.getAllForces()


def test_each_frame_answers_its_own_elements_even_when_atoms_are_listed_in_another_order():
    frames = [Atoms("H2O", positions=np.zeros((3, 3))), Atoms("OH2", positions=np.zeros((3, 3)))]
    dataset = aseDatasetLoader("x.xyz", atomsList=frames)
    assert dataset.getElements(0).tolist() == [1, 1, 8]
    assert dataset.getElements(1).tolist() == [8, 1, 1]
    assert dataset.getAllElements().tolist() == [1, 1, 8, 8, 1, 1]


def test_models_get_one_3d_array_and_one_structures_elements():
    from ffast.loaders.model import uniformFrames

    dataset = _ase(UNIFORM)
    R, z = uniformFrames(dataset)
    assert R.shape == (4, 3, 3) and z.tolist() == [1, 1, 8]
    assert np.array_equal(R[2], dataset.getCoordinates(2))

    R, z = uniformFrames(dataset, indices=[3, 1])
    assert np.array_equal(R, dataset.getCoordinates([3, 1]))


def test_models_refuse_structures_of_different_sizes():
    from ffast.loaders.model import uniformFrames

    with pytest.raises(ValueError, match="differ in size"):
        uniformFrames(_ase(VARIABLE))


def test_models_accept_a_dataset_built_from_an_sgdml_file(tmp_path):
    from ffast.loaders.model import uniformFrames
    from ffast.plugins.models.sgdml import sGDMLDatasetLoader

    rng = np.random.default_rng(0)
    path = tmp_path / "d.npz"
    np.savez(path, R=rng.normal(size=(5, 3, 3)), E=rng.normal(size=(5, 1)),
             F=rng.normal(size=(5, 3, 3)), z=np.array([1, 1, 8]))
    dataset = sGDMLDatasetLoader(str(path))

    R, z = uniformFrames(dataset, indices=[4, 0])
    assert np.array_equal(R, dataset.getCoordinates([4, 0])) and z.tolist() == [1, 1, 8]
    assert dataset.getAllEnergies().shape == (5,)


def test_a_calculator_model_predicts_every_frame_of_a_file():
    from ase.calculators.emt import EMT
    from ffast.loaders.model import ModelLoaderACE

    model = ModelLoaderACE.__new__(ModelLoaderACE)
    model.calculator = EMT()
    dataset = _ase(UNIFORM)
    dataset.lattice = None

    E, F = model.predict(dataset)
    assert np.shape(E) == (4,) and np.shape(F) == (4, 3, 3)


def test_the_3d_view_draws_a_dataset_built_from_an_sgdml_file(tmp_path):
    from ffast.plugins.models.sgdml import sGDMLDatasetLoader
    from ffast.visualization.scene_builder import build_scene
    from ffast.visualization.models import VisualizationState

    rng = np.random.default_rng(0)
    path = tmp_path / "d.npz"
    np.savez(path, R=rng.normal(size=(5, 3, 3)), E=rng.normal(size=(5, 1)),
             F=rng.normal(size=(5, 3, 3)), z=np.array([1, 1, 8]))
    dataset = sGDMLDatasetLoader(str(path))

    assert dataset.getElements(2).tolist() == [1, 1, 8]
    scene = build_scene(VisualizationState(view_id="v", dataset_ref="fp", structure_index=2),
                        get_dataset=lambda fp: dataset)
    assert scene.atoms is not None and len(scene.atoms.positions) == 3
