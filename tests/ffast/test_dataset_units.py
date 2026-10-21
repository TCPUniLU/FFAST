"""The units a user names for a dataset's energies and forces.

No dataset file declares its units, so a window names them ("[unit]" until it
does). They belong to the loaded dataset: a subset cut from it reads and sets
its root's. A change is announced to every window as the dataset's metadata
again, and a saved session keeps them.
"""
import asyncio
import json
import types

from ase import Atoms
import pytest

from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import DatasetView
from ffast.protocol import DatasetMeta, control
from ffast.session.persistence import SessionPersistence
from ffast.session.server_session import ServerSession


def _dataset(path="test.xyz"):
    frames = [Atoms("H2O", positions=[[0, 0, 0], [0, 0, 1], [1, 0, 0]]) for _ in range(4)]
    dataset = aseDatasetLoader(path, atomsList=frames)
    dataset.initialise()
    return dataset


def _subset(dataset):
    subset = DatasetView.frozenSubset(dataset, None, [1, 2], "Energy")
    subset.initialise()
    return subset


def _pushes(dataset):
    pushed = []
    dataset.eventPush = lambda *args, **kwargs: pushed.append(args)
    return pushed


# ── the dataset ─────────────────────────────────────────────────────────────

def test_a_new_dataset_has_no_units():
    dataset = _dataset()
    assert dataset.getUnits() == {}
    assert DatasetMeta.model_validate(dataset.toMetaDict()).units == {}


def test_a_unit_is_set_trimmed_and_removed_by_an_empty_one():
    dataset = _dataset()
    dataset.setUnit("energy", " eV ")
    dataset.setUnit("force", "eV/Å")
    assert dataset.getUnits() == {"energy": "eV", "force": "eV/Å"}
    dataset.setUnit("energy", "")
    assert dataset.getUnits() == {"force": "eV/Å"}


def test_an_unknown_dimension_is_refused():
    with pytest.raises(ValueError):
        _dataset().setUnit("length", "Å")


def test_the_metadata_carries_the_units():
    dataset = _dataset()
    dataset.setUnit("energy", "kcal/mol")
    assert DatasetMeta.model_validate(dataset.toMetaDict()).units == {"energy": "kcal/mol"}


def test_a_subset_reads_and_sets_its_roots_units():
    dataset = _dataset()
    subset = _subset(dataset)
    dataset.setUnit("energy", "eV")
    assert subset.getUnits() == {"energy": "eV"}
    subset.setUnit("force", "eV/Å")
    assert dataset.getUnits() == {"energy": "eV", "force": "eV/Å"}
    # Only the dataset announces them, so a change is one announcement.
    assert DatasetMeta.model_validate(subset.toMetaDict()).units == {}


def test_a_change_announces_the_dataset_once_and_no_change_not_at_all():
    dataset = _dataset()
    dataset.loaded = True
    pushed = _pushes(dataset)
    dataset.setUnit("energy", "eV")
    dataset.setUnit("energy", "eV")
    assert pushed == [("DATASET_STATE_CHANGED", dataset.fingerprint)]


# ── the server message ──────────────────────────────────────────────────────

def _dispatch(env, kwargs):
    async def scenario():
        session = ServerSession(env, asyncio.Queue(), broadcast=lambda data: None)
        await session.dispatch(control.SET_DATASET_UNIT, [], kwargs)
    asyncio.run(scenario())


def _env(*datasets):
    held = {d.fingerprint: d for d in datasets}
    return types.SimpleNamespace(datasets=types.SimpleNamespace(get=held.get))


def test_set_dataset_unit_names_the_unit():
    dataset = _dataset()
    _dispatch(_env(dataset), {"fingerprint": dataset.fingerprint, "dimension": "force", "unit": "eV/Å"})
    assert dataset.getUnits() == {"force": "eV/Å"}


def test_set_dataset_unit_on_a_subset_names_its_datasets_unit():
    dataset = _dataset()
    subset = _subset(dataset)
    _dispatch(_env(dataset, subset), {"fingerprint": subset.fingerprint, "dimension": "energy", "unit": "eV"})
    assert dataset.getUnits() == {"energy": "eV"}


def test_set_dataset_unit_ignores_an_unknown_dimension_or_dataset():
    dataset = _dataset()
    _dispatch(_env(dataset), {"fingerprint": dataset.fingerprint, "dimension": "length", "unit": "Å"})
    _dispatch(_env(dataset), {"fingerprint": "nope", "dimension": "energy", "unit": "eV"})
    assert dataset.getUnits() == {}


def test_set_dataset_unit_is_a_change_a_viewer_may_not_make():
    assert control.SET_DATASET_UNIT in control.MUTATING_CLIENT_EVENTS


# ── a saved session ─────────────────────────────────────────────────────────

def _session_env(held):
    def load_dataset(path, dataset_type, **kwargs):
        dataset = _dataset(path)
        held[dataset.fingerprint] = dataset

    return types.SimpleNamespace(
        datasets=types.SimpleNamespace(
            get=held.get, all=lambda excludeSubs=False: list(held.values())),
        models=types.SimpleNamespace(all=lambda: []),
        cache={},
        objects=types.SimpleNamespace(snapshot=lambda: {}, load=lambda objects: None),
        datasetTypes={},
        loadDataset=load_dataset,
        lookForGhosts=lambda: None,
    )


def test_a_saved_session_keeps_the_units(tmp_path):
    source = tmp_path / "test.xyz"
    source.write_text("")   # the session reloads only datasets whose file exists
    dataset = _dataset(str(source))
    dataset.setUnit("energy", "eV")

    SessionPersistence(_session_env({dataset.fingerprint: dataset})).save(str(tmp_path / "s"))
    info = json.loads((tmp_path / "s" / "info.json").read_text())
    assert info["objects"][dataset.fingerprint]["units"] == {"energy": "eV"}

    held = {}
    SessionPersistence(_session_env(held)).load(str(tmp_path / "s"))
    assert held[dataset.fingerprint].getUnits() == {"energy": "eV"}
