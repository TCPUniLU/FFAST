"""
The all-frames methods mean the same on every kind of dataset (ADR 0058, step 4).

Flat arrays with one row per atom, frame after frame, plus offsets saying where
each frame's atoms start. Every kind is checked against its own per-frame
answers, which is what the flat arrays must add up to.
"""
import os
import tempfile

import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import DatasetView, stackFrames

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
    sub = DatasetView.subset(parent, None, np.array(frames), "Subset")
    sub.initialise()
    return sub


def _atom_subset(parent):
    sub = DatasetView.atomSubset(parent, np.array([0, 2]))
    sub.initialise()
    return sub


def _sgdml(forces=True, energies=True):
    from ffast.plugins.models.sgdml import sGDMLDatasetLoader

    rng = np.random.default_rng(3)
    arrays = {"R": rng.normal(size=(4, 3, 3)), "z": np.array([1, 1, 8])}
    if energies:
        arrays["E"] = rng.normal(size=(4, 1))
    if forces:
        arrays["F"] = rng.normal(size=(4, 3, 3))
    path = os.path.join(tempfile.mkdtemp(), "d.npz")
    np.savez(path, **arrays)
    dataset = sGDMLDatasetLoader(path)
    dataset.initialise()
    dataset.loaded = True
    return dataset


KINDS = {
    "uniform file": lambda: _ase(UNIFORM),
    "sGDML file": lambda: _sgdml(),
    "frame subset of sGDML": lambda: _frame_subset(_sgdml()),
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


def test_an_sgdml_file_lacking_results_has_none():
    dataset = _sgdml(forces=False, energies=False)
    assert not dataset.hasForces()
    with pytest.raises(RuntimeError):
        dataset.getAllForces()
    with pytest.raises(RuntimeError):
        dataset.getAllEnergies()


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
    dataset = _ase(UNIFORM)       # molecules: every frame's cell is all zeros

    E, F = model.predict(dataset)
    assert np.shape(E) == (4,) and np.shape(F) == (4, 3, 3)
    molecule = Atoms(dataset.getElements(1), positions=dataset.getCoordinates(1))
    molecule.calc = EMT()
    assert np.isclose(E[1], molecule.get_potential_energy())


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


def test_predictions_are_shaped_for_reading_one_frame_at_a_time():
    from ffast.loaders.dataset import framesShaped

    flat = np.arange(12, dtype=float).reshape(6, 2)
    stacked = framesShaped(flat, [0, 3, 6], variable=False)
    assert stacked.shape == (2, 3, 2) and stacked.flags.writeable
    listed = framesShaped(flat, [0, 1, 6], variable=True)
    assert [len(f) for f in listed] == [1, 5]
    assert np.array_equal(np.concatenate(listed), flat)


@pytest.mark.parametrize("kind", ["uniform file", "variable file", "frame subset of variable"])
def test_the_zero_model_predicts_zeros_in_the_cached_shape(kind):
    from ffast.loaders.zero import ZeroModelLoader

    dataset = KINDS[kind]()
    energies, forces = ZeroModelLoader.predict(None, dataset)
    assert energies.shape == (dataset.getN(),)
    for i in range(dataset.getN()):
        assert np.shape(forces[i]) == np.shape(dataset.getForces(i))
        assert not np.any(forces[i])
    assert isinstance(forces, list) is bool(dataset.isVariable)


def test_a_calculator_model_uses_each_frames_own_cell():
    from ase.calculators.emt import EMT
    from ffast.loaders.model import ModelLoaderACE

    frames = [Atoms("Cu2", positions=[[0, 0, 0], [1.8, 0, 0]], cell=[3.6 + 0.1 * i] * 3, pbc=True)
              for i in range(3)]
    dataset = aseDatasetLoader("cu.xyz", atomsList=frames)
    model = ModelLoaderACE.__new__(ModelLoaderACE)
    model.calculator = EMT()

    E, F = model.predict(dataset)
    for i, atoms in enumerate(frames):
        atoms = atoms.copy()
        atoms.calc = EMT()
        assert np.isclose(E[i], atoms.get_potential_energy())

    E1, _ = model.predict(dataset, indices=[2])
    assert np.isclose(E1[0], E[2])


@pytest.mark.parametrize("formulas", [UNIFORM, VARIABLE])
@pytest.mark.parametrize("results", [True, False])
def test_export_writes_every_frame_with_the_results_it_has(tmp_path, formulas, results):
    import ase.io

    dataset = _ase(formulas, forces=results, energies=results)
    out = tmp_path / "out.extxyz"
    aseDatasetLoader.saveDataset(dataset, str(out), "extxyz")

    back = ase.io.read(str(out), index=":")
    assert len(back) == dataset.getN()
    for i, frame in enumerate(back):
        assert frame.get_atomic_numbers().tolist() == dataset.getElements(i).tolist()
        np.testing.assert_allclose(frame.get_positions(), dataset.getCoordinates(i), atol=1e-6)
        if results:      # extxyz keeps 8 decimals
            np.testing.assert_allclose(frame.get_forces(), dataset.getForces(i), atol=1e-6)
            assert np.isclose(frame.get_potential_energy(), dataset.getEnergies(i), atol=1e-6)


@pytest.mark.parametrize("kind", ["uniform file", "sGDML file", "frame subset of uniform",
                                  "atom subset of uniform"])
def test_a_dataset_with_the_same_atoms_in_every_frame_saves_as_sgdml(kind, tmp_path):
    from ffast.plugins.models.sgdml import sGDMLDatasetLoader

    dataset = KINDS[kind]()
    path = str(tmp_path / "out.npz")
    sGDMLDatasetLoader.saveDataset(dataset, path)
    saved = sGDMLDatasetLoader(path)
    assert np.array_equal(saved.getAllCoordinates(), dataset.getAllCoordinates())
    assert np.array_equal(saved.getAllForces(), dataset.getAllForces())
    assert np.array_equal(saved.getAllElements(), dataset.getAllElements())
    assert np.array_equal(saved.getAllEnergies(), dataset.getAllEnergies())


def test_a_dataset_whose_structures_differ_is_not_saved_as_sgdml(tmp_path):
    from ffast.plugins.models.sgdml import sGDMLDatasetLoader

    with pytest.raises(ValueError, match="same atoms in the same order"):
        sGDMLDatasetLoader.saveDataset(KINDS["variable file"](), str(tmp_path / "out.npz"))
