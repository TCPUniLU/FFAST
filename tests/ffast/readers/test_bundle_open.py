"""A bundle opens without its source and gives the dataset of the original load
(ADR 0110, step 4)."""
import os
import shutil

import numpy as np
import pytest

from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.readers import bundle, chunks, registry, text
from ffast.readers.bundle_reader import BundleReader
from ffast.readers.contract import ReaderError
from ffast.readers.dataset import ReaderDataset
from ffast.readers.xyz_reader import XyzReader
from tests.ffast.readers.test_bundle_export import write_xyz  # noqa: F401
from tests.ffast.readers.test_scan_naming import folder, forget_ram  # noqa: F401
from tests.ffast.test_loading_coordinator import _file_env

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")

KINDS = ["constant", "variable", "predictions"]
NAMES = {"energy_key": "pred_energy", "force_key": "pred_forces"}


def original(path, kind):
    """The dataset of an ordinary memory-saving load of the .xyz file."""
    forget_ram()
    kwargs = NAMES if kind == "predictions" else {}
    loaded = chunks.read_arrays(XyzReader, str(path), memory_mode="saving", workers=1)
    return ReaderDataset(str(path), loaded, "xyz", **kwargs)


def opened(target, kind="constant", **load):
    """The dataset of a bundle opened through the reader contract."""
    forget_ram()
    path = str(target / bundle.DESCRIPTION)
    kwargs = NAMES if kind == "predictions" else {}
    loaded = chunks.read_arrays(BundleReader, path, **load)
    return ReaderDataset(path, loaded, "ffast-bundle", **kwargs)


def exported(tmp_path, kind, name="a"):
    path = write_xyz(tmp_path / f"{name}.xyz", variable=kind == "variable",
                     predictions=kind == "predictions")
    ds = original(path, kind)
    target = tmp_path / f"{name}.ffast"
    bundle.export(path, target)
    return path, ds, target


def values(ds):
    """Every value of a dataset in memory, so the source can go afterwards."""
    out = {"E": ds.E, "F": ds.F_flat, "R": ds.R_flat, "z": ds.z_flat}
    out.update({f"field {k}": v for k, v in ds.field_arrays.items()})
    out = {k: np.array(np.asarray(v)) for k, v in out.items()}
    out["offsets"] = np.asarray(ds.molecule_offsets)
    return out


def same_values(a, b):
    a = a if isinstance(a, dict) else values(a)
    b = b if isinstance(b, dict) else values(b)
    assert a.keys() == b.keys()
    for key in a:
        assert np.array_equal(a[key], b[key]), key


@pytest.mark.parametrize("kind", KINDS)
def test_the_fingerprint_equals_the_original_loads(folder, tmp_path, kind):  # noqa: F811
    path, ds, target = exported(tmp_path, kind)
    opened_ds = opened(target, kind)
    assert opened_ds.getFingerprint() == ds.getFingerprint()
    assert opened_ds.isVariable == ds.isVariable
    assert opened_ds.metaElements() == ds.metaElements()
    assert opened_ds.reader_name == "ffast-bundle" and opened_ds.memory_mode == "saving"
    assert opened_ds.energy_key == ds.energy_key and opened_ds.force_key == ds.force_key
    if kind == "predictions":
        # The prediction's own energy and force are part of the dataset's columns.
        other = {"energy_key": "energy", "force_key": "forces"}
        a = ReaderDataset(str(path), chunks.read_arrays(XyzReader, str(path), memory_mode="saving", workers=1),
                          "xyz", **other)
        b = ReaderDataset(str(target / bundle.DESCRIPTION),
                          chunks.read_arrays(BundleReader, str(target / bundle.DESCRIPTION)),
                          "ffast-bundle", **other)
        assert a.getFingerprint() == b.getFingerprint() != ds.getFingerprint()


