"""Step 5 of the reader plan: units, ``can_read`` as the Type default, and a
session that names a missing reader."""
import asyncio
import json
import logging
import os
from types import SimpleNamespace

import numpy as np
import pytest

from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.readers import chunks, registry
from ffast.readers.dataset import ReaderDataset
from ffast.session.server_session import ServerSession
from tests.ffast.readers.toy_reader import (
    ToyDatNo, ToyDatYes, ToyFieldReader, ToyReader, make_frames, write_toy)


# ── units ───────────────────────────────────────────────────────────────────

def test_a_readers_unit_labels_are_the_datasets_units_and_the_user_can_change_them(tmp_path):
    registry.register(ToyFieldReader)
    path = write_toy(tmp_path / "a.toyf", make_frames(3))
    ds = ReaderDataset(path, chunks.read_arrays(ToyFieldReader, path), "toyfield")
    assert ds.getUnits() == {"energy": "eV", "force": "eV/A"}
    assert ds.toMetaDict()["units"] == {"energy": "eV", "force": "eV/A"}
    ds.setUnits({"energy": "Hartree"})
    assert ds.getUnits() == {"energy": "Hartree"}


def test_a_unit_for_a_dimension_ffast_does_not_know_is_left_out_with_a_warning(tmp_path, caplog):
    class Odd(ToyReader):
        name = "odd"
        units = {"energy": "eV", "charge": "e"}

    path = write_toy(tmp_path / "a.toy", make_frames(2))
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        ds = ReaderDataset(path, chunks.read_arrays(Odd, path), "odd")
    assert ds.getUnits() == {"energy": "eV"}
    assert any("charge" in r.getMessage() for r in caplog.records)


def test_a_file_without_unit_labels_has_no_units(tmp_path, toy):
    path = write_toy(tmp_path / "a.toy", make_frames(2))
    assert ReaderDataset(path, chunks.read_arrays(ToyReader, path), "toy").getUnits() == {}


# ── can_read as the Type default ────────────────────────────────────────────

def _probe(path, dataset_type):
    session = ServerSession.__new__(ServerSession)
    session.env = SimpleNamespace(datasetTypes={})
    sent = []

    async def emit(data):
        sent.append(data)

    session._emit = emit
    asyncio.run(session._on_probe_dataset_keys(path, dataset_type))
    [(event, _, kwargs)] = [unpack(d) for d in sent]
    assert event == control.DATASET_KEYS_RESPONSE
    return kwargs


def test_the_probe_names_the_reader_that_can_read_the_file(tmp_path):
    registry.register(ToyDatNo)
    registry.register(ToyDatYes)
    path = write_toy(tmp_path / "x.dat", make_frames(2))
    assert [r.name for r in registry.readers_for(path)] == ["toydat-b", "ase (auto)"]   # ASE: the fallback
    assert _probe(path, "toydat-b")["reader"] == "toydat-b"
    # asked as the reader that declines, the probe hands over to the one that reads it
    reply = _probe(path, "toydat-a")
    assert reply["reader"] == "toydat-b" and reply["energy_keys"] == ["dft"]


def test_a_hidden_reader_is_never_the_suggested_type(tmp_path):
    path = tmp_path / "a.extxyz"
    path.write_text("1\nProperties=species:S:1:pos:R:3 energy=1.0\nH 0 0 0\n")
    assert _probe(str(path), "ase (auto)")["reader"] is None


# ── sessions ────────────────────────────────────────────────────────────────

def _persistence(tmp_path):
    from ffast.session.persistence import SessionPersistence

    loaded = []
    p = SessionPersistence.__new__(SessionPersistence)
    p._env = SimpleNamespace(
        datasetTypes={"sGDML": object},
        datasets=SimpleNamespace(get=lambda fp: None),
        loadDataset=lambda *a, **k: loaded.append((a, k)),
        lookForGhosts=lambda: None,
    )
    p.loadInfo = lambda info: None
    return p, loaded


def _session(tmp_path, objects):
    folder = tmp_path / "session"
    folder.mkdir()
    (folder / "info.json").write_text(json.dumps({"objects": objects}))
    return str(folder)


def test_a_session_loads_a_dataset_by_the_reader_name_it_saved(tmp_path):
    registry.register(ToyReader)
    data = write_toy(tmp_path / "a.toy", make_frames(2))
    p, loaded = _persistence(tmp_path)
    p.load(_session(tmp_path, {"fp1": {
        "name": "a", "path": data, "type": "dataset", "datasetType": None, "reader": "toy",
        "ase_energy_key": "dft", "ase_force_key": ""}}))
    [((path, dataset_type), kwargs)] = loaded
    assert (path, dataset_type) == (data, "toy")
    assert kwargs["selected_energy_key"] == "dft" and kwargs["selected_force_key"] == ""


def test_a_missing_reader_is_named_with_its_package_and_the_rest_of_the_session_loads(tmp_path, caplog):
    registry.register(ToyReader)
    here = write_toy(tmp_path / "here.toy", make_frames(2))
    gone = write_toy(tmp_path / "run.foo", make_frames(2))
    p, loaded = _persistence(tmp_path)
    with caplog.at_level(logging.WARNING):
        p.load(_session(tmp_path, {
            "fp1": {"name": "run", "path": gone, "type": "dataset", "reader": "foo",
                    "reader_package": "ffast-foo"},
            "fp2": {"name": "here", "path": here, "type": "dataset", "reader": "toy"}}))
    assert [a[0][0] for a in loaded] == [here]
    assert "run.foo needs the foo reader: pip install ffast-foo" in [r.getMessage() for r in caplog.records]


def test_a_missing_reader_with_no_known_package_is_named_without_the_pip_part(tmp_path, caplog):
    gone = write_toy(tmp_path / "run.foo", make_frames(2))
    p, loaded = _persistence(tmp_path)
    with caplog.at_level(logging.WARNING):
        p.load(_session(tmp_path, {"fp1": {"name": "run", "path": gone, "type": "dataset", "reader": "foo"}}))
    assert loaded == []
    assert [r.getMessage() for r in caplog.records if "needs the" in r.getMessage()] == [
        "run.foo needs the foo reader"]


def test_a_session_saved_before_readers_still_falls_back_to_ase(tmp_path):
    data = write_toy(tmp_path / "a.xyz", make_frames(2))
    p, loaded = _persistence(tmp_path)
    p.load(_session(tmp_path, {"fp1": {"name": "a", "path": data, "type": "dataset",
                                       "datasetType": "ase (variable)"}}))
    [((path, dataset_type), _)] = loaded
    assert dataset_type == "ase (auto)"


def test_a_reader_dataset_saves_its_reader_package_and_chosen_names(tmp_path):
    class Pkg(ToyReader):
        name = "pkgd"
        package = "ffast-pkgd"

    registry.register(Pkg)
    path = write_toy(tmp_path / "a.toy", make_frames(2, extra=["mace"]))
    ds = ReaderDataset(path, chunks.read_arrays(Pkg, path), "pkgd", energy_key="mace", force_key="")
    from ffast.session.persistence import SessionPersistence
    info = SessionPersistence.datasetInfo(ds)
    assert info["reader"] == "pkgd" and info["reader_package"] == "ffast-pkgd"
    assert info["ase_energy_key"] == "mace" and info["ase_force_key"] == ""
