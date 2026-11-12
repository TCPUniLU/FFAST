"""Dataset Fields (ADR 0023) of a reader's dataset and prediction behave as
the old ASE loader's did."""
import ase.io
import numpy as np
import pytest

from ffast.io import frame_cache, frame_index as fi
from ffast.loaders.ase import aseDatasetLoader
from ffast.readers import chunks, text
from ffast.readers.dataset import ReaderDataset, ReaderFieldSource
from ffast.readers.xyz_reader import XyzReader
from tests.ffast.read_once_fixtures import FILES


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    fi.clear_memory_cache()
    text.clear_memory_cache()
    for name, body in FILES.items():
        (tmp_path / name).write_text(body)
    yield tmp_path
    fi.clear_memory_cache()
    text.clear_memory_cache()


def _both(files, name, **keys):
    path = str(files / name)
    from ffast.core.data_types import AtomsList
    legacy = aseDatasetLoader(path, atomsList=AtomsList(path), **keys)
    new = ReaderDataset(path, chunks.read_arrays(XyzReader, path), "xyz", **{
        k.replace("selected_", "").replace("_key", "_key"): v for k, v in keys.items()})
    return legacy, new


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, list):
        return len(a) == len(b) and all(np.array_equal(x, y, equal_nan=True) for x, y in zip(a, b))
    return np.array_equal(a, b, equal_nan=True)


@pytest.mark.parametrize("name", ["uniform.extxyz", "variable.extxyz", "columns_change.extxyz", "plain.xyz"])
def test_the_available_keys_and_every_field_equal_the_old_loaders(files, name):
    legacy, new = _both(files, name)
    assert new.availableFieldKeys() == legacy.availableFieldKeys()
    frame_keys, atom_keys = legacy.availableFieldKeys()
    for key in frame_keys:
        assert _same(new.getFrameField(key), legacy.getFrameField(key)), key
        assert new.getFrameField(key, indices=1) == legacy.getFrameField(key, indices=1)
        assert _same(new.getFrameField(key, indices=[0, 2]), legacy.getFrameField(key, indices=[0, 2]))
    for key in atom_keys:
        assert _same(new.getAtomField(key), legacy.getAtomField(key)), key
        assert _same(new.getAtomField(key, indices=1), legacy.getAtomField(key, indices=1)), key
        assert _same(new.getAtomFieldMissing(key), legacy.getAtomFieldMissing(key)), key


def test_a_per_atom_field_some_frames_lack_has_a_missing_mask(files):
    _, new = _both(files, "columns_change.extxyz")
    assert "spin" in new.availableFieldKeys()[1]
    mask = new.getAtomFieldMissing("spin")
    assert mask is not None and mask.tolist() == [[True, True], [True, True], [False, False]]
    assert new.getAtomFieldMissing("spin", indices=2).tolist() == [False, False]


def test_a_frame_field_some_frame_lacks_is_unavailable(files, caplog):
    path = files / "gap.extxyz"
    path.write_text("1\nProperties=species:S:1:pos:R:3 gap=1.0\nH 0 0 0\n"
                    "1\nProperties=species:S:1:pos:R:3\nH 0 0 1\n")
    ds = ReaderDataset(str(path), chunks.read_arrays(XyzReader, str(path)), "xyz")
    assert "gap" not in ds.availableFieldKeys()[0]
    assert ds.getFrameField("gap") is None
    assert ds.getFrameField("nothing") is None and ds.getAtomField("nothing") is None


def test_the_fields_stay_when_the_extra_arrays_are_released(files):
    _, new = _both(files, "variable.extxyz")
    new.arrays = None                        # as after the in-file predictions are loaded
    assert new.getFrameField("gap").tolist() == [1.25, 2.5, 0.75]


def test_a_prediction_source_answers_like_a_dataset_over_the_same_arrays(files):
    path = str(files / "variable.extxyz")
    loaded = chunks.read_arrays(XyzReader, path)
    source = ReaderFieldSource(loaded.arrays)
    legacy, _ = _both(files, "variable.extxyz")
    assert source.availableFieldKeys() == legacy.availableFieldKeys()
    assert _same(source.getFrameField("gap"), legacy.getFrameField("gap"))
    assert _same(source.getAtomField("q"), legacy.getAtomField("q"))