@pytest.mark.parametrize("kind", KINDS)
def test_every_value_equals_the_original_with_the_source_deleted(folder, tmp_path, kind):  # noqa: F811
    path, ds, target = exported(tmp_path, kind)
    expected = values(ds)
    os.unlink(path)
    shutil.rmtree(folder.parent)                        # and the parsed cache
    same_values(opened(target, kind), expected)


def test_a_bundle_copied_elsewhere_opens_the_same(folder, tmp_path):  # noqa: F811
    path, ds, target = exported(tmp_path, "variable")
    fingerprint, expected = ds.getFingerprint(), values(ds)
    os.unlink(path)
    copy = tmp_path / "elsewhere" / "renamed.ffast"
    shutil.copytree(target, copy)
    shutil.rmtree(target)
    other = opened(copy)
    assert other.getFingerprint() == fingerprint
    same_values(other, expected)


def damage(target, index=1):
    """Same size, unreadable: the end of a batch zeroed."""
    batch = sorted((target / bundle.BATCHES).glob("*.npz"), key=lambda p: int(p.stem.split("-")[0]))[index]
    data = bytearray(batch.read_bytes())
    data[-200:] = bytes(200)
    batch.write_bytes(bytes(data))
    return batch


def test_a_damaged_batch_is_parsed_from_the_source_when_it_is_there(folder, tmp_path):  # noqa: F811
    path, ds, target = exported(tmp_path, "predictions")
    batch = damage(target)
    before = batch.read_bytes()
    bundle.read_description(target)                     # the size check still passes
    same_values(opened(target, "predictions"), ds)
    assert batch.read_bytes() == before                 # never repaired in place


def test_a_damaged_batch_with_the_source_beside_the_bundle(folder, tmp_path):  # noqa: F811
    path, ds, target = exported(tmp_path, "constant")
    expected = values(ds)
    damage(target)
    moved = tmp_path / "box" / "a.xyz"
    moved.parent.mkdir()
    shutil.move(path, moved)
    folder_copy = moved.parent / "a.ffast"
    shutil.move(target, folder_copy)
    same_values(opened(folder_copy), expected)


def test_a_damaged_batch_with_the_source_gone_names_the_source(folder, tmp_path):  # noqa: F811
    path, ds, target = exported(tmp_path, "constant")
    damage(target)
    os.unlink(path)
    other = opened(target)
    with pytest.raises(ReaderError) as error:
        np.asarray(other.R_flat)
    text_ = str(error.value)
    assert "a.xyz" in text_ and str(path) in text_ and str(tmp_path / "a.xyz") in text_
    assert "a.ffast" in text_


