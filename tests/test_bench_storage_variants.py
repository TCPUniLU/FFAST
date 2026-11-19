"""All 16 storage variants round-trip the batch set bit for bit (ADR 0109 step 2)."""

import json

import numpy as np
import pytest

pytest.importorskip("zarr")

from benchmarks.storage import measure, variants
from benchmarks.storage.source import Source, describe_batches, read_restored

IDENTITY = "ab" * 32


def make_source(folder, variable, windows=3, frames=120, seed=0):
    """Batches written the way FFAST writes them: species as uint8 with a restore table."""
    rng = np.random.default_rng(seed)
    batches = folder / "ffast-cache" / "batches"
    batches.mkdir(parents=True)
    a = 0
    for _ in range(windows):
        counts = rng.integers(60, 140, frames) if variable else np.full(frames, 100)
        atoms = int(counts.sum())
        part = {
            "n_atoms": counts.astype(np.int64),
            "numbers": rng.integers(1, 9, atoms).astype(np.uint8),
            "positions": rng.normal(size=(atoms, 3)),
            "cell": np.tile(np.eye(3) * 12.0, (frames, 1, 1)),
            "forces:forces": rng.normal(size=(atoms, 3)),
            "forces:forces_pred": rng.normal(size=(atoms, 3)),
            "energy:energy": rng.normal(size=frames),
            "__restore__": np.asarray([["numbers", "<i8"]]),
        }
        part["forces:forces"][::97] = np.nan  # NaN must survive the round trip
        np.savez(batches / f"{IDENTITY}-{a}-{a + frames}.npz", **part)
        a += frames
    describe_batches(folder, batches, {"label": folder.name})
    return Source(folder)


@pytest.fixture(scope="module", params=["constant", "variable"])
def source(request, tmp_path_factory):
    return make_source(tmp_path_factory.mktemp(request.param), request.param == "variable")


def test_there_are_sixteen_variants():
    names = variants.all_names()
    assert len(names) == 16 and names[0] == "npz"
    assert "zarr-zstd3-16k" in names and "zarr-none-1m" in names


@pytest.mark.parametrize("name", variants.all_names())
def test_a_variant_round_trips_every_column_and_frame(source, tmp_path, name):
    variant = variants.make(name)
    store = tmp_path / "store"
    wall, cpu = variant.write(source, store)
    assert wall > 0 and cpu >= 0
    assert measure.check(variant, source, store) == {"ok": True, "bad": []}
    # Frame i equals the rows of the source window, for frames in every window.
    handle = variant.open(source, store)
    for i in (0, 119, 120, 200, 359):
        window, l0, l1, _, _ = source.locate(i)
        want = read_restored(source.batch_path(window))
        got = handle.frame(i)
        assert set(got) == {"positions", "forces:forces", "forces:forces_pred"}
        for key, array in got.items():
            assert np.array_equal(array, want[key][l0:l1], equal_nan=True)
    size, alloc, files = variants.disk_usage(store)
    assert size > 0 and alloc >= 0 and files >= 1
    assert measure.op_scan(variant, source, store)["scan_s"] > 0
    assert measure.op_reopen(variant, source, store)["reopen_s"] > 0


def test_the_check_notices_a_changed_value(source, tmp_path):
    variant = variants.make("zarr-none-16k")
    store = tmp_path / "store"
    variant.write(source, store)
    import zarr

    group = zarr.open_group(store, mode="r+")
    array = group["positions"]
    array[5] = array[5] + 1.0
    result = measure.check(variant, source, store)
    assert not result["ok"] and any("positions" in b for b in result["bad"])


def test_frame_indices_are_the_fixed_draw_in_draw_order():
    got = measure.frame_indices(1000)
    assert len(got) == 2000
    assert not np.array_equal(got, np.sort(got))
    assert np.array_equal(got, np.random.default_rng(20261119).integers(0, 1000, size=2000))


def test_blosc_runs_on_one_thread():
    effect = variants.configure_threads()
    assert effect["blosc_nthreads"] == 1 and effect["zarr_max_workers"] == 1


def test_an_unknown_variant_is_refused():
    with pytest.raises(ValueError):
        variants.make("zarr-lz5-16k")