def test_a_modified_source_is_not_accepted(folder, tmp_path):  # noqa: F811
    path, ds, target = exported(tmp_path, "constant")
    damage(target)
    data = bytearray(open(path, "rb").read())
    data[len(data) // 2] ^= 1                           # inside the whole-file slice of a small file
    open(path, "wb").write(bytes(data))
    other = opened(target)
    with pytest.raises(ReaderError, match="source file a.xyz was not found"):
        np.asarray(other.R_flat)


def listing(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in root.rglob("*"))


def test_the_bundle_is_unchanged_by_an_open_and_by_basic_errors(folder, tmp_path):  # noqa: F811
    from ffast.io import budget as resources
    from ffast.metrics import builtin  # noqa: F401
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.models import MetricFailure
    from ffast.metrics.registry import default_registry
    from ffast.metrics.server_executor import ServerExecutor

    path, ds, target = exported(tmp_path, "variable")
    before = listing(target)
    cache_before = listing(folder.parent)
    other = opened(target)
    executor = ServerExecutor(default_registry)
    try:
        for metric, value in (("ffast.energy_difference[dataset;mean]", other.E),
                              ("ffast.force_difference[dataset;component;mae]", other.F_flat)):
            source = FlatInputSource({"reference": value, "predicted": value + 0.1,
                                      "_offsets": other.getOffsets(), "_elements": other.z_flat})
            result = executor.run(metric, source, {})
            assert not isinstance(result, MetricFailure), getattr(result, "traceback", "")
            np.testing.assert_allclose(result.values, 0.1)
    finally:
        executor.shutdown()
    assert listing(target) == before
    assert listing(folder.parent) == cache_before       # and nothing in the parsed cache either


def test_a_stride_or_a_sample_of_a_bundle_is_refused(folder, tmp_path):  # noqa: F811
    path, ds, target = exported(tmp_path, "constant")
    file = str(target / bundle.DESCRIPTION)
    with pytest.raises(ReaderError, match="loads whole"):
        chunks.read_arrays(BundleReader, file, stride=2)
    with pytest.raises(ReaderError, match="loads whole"):
        chunks.read_arrays(BundleReader, file, select=lambda n: range(0, n, 3))
    chunks.read_arrays(BundleReader, file, select=lambda n: range(n))     # every frame is fine


def test_a_newer_bundle_is_refused_with_its_message(folder, tmp_path):  # noqa: F811
    import json
    path, ds, target = exported(tmp_path, "constant")
    file = target / bundle.DESCRIPTION
    data = json.loads(file.read_text())
    data["schema"] = 9
    file.write_text(json.dumps(data))
    with pytest.raises(ReaderError, match="newer FFAST; update FFAST"):
        chunks.read_arrays(BundleReader, str(file))


def test_the_open_box_finds_the_bundle_reader_before_ases(folder, tmp_path):  # noqa: F811
    path, ds, target = exported(tmp_path, "constant")
    file = str(target / bundle.DESCRIPTION)
    assert registry.readers_for(file)[0] is BundleReader
    assert LoadingCoordinator._datasetReader("ase (auto)", file) is BundleReader
    assert LoadingCoordinator._datasetReader("ffast-bundle", file) is BundleReader
    assert not any(r["name"] == "ffast-bundle" for r in registry.type_rows())


# -- predictions ----------------------------------------------------------------

def coordinator_for(ds):
    ds.initialise()
    env = _file_env(ds, key=ds.fingerprint)
    return LoadingCoordinator(env), env


def test_a_prediction_from_the_datasets_own_bundle_reuses_its_storage(folder, tmp_path, monkeypatch):  # noqa: F811
    path, ds, target = exported(tmp_path, "predictions")
    dataset = opened(target, "predictions")
    coordinator, env = coordinator_for(dataset)
    asked = []
    monkeypatch.setattr(LoadingCoordinator, "_readArrays",
                        lambda *a, **k: asked.append(1) or (_ for _ in ()).throw(AssertionError("reread")))
    errors = []
    coordinator.loadPrepredictedDataset(
        str(target / bundle.DESCRIPTION), dataset.fingerprint, reader="ffast-bundle",
        selected_energy_key="energy", selected_force_key="forces", on_error=errors.append)
    assert errors == [] and asked == []
    stored = {kind: payload for kind, payload, _, _ in env.data.stored}
    assert np.array_equal(np.asarray(stored["energy"]["energy"]), np.arange(3000) * 0.5)


def test_a_prediction_exported_to_its_own_bundle_loads_as_a_separate_file(folder, tmp_path):  # noqa: F811
    path, ds, target = exported(tmp_path, "predictions", name="ds")
    other = write_xyz(tmp_path / "pred.xyz", predictions=True)
    pred_target = tmp_path / "pred.ffast"
    bundle.export(other, pred_target)
    dataset = opened(target, "predictions")
    coordinator, env = coordinator_for(dataset)
    errors = []
    coordinator.loadPrepredictedDataset(
        str(pred_target / bundle.DESCRIPTION), dataset.fingerprint, reader="ffast-bundle",
        selected_energy_key="pred_energy", selected_force_key="pred_forces", on_error=errors.append)
    assert errors == []
    stored = {kind: payload for kind, payload, _, _ in env.data.stored}
    assert np.array_equal(np.asarray(stored["energy"]["energy"]), np.arange(3000) * 0.5 + 0.25)
