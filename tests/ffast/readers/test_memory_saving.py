"""Memory-saving storage keeps scientific values and bounds materialisation."""

import gc
from pathlib import Path

import numpy as np
import pytest

from ffast.io import budget as resources
from ffast.io.batched import BatchedArray
from ffast.io.extxyz_arrays import NotEnoughMemory
from ffast.readers import chunks
from ffast.readers.dataset import ReaderDataset
from ffast.readers.xyz_reader import XyzReader


@pytest.fixture
def small_budget(monkeypatch, tmp_path):
    owner = resources.Budget(
        ram_bytes=512 * 1024, parsed_bytes=0, result_bytes=8 * 2**20
    )
    monkeypatch.setattr(resources, "_budget", owner)
    monkeypatch.setenv("FFAST_RESULT_CACHE_DIR", str(tmp_path / "results"))
    yield owner
    gc.collect()


def write_xyz(path, n=700, atoms=40):
    with open(path, "w") as f:
        for i in range(n):
            f.write(
                f"{atoms}\nProperties=species:S:1:pos:R:3:forces:R:3 energy={i}\n"
            )
            for j in range(atoms):
                f.write(f"C {i}.125 {j}.25 -1.5 0.1 0.2 0.3\n")
    return str(path)


def dataset(path, **kwargs):
    return ReaderDataset(
        path,
        chunks.read_arrays(
            XyzReader, path, workers=1, use_cache=False, **kwargs
        ),
        "xyz",
    )


def test_saving_reads_individual_frames_and_refuses_accidental_whole_array(
    tmp_path, small_budget
):
    path = write_xyz(tmp_path / "large.xyz")
    ds = dataset(path, memory_mode="saving")
    assert ds.memory_mode == "saving"
    assert ds.getCoordinates(123)[0, 0] == 123.125
    assert ds.getForces(4).shape == (40, 3)
    assert ds.metaElements()["element_set"] == [6]
    with pytest.raises(NotEnoughMemory):
        np.asarray(ds.R_flat)
    assert small_budget.cache_bytes <= small_budget.limit // 4


def test_auto_selects_saving_before_full_allocation(tmp_path, small_budget):
    ds = dataset(write_xyz(tmp_path / "large.xyz"))
    assert ds.memory_mode == "saving"


@pytest.mark.parametrize("kind", ["energy", "force"])
def test_metric_scan_with_other_work_using_ram(tmp_path, small_budget, kind):
    from ffast.metrics import builtin
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.models import MetricFailure
    from ffast.metrics.registry import default_registry
    from ffast.metrics.server_executor import ServerExecutor

    ds = dataset(write_xyz(tmp_path / "large.xyz"), memory_mode="saving")
    value = ds.E if kind == "energy" else ds.F_flat
    source = FlatInputSource(
        {
            "reference": value,
            "predicted": value + 0.1,
            "_offsets": ds.getOffsets(),
            "_elements": ds.z_flat,
        }
    )
    metric = (
        "ffast.energy_difference[dataset;mean]"
        if kind == "energy"
        else "ffast.force_difference[dataset;component;mae]"
    )
    executor = ServerExecutor(default_registry)
    try:
        # Model the other panels holding working RAM, as in the reported
        # dashboard failure. A scalar scan still needs only small batches.
        with small_budget.reserve(300 * 1024):
            result = executor.run(metric, source, {})
            assert not isinstance(result, MetricFailure), getattr(
                result, "traceback", ""
            )
            np.testing.assert_allclose(result.values, 0.1)
    finally:
        executor.shutdown()


def test_dashboard_metrics_share_bounded_workspaces(tmp_path, small_budget):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from ffast.metrics import builtin
    from ffast.metrics.execution import InputSource
    from ffast.metrics.models import MetricFailure
    from ffast.metrics.registry import default_registry
    from ffast.metrics.server_executor import ServerExecutor

    small_budget.limit = 8 * 2**20
    ds = dataset(write_xyz(tmp_path / "dashboard.xyz"), memory_mode="saving")

    class Source(InputSource):
        def get(self, metric_id, local_key, ref):
            values = {
                "reference.energies": ds.E,
                "prediction.energies": ds.E + 0.1,
                "reference.forces": ds.F_flat,
                "prediction.forces": ds.F_flat + 0.1,
                "offsets": ds.getOffsets(),
                "reference.elements": ds.z_flat,
            }
            return ref in values, values.get(ref)

        def identity(self, ref):
            return ref + "|dashboard"

    metrics = [
        "ffast.energy_error_smoothed",
        "ffast.energy_parity_outliers",
        "ffast.energy_parity_density",
        "ffast.force_difference[dataset;component;rmse]",
        "ffast.force_difference[dataset;component;mae]",
        "ffast.energy_difference__shift__rmse",
        "ffast.energy_difference__shift__mean_abs",
        "ffast.energy_difference_density",
        "ffast.force_parity_outliers",
        "ffast.force_parity_density",
    ]
    from ffast.metrics.transforms import compile_pipeline

    for transform in ("rmse", "mean_abs"):
        compile_pipeline("ffast.energy_difference", ["shift", transform])
    start = Barrier(len(metrics))
    executor = ServerExecutor(default_registry)

    def run(metric):
        start.wait(timeout=10)
        return executor.run(metric, Source(), {})

    try:
        with ThreadPoolExecutor(max_workers=len(metrics)) as pool:
            results = list(pool.map(run, metrics))
        for metric, result in zip(metrics, results):
            assert not isinstance(result, MetricFailure), (metric, result)
        np.testing.assert_allclose(results[0].values, 0.1)
        np.testing.assert_allclose(results[3].values, 0.1)
        np.testing.assert_allclose(results[4].values, 0.1)
        np.testing.assert_allclose(results[5].values, 0.1)
        np.testing.assert_allclose(results[6].values, 0.1)
        assert results[2].values.shape == (301, 300)
        assert results[9].values.shape == (301, 300)
        assert (
            small_budget.used + small_budget.cache_bytes <= small_budget.limit
        )
    finally:
        executor.shutdown()


@pytest.mark.parametrize(
    "path",
    [
        "examples/data/fixed-sized molecular/reference_plus_noise.xyz",
        "examples/data/variable-sized molecular/dataset.xyz",
        "examples/data/variable-sized periodic/mptrj_sampled_over_200.extxyz",
    ],
)
def test_saving_has_identical_fingerprint_and_values(path):
    eager = dataset(path)
    saving = dataset(path, memory_mode="saving")
    assert eager.getFingerprint() == saving.getFingerprint()
    assert eager.isVariable == saving.isVariable
    assert eager.availableFieldKeys() == saving.availableFieldKeys()
    for i in (0, saving.N // 2, saving.N - 1):
        np.testing.assert_array_equal(
            eager.getCoordinates(i), saving.getCoordinates(i)
        )
        np.testing.assert_array_equal(
            eager.getElements(i), saving.getElements(i)
        )
        if eager.hasForces():
            np.testing.assert_array_equal(
                eager.getForces(i), saving.getForces(i)
            )


def test_changed_source_is_refused_even_with_cached_frames(
    tmp_path, small_budget
):
    path = write_xyz(tmp_path / "changing.xyz", n=10)
    ds = dataset(path, memory_mode="saving")
    Path(path).write_text(Path(path).read_text() + "\n")
    with pytest.raises(Exception, match="Reload the dataset"):
        ds.getCoordinates(0)


def test_saving_streams_difference_and_group_reductions(
    tmp_path, small_budget
):
    from ffast.metrics.expr import _ExprFn, _OFFSETS, _ELEMENTS
    from ffast.metrics.streaming import parity, smooth, kde
    from ffast.io.result_array import store_result

    ds = dataset(write_xyz(tmp_path / "large.xyz"), memory_mode="saving")
    difference = ds.F_flat - 0.05
    result = store_result(difference)
    assert isinstance(result, BatchedArray)
    np.testing.assert_allclose(result[:2], [[0.05, 0.15, 0.25]] * 2)
    fn = _ExprFn(
        "mean(abs(F), by=structure)", {"F": "reference.forces"}, {"F": 1}
    )
    values = fn(
        F=difference, **{_OFFSETS: ds.molecule_offsets, _ELEMENTS: ds.z_flat}
    )
    np.testing.assert_allclose(values, [[0.05, 0.15, 0.25]] * ds.N)
    fn = _ExprFn("rms(F, by=all)", {"F": "reference.forces"}, {"F": 1})
    np.testing.assert_allclose(fn(F=difference), [0.05, 0.15, 0.25])
    np.testing.assert_allclose(smooth(ds.E, 5)[:3], [2, 3, 4])
    assert parity(ds.F_flat, ds.F_flat).shape == (301, 300)
    assert kde(ds.E).shape == (2, 200)


@pytest.mark.parametrize("disk_bytes", [0, 8 * 2**20])
@pytest.mark.parametrize("keep", ["structure", "atom+structure"])
def test_real_metric_plan_streams_inputs_dependencies_and_results(
    tmp_path, small_budget, disk_bytes, keep
):
    from ffast.metrics import builtin
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.registry import default_registry
    from ffast.metrics.server_executor import ServerExecutor
    from ffast.metrics.models import MetricFailure

    # Per-atom output also needs room alongside parse/work buffers.
    n = 400 if keep == "atom+structure" else 700
    if keep == "atom+structure":
        small_budget.limit = 2 * 2**20
    ds = dataset(write_xyz(tmp_path / "large.xyz", n=n), memory_mode="saving")
    small_budget.result_limit = disk_bytes
    source = FlatInputSource(
        {
            "reference": ds.F_flat,
            "predicted": ds.F_flat + 0.1,
            "_offsets": ds.getOffsets(),
            "_elements": ds.z_flat,
        }
    )
    executor = ServerExecutor(default_registry)
    try:
        statistic = "mae" if keep == "atom+structure" else "rmse"
        result = executor.run(
            f"ffast.force_difference[{keep};component;{statistic}]", source, {}
        )
        assert not isinstance(result, MetricFailure), getattr(
            result, "traceback", ""
        )
        np.testing.assert_allclose(result.values, 0.1)
        assert result.values.shape == (
            (ds.N,) if keep == "structure" else (ds.N * 40,)
        )
        (difference,) = [
            r
            for r, _ in executor._cache._store.values()
            if r.metric_id == "ffast.force_difference"
        ]
        assert isinstance(difference.values, BatchedArray)
        assert not hasattr(difference.values, "result_path")
        if disk_bytes == 0 and keep == "structure":
            # A virtual dependency cached by this reduction must still pass
            # root-result admission when requested directly afterwards.
            too_large = executor.run("ffast.force_difference", source, {})
            assert isinstance(too_large, MetricFailure)
            assert too_large.kind == "temporary"
    finally:
        executor.shutdown()


def test_colouring_keeps_only_a_range_and_recomputes_one_frame(
    tmp_path, small_budget, monkeypatch
):
    from ffast.io.result_array import ResultWriter
    from ffast.metrics import builtin
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.registry import default_registry
    from ffast.metrics.server_executor import ServerExecutor
    from ffast.metrics.models import MetricFailure

    ds = dataset(write_xyz(tmp_path / "large.xyz"), memory_mode="saving")
    small_budget.result_limit = 0
    predicted = ds.F_flat + 0.1
    source = FlatInputSource(
        {
            "reference": ds.F_flat,
            "predicted": predicted,
            "_offsets": ds.getOffsets(),
            "_elements": ds.z_flat,
        }
    )

    def no_spill(*args, **kwargs):
        raise AssertionError("3D colouring must never store the whole output")

    monkeypatch.setattr(ResultWriter, "__init__", no_spill)
    executor = ServerExecutor(default_registry)
    try:
        metric = "ffast.force_difference[atom+structure;component;mae]"
        result = executor.run_colouring(metric, source, {})
        assert not isinstance(result, MetricFailure), getattr(
            result, "traceback", ""
        )
        assert isinstance(result.values, BatchedArray)
        assert not hasattr(result.values, "result_path")
        np.testing.assert_allclose(result._colour_span, [0.1, 0.1])
        np.testing.assert_allclose(result.values[123 * 40 : 124 * 40], 0.1)

        # The range is cached independently of full plot results.
        def no_reads(*args, **kwargs):
            raise AssertionError(
                "A cached colour range must not rescan the file"
            )

        monkeypatch.setattr(ds.storage, "_part", no_reads)
        assert executor.run_colouring(metric, source, {}) is result
    finally:
        executor.shutdown()


@pytest.mark.parametrize("masked", [False, True])
def test_colour_range_preserves_masked_bad_value_warnings_and_gaps(masked):
    from ffast.metrics.registry import MetricRegistry
    from ffast.metrics.expr import compile_expr_metric
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.server_executor import ServerExecutor
    from ffast.metrics.models import MetricFailure

    values = np.array([np.nan, -2 if masked else 2, -1, 5.0])
    reg = MetricRegistry()
    compile_expr_metric(
        "test.colour",
        "mean(F, by=structure, where=F>0)",
        {"F": "reference.atoms.q"},
        registry=reg,
    )
    source = FlatInputSource(
        {
            "F": BatchedArray(
                values.shape, values.dtype, lambda a, b: values[a:b]
            ),
            "_offsets": np.array([0, 2, 4]),
            "_elements": np.ones(4, int),
        }
    )
    executor = ServerExecutor(reg)
    try:
        result = executor.run_colouring("test.colour", source, {})
        assert not isinstance(result, MetricFailure), getattr(
            result, "traceback", ""
        )
        np.testing.assert_allclose(
            result._colour_span, [5 if masked else 2, 5]
        )
        np.testing.assert_allclose(
            result.values[:], [np.nan if masked else 2, 5]
        )
        assert result.warnings == [
            "1 bad value in rows left out by where= was ignored (frame 0)"
        ]
    finally:
        executor.shutdown()


def test_colour_summaries_do_not_replace_full_plot_results():
    from ffast.metrics.registry import MetricRegistry
    from ffast.metrics.expr import compile_expr_metric
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.server_executor import ServerExecutor

    reg = MetricRegistry()
    compile_expr_metric(
        "test.colour", "norm(F)", {"F": "reference.forces"}, registry=reg
    )
    source = FlatInputSource({"F": np.arange(15.0).reshape(5, 3)})
    source.uses = lambda: frozenset({("dataset", None)})
    executor = ServerExecutor(reg)
    try:
        colour_key, full_key = {}, {}
        colour = executor.run_colouring(
            "test.colour", source, {}, key_out=colour_key
        )
        full = executor.run("test.colour", source, {}, key_out=full_key)
        assert colour._colour_only and not full._colour_only
        assert colour_key != full_key
        assert isinstance(colour.values, BatchedArray)
        assert isinstance(full.values, np.ndarray)
        np.testing.assert_allclose(colour.values[:], full.values)
        executor.forget_objects({"dataset"})
        assert executor.cached(colour_key["key"]) is None
        assert executor.cached(full_key["key"]) is None
    finally:
        executor.shutdown()


def test_colour_range_refuses_unmasked_non_finite_values():
    from ffast.metrics.registry import MetricRegistry
    from ffast.metrics.expr import compile_expr_metric
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.server_executor import ServerExecutor
    from ffast.metrics.models import MetricFailure

    reg = MetricRegistry()
    compile_expr_metric(
        "test.bad", "norm(F) / 0", {"F": "reference.forces"}, registry=reg
    )
    executor = ServerExecutor(reg)
    try:
        with np.errstate(divide="ignore"):
            result = executor.run_colouring(
                "test.bad", FlatInputSource({"F": np.ones((4, 3))}), {}
            )
        assert isinstance(result, MetricFailure)
        assert "non-finite" in result.reason
    finally:
        executor.shutdown()


def test_single_prediction_runs_in_batches_without_materializing_dataset(
    tmp_path, small_budget
):
    from ffast.readers.predict_batches import predict
    from ffast.loaders.zero import ZeroModelLoader

    ds = dataset(write_xyz(tmp_path / "large.xyz"), memory_mode="saving")
    model = ZeroModelLoader.__new__(ZeroModelLoader)
    e, f = predict(model, ds)
    assert e.shape == (ds.N,)
    assert isinstance(f.values, BatchedArray)
    np.testing.assert_array_equal(e[:2], [0, 0])
    np.testing.assert_array_equal(f[123], np.zeros((40, 3)))


@pytest.mark.parametrize("mode", ["auto", "saving"])
def test_small_predictions_work_without_result_disk(
    tmp_path, small_budget, mode
):
    from ffast.readers.predict_batches import predict
    from ffast.loaders.zero import ZeroModelLoader

    small_budget.result_limit = 0
    ds = dataset(write_xyz(tmp_path / "small.xyz", n=10), memory_mode=mode)
    e, f = predict(ZeroModelLoader.__new__(ZeroModelLoader), ds)
    np.testing.assert_array_equal(e, np.zeros(10))
    np.testing.assert_array_equal(f.values, np.zeros((400, 3)))


def test_unwritable_parsed_cache_keeps_source_access(
    tmp_path, small_budget, monkeypatch
):
    from ffast.io import frame_cache

    small_budget.parsed_limit = 2**20
    blocked = tmp_path / "cache-is-a-file"
    blocked.write_text("occupied")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", blocked)
    path = write_xyz(tmp_path / "large.xyz")
    ds = ReaderDataset(
        path,
        chunks.read_arrays(XyzReader, path, workers=1, memory_mode="saving"),
        "xyz",
    )
    assert ds.getCoordinates(456)[0, 0] == 456.125


def test_cancel_between_batches_keeps_dataset_unpublished(
    tmp_path, small_budget
):
    from ffast.metrics import stop

    path = write_xyz(tmp_path / "large.xyz")
    cancelled = [False]
    token = stop.running_under(lambda: cancelled[0])
    try:
        with pytest.raises(stop.Stopped):
            chunks.read_arrays(
                XyzReader,
                path,
                memory_mode="saving",
                workers=1,
                use_cache=False,
                progress=lambda *_: cancelled.__setitem__(0, True),
            )
    finally:
        stop.reset(token)


@pytest.mark.parametrize("backend", ["fast", "python"])
def test_uncached_random_access_parses_only_the_requested_frame(
    tmp_path, small_budget, monkeypatch, backend
):
    from ffast.readers import text

    if backend == "python":
        monkeypatch.setattr(text, "fast_parser_usable", lambda: False)
    ds = dataset(write_xyz(tmp_path / "large.xyz"), memory_mode="saving")
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    original = XyzReader.parse_into
    original_python = XyzReader.parse
    asked = []

    def parse(*args):
        result = original(*args)
        asked.append(len(args[1]))
        return result

    def parse_python(*args):
        result = original_python(*args)
        asked.append(len(result["n_atoms"]))
        return result

    monkeypatch.setattr(XyzReader, "parse_into", staticmethod(parse))
    monkeypatch.setattr(XyzReader, "parse", staticmethod(parse_python))
    assert ds.getCoordinates(123)[0, 0] == 123.125
    assert asked == [1]


def test_disk_cache_reads_rows_without_parsing_or_loading_entire_batch(
    tmp_path, small_budget, monkeypatch
):
    from ffast.io import frame_cache

    small_budget.parsed_limit = 4 * 2**20
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "large.xyz")
    first = chunks.read_arrays(
        XyzReader, path, memory_mode="saving", workers=1
    )
    small_budget.cache.clear()
    small_budget.cache_bytes = 0

    def fail(*args, **kwargs):
        raise AssertionError("A warm parsed cache must not parse XYZ")

    monkeypatch.setattr(XyzReader, "parse_into", staticmethod(fail))
    monkeypatch.setattr(XyzReader, "parse", staticmethod(fail))
    second = chunks.read_arrays(
        XyzReader, path, memory_mode="saving", workers=1
    )
    ds = ReaderDataset(path, second, "xyz")
    assert ds.getCoordinates(123)[0, 0] == 123.125
    assert ds.getEnergies(123) == 123
    assert (
        not small_budget.cache
    )  # neither a full batch nor a parse was needed


def test_damaged_cached_batch_falls_back_to_the_source(
    tmp_path, small_budget, monkeypatch
):
    from ffast.io import frame_cache

    small_budget.parsed_limit = 4 * 2**20
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "large.xyz")
    first = chunks.read_arrays(
        XyzReader, path, memory_mode="saving", workers=1
    )
    store = first.arrays.store
    cached = list(Path(store.folder).glob("*.npz"))
    assert cached
    for file in cached:
        file.write_bytes(file.read_bytes()[:200])  # cut off mid-file
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    second = chunks.read_arrays(
        XyzReader, path, memory_mode="saving", workers=1
    )
    ds = ReaderDataset(path, second, "xyz")
    assert ds.getCoordinates(123)[0, 0] == 123.125
    assert ds.getEnergies(123) == 123
    assert ds.getForces(4).shape == (40, 3)
    # Reading a whole damaged batch directly must also fall back to parsing.
    for file in cached:
        if "--" in file.stem:  # a column file, not a frame batch
            continue
        a, b = (int(x) for x in file.stem.rsplit("-", 2)[1:])
        file.write_bytes(b"PK\x03\x04 cut off")
        small_budget.cache.clear()
        small_budget.cache_bytes = 0
        part = second.arrays.store._part(a, b)
        key = next(k for k in part if k.startswith("energy"))
        np.testing.assert_array_equal(
            np.ravel(part[key]), np.arange(a, b)
        )


def test_parsed_partition_survives_a_changed_ram_budget(tmp_path, small_budget, monkeypatch):
    from ffast.io import frame_cache

    small_budget.limit = 8 * 2**20
    small_budget.parsed_limit = 8 * 2**20
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "partition.xyz", n=1200)
    first = chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)
    ranges = first.arrays.store.ranges
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    small_budget.limit -= 2**20

    def fail(*args, **kwargs):
        raise AssertionError("A changed RAM budget must not invalidate parsed batches")

    monkeypatch.setattr(XyzReader, "parse_into", staticmethod(fail))
    monkeypatch.setattr(XyzReader, "parse", staticmethod(fail))
    second = chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)
    assert second.arrays.store.ranges == ranges
    ds = ReaderDataset(path, second, "xyz")
    assert ds.getCoordinates(123)[0, 0] == 123.125


def test_reused_small_energy_column_does_not_reparse(tmp_path, small_budget, monkeypatch):
    small_budget.limit = 8 * 2**20
    ds = dataset(write_xyz(tmp_path / "energies.xyz", n=1200), memory_mode="saving")
    small_budget.cache.clear()
    small_budget.cache_bytes = 0

    def fail(*args, **kwargs):
        raise AssertionError("Small energy columns must survive the initial scan")

    monkeypatch.setattr(ds.storage, "_part", fail)
    monkeypatch.setattr(ds.storage, "_cached_slice", fail)
    np.testing.assert_array_equal(np.asarray(ds.E), np.arange(1200))
    np.testing.assert_array_equal(np.asarray(ds.E), np.arange(1200))


def test_index_and_parse_windows_overlap_without_a_second_scan(tmp_path, small_budget, monkeypatch):
    from ffast.io import frame_index, frame_cache
    from ffast.readers.indexed import IndexedStore, IndexedArrays
    from ffast.readers import text
    from threading import Event

    if not text.fast_parser_usable():
        pytest.skip("Optional fast parser unavailable")
    small_budget.limit = 8 * 2**20
    small_budget.parsed_limit = 8 * 2**20
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "pipeline.xyz", n=1500)
    eager = dataset(path)
    expected_fp, expected_variable = eager.getFingerprint(), eager.isVariable
    expected = {frame: (eager.getCoordinates(frame).copy(), eager.getForces(frame).copy())
                for frame in (0, 731, 1499)}
    del eager
    gc.collect()
    original = text._read_fast_arrays
    parsing_started = Event()
    parsed = []

    def parse(*args, **kwargs):
        parsed.append(len(args[3]))
        parsing_started.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(text, "_read_fast_arrays", parse)
    native = frame_index._fast_finder()
    from types import SimpleNamespace
    find_calls = []

    def find(*args):
        if find_calls:
            assert parsing_started.wait(5), "Parsing must start before the full index is finished"
        find_calls.append(1)
        return native.find_frames(*args)

    monkeypatch.setattr(frame_index, "_fast_finder", lambda: SimpleNamespace(
        find_frames=find, FIND_MORE=native.FIND_MORE, FIND_END=native.FIND_END,
        FIND_PYTHON=native.FIND_PYTHON))
    store = IndexedStore(XyzReader, path, XyzReader(path).contents(), [], [], [], [],
        workers=1, scan_source=True)
    loaded = chunks.Loaded(IndexedArrays(store), store.contents,
                          list(range(len(store.counts))), len(store.counts), "xyz")
    pipelined = ReaderDataset(path, loaded, "xyz")
    assert len(store.ranges) > 1
    assert pipelined.getFingerprint() == expected_fp
    assert pipelined.isVariable == expected_variable
    for frame in (0, 731, 1499):
        np.testing.assert_array_equal(pipelined.getCoordinates(frame), expected[frame][0])
        np.testing.assert_array_equal(pipelined.getForces(frame), expected[frame][1])
    np.testing.assert_array_equal(np.asarray(pipelined.E), np.arange(1500))
    assert frame_index.load_cached(path) is not None


def test_bulk_columns_share_canonical_parse_windows(tmp_path, small_budget, monkeypatch):
    from ffast.readers import text

    small_budget.limit = 128 * 2**20
    # A selection keeps the indexed path, whose windows follow the budget.
    ds = dataset(write_xyz(tmp_path / "bulk.xyz", n=5000), memory_mode="saving",
                 select=lambda n: list(range(n)))
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    original = text._read_fast_arrays
    calls = []

    def parse(*args, **kwargs):
        calls.append(len(args[3]))
        return original(*args, **kwargs)

    monkeypatch.setattr(text, "_read_fast_arrays", parse)
    np.testing.assert_allclose(np.asarray(ds.F_flat), np.tile([.1, .2, .3], (200000, 1)))
    assert len(calls) <= len(ds.storage.ranges)


def test_bulk_cache_misses_do_not_evict_the_rest_of_a_large_scan(tmp_path, small_budget, monkeypatch):
    from ffast.io import frame_cache

    small_budget.limit = 128 * 2**20
    small_budget.parsed_limit = 4 * 2**20
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "pressure.xyz", n=5000)
    loaded = chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)
    ds = ReaderDataset(path, loaded, "xyz")
    folder = tmp_path / "parsed" / "batches"
    before = {p.name for p in folder.glob("*") if p.suffix in (".json", ".npz")}
    assert any(p.endswith(".json") for p in before)
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    np.testing.assert_allclose(np.asarray(ds.F_flat), np.tile([.1, .2, .3], (200000, 1)))
    after = {p.name for p in folder.glob("*") if p.suffix in (".json", ".npz")}
    assert after == before


def test_shrinking_disk_allowance_keeps_the_manifest(tmp_path, small_budget, monkeypatch):
    from ffast.io import frame_cache
    from ffast.readers import indexed
    from types import SimpleNamespace

    small_budget.limit = 8 * 2**20
    small_budget.parsed_limit = 8 * 2**20
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "disk-pressure.xyz", n=1500)
    first = chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)
    store = first.arrays.store
    manifest = store.folder / f"{store.identity}.json"
    manifest.unlink()
    used = sum(p.stat().st_size for p in store.folder.iterdir() if p.is_file())
    monkeypatch.setattr(indexed.shutil, "disk_usage", lambda path: SimpleNamespace(free=4 * (used - 1024)))
    store._save_manifest()
    assert manifest.is_file()
    small_budget.cache.clear()
    small_budget.cache_bytes = 0

    def fail(*args, **kwargs):
        raise AssertionError("The manifest must prevent another full scan")

    monkeypatch.setattr(XyzReader, "parse_into", staticmethod(fail))
    second = chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)
    assert second.arrays.store.info["positions"]["digest"] == store.info["positions"]["digest"]


def test_windowed_load_falls_back_without_dropping_a_python_count_frame(tmp_path, small_budget, monkeypatch):
    from ffast.readers import text
    from ffast.io import frame_index, frame_cache

    if not text.fast_parser_usable():
        pytest.skip("Optional fast parser unavailable")
    small_budget.limit = 8 * 2**20
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "signed-count.xyz", n=1500)
    lines = Path(path).read_text().splitlines(keepends=True)
    lines[800 * 42] = "+40\n"
    Path(path).write_text("".join(lines))
    expected = dataset(path, memory_mode="saving")
    fingerprint = expected.getFingerprint()
    del expected
    gc.collect()
    monkeypatch.setattr(text, "can_scan_and_read", lambda *args, **kwargs: True)
    got = dataset(path, memory_mode="saving")
    assert got.N == 1500
    assert got.getFingerprint() == fingerprint
    assert got.getCoordinates(800)[0, 0] == 800.125


def test_cancelled_pipeline_does_not_publish_partial_metadata(tmp_path, small_budget, monkeypatch):
    from ffast.io import frame_index, frame_cache
    from ffast.readers.indexed import IndexedStore
    from ffast.readers import text
    from ffast.metrics.stop import Stopped

    if not text.fast_parser_usable():
        pytest.skip("Optional fast parser unavailable")
    small_budget.limit = 8 * 2**20
    small_budget.parsed_limit = 8 * 2**20
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "cancel.xyz", n=1500)

    def progress(done, total):
        pass

    def cancel(*args):
        raise Stopped("cancelled")

    progress.bytes_read = cancel
    with pytest.raises(Stopped):
        IndexedStore(XyzReader, path, XyzReader(path).contents(), [], [], [], [],
                     workers=1, scan_source=True, progress=progress)
    assert frame_index.load_cached(path) is None
    assert not list((tmp_path / "parsed" / "batches").glob("*.npz"))
    assert not list((tmp_path / "parsed" / "batches").glob("*.json"))


def test_missing_and_mixed_integer_energies_preserve_fingerprint(tmp_path):
    path = tmp_path / "mixed.xyz"
    path.write_text(
        "1\nenergy=-5\nH 0 0 0\n1\ncomment\nH 1 0 0\n1\nenergy=-5.2\nH 2 0 0\n"
    )
    eager = dataset(str(path))
    saving = dataset(str(path), memory_mode="saving")
    assert eager.getFingerprint() == saving.getFingerprint()
    assert eager.E.dtype == saving.E.dtype
    np.testing.assert_array_equal(eager.E, saving.E)


@pytest.mark.parametrize("attach", ["columns", "file"])
def test_same_file_prediction_uses_scanned_columns_without_reads(
    tmp_path, small_budget, monkeypatch, attach
):
    from ffast.cache.fingerprint import md5FromArraysAndStrings
    from ffast.core.loading_coordinator import LoadingCoordinator
    from tests.ffast.test_loading_coordinator import _file_env

    path = write_xyz(tmp_path / "large.xyz")
    ds = dataset(path, memory_mode="saving")
    ds.fingerprint = ds.getFingerprint()
    env = _file_env(ds)
    coord = LoadingCoordinator(env)
    coord.lookForGhosts = lambda: None
    expected = [
        np.arange(ds.N, dtype=float),
        np.tile([0.1, 0.2, 0.3], (ds.N * 40, 1)),
    ]
    if attach == "columns":
        expected.append("in-file")
    expected_fp = md5FromArraysAndStrings(*expected)

    def fail(*args, **kwargs):
        raise AssertionError(
            "The dataset already scanned these prediction columns"
        )

    monkeypatch.setattr(ds.storage, "_part", fail)
    monkeypatch.setattr(ds.storage, "_cached_slice", fail)
    monkeypatch.setattr(coord, "_readArrays", fail)
    if attach == "columns":
        coord._loadReaderPredictionsFromKeys(
            ds, path, [("energy", "forces", "in-file")]
        )
    else:
        ds.arrays = None  # As after the dataset load has completed.
        coord._loadReaderPrediction(
            XyzReader, path, "ds-fp", None, "energy", "forces", None
        )
    assert expected_fp in env.objects.registered
    # Reusing fingerprints must still refuse a changed source file.
    import os

    state = os.stat(path)
    os.utime(path, ns=(state.st_atime_ns, state.st_mtime_ns + 1_000_000))
    if attach == "columns":
        with pytest.raises(Exception, match="Reload the dataset"):
            coord._loadReaderPredictionsFromKeys(
                ds, path, [("energy", "forces", "changed")]
            )
    else:
        errors = []
        coord._loadReaderPrediction(
            XyzReader, path, "ds-fp", None, "energy", "forces", errors.append
        )
        assert len(errors) == 1 and "Reload the dataset" in errors[0]


def test_an_oversized_result_only_refuses_the_operation(
    tmp_path, small_budget
):
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.registry import default_registry
    from ffast.metrics.server_executor import ServerExecutor
    from ffast.metrics.models import MetricFailure
    from ffast.metrics import builtin

    ds = dataset(write_xyz(tmp_path / "large.xyz"), memory_mode="saving")
    small_budget.result_limit = 0
    source = FlatInputSource(
        {"reference": ds.F_flat, "predicted": ds.F_flat + 0.1}
    )
    executor = ServerExecutor(default_registry)
    result = executor.run("ffast.force_difference", source, {})
    assert isinstance(result, MetricFailure)
    assert result.kind == "temporary", result.traceback
    assert ds.getCoordinates(123)[0, 0] == 123.125


def _cached_batches(store):
    import zipfile

    out = []
    for file in Path(store.folder).glob(store.identity + "-*.npz"):
        if "--" in file.stem:  # the small-column file, not a frame batch
            continue
        with zipfile.ZipFile(file) as archive:
            out.append({n[:-4] for n in archive.namelist()})
    return out


def _cached_store(tmp_path, small_budget, monkeypatch, parsed_limit):
    from ffast.io import frame_cache

    small_budget.limit = 8 * 2**20  # realistic windows, not 12-frame ones
    small_budget.parsed_limit = parsed_limit
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    path = write_xyz(tmp_path / "large.xyz")
    loaded = chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    return path, loaded.arrays.store


def _forbid_parsing(monkeypatch, store):
    def fail(*args, **kwargs):
        raise AssertionError("must be served from the parsed cache")

    monkeypatch.setattr(store, "_part", fail)


def test_cache_keeps_species_and_forces_before_positions(tmp_path, small_budget, monkeypatch):
    """ING-05 level order: per-frame values and species, then forces, then positions."""
    # 28,000 atoms: forces 0.67 MB, positions 0.67 MB, species 28 kB as uint8.
    path, store = _cached_store(tmp_path, small_budget, monkeypatch, 1200 * 1024)
    batches = _cached_batches(store)
    assert batches
    for names in batches:
        assert {"numbers", "forces:forces", "energy:energy"} <= names
        assert "positions" not in names and "__omitted__" in names
    eager = dataset(path)
    _forbid_parsing(monkeypatch, store)
    numbers = np.asarray(store.column("numbers")[:])
    assert numbers.dtype == eager.arrays["numbers"].dtype
    np.testing.assert_array_equal(numbers, eager.arrays["numbers"])
    np.testing.assert_array_equal(
        np.asarray(store.column("forces:forces")[:]), eager.arrays["forces:forces"])


def test_cache_keeps_species_when_forces_do_not_fit(tmp_path, small_budget, monkeypatch):
    path, store = _cached_store(tmp_path, small_budget, monkeypatch, 300 * 1024)
    batches = _cached_batches(store)
    assert batches
    for names in batches:
        assert "numbers" in names and "energy:energy" in names
        assert not any(n.startswith("forces:") for n in names) and "positions" not in names
    eager = dataset(path)
    ds = ReaderDataset(path, chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1), "xyz")
    # Omitted columns come back from the source with their real values, never NaN.
    np.testing.assert_array_equal(ds.getForces(321), eager.getForces(321))
    np.testing.assert_array_equal(ds.getCoordinates(321), eager.getCoordinates(321))


def test_cache_keeps_every_column_when_there_is_room(tmp_path, small_budget, monkeypatch):
    path, store = _cached_store(tmp_path, small_budget, monkeypatch, 8 * 2**20)
    for names in _cached_batches(store):
        assert {"numbers", "positions", "forces:forces"} <= names
        assert "__omitted__" not in names
    eager = dataset(path)
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    a, b = store.ranges[0] if getattr(store, "ranges", None) else next(iter(store._ranges()))
    part = store._part(a, b)
    assert part["numbers"].dtype == eager.arrays["numbers"].dtype
    assert "__restore__" not in part


def test_saving_a_batch_sweeps_only_old_temp_files(tmp_path, small_budget, monkeypatch):
    """A crashed write leaves a .tmp file; one older than an hour is removed."""
    import os
    import time

    path, store = _cached_store(tmp_path, small_budget, monkeypatch, 8 * 2**20)
    old = Path(store.folder) / "crashed.tmp"
    young = Path(store.folder) / "writing-now.tmp"
    old.write_bytes(b"x")
    young.write_bytes(b"x")
    two_hours_ago = time.time() - 2 * 3600
    os.utime(old, (two_hours_ago, two_hours_ago))
    store._save(0, 1, {"energy:energy": np.zeros(1)})
    assert not old.exists()
    assert young.exists()


def test_cache_never_evicts_files_of_an_open_dataset(tmp_path, small_budget, monkeypatch):
    """A second dataset that does not fit must not delete the batches of an open one."""
    path, store = _cached_store(tmp_path, small_budget, monkeypatch, 8 * 2**20)
    mine = sorted(Path(store.folder).glob(store.identity + "*"))
    assert mine
    # Room for dataset A only: there is no space left for dataset B.
    small_budget.parsed_limit = sum(f.stat().st_size for f in mine) + 4096
    other = write_xyz(tmp_path / "other.xyz", n=300)
    chunks.read_arrays(XyzReader, other, memory_mode="saving", workers=1)
    assert all(f.exists() for f in mine)
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    _forbid_parsing(monkeypatch, store)
    eager = dataset(path)
    np.testing.assert_array_equal(
        np.asarray(store.column("forces:forces")[:]), eager.arrays["forces:forces"])


# ── one memory-saving path (ADR 0106) ───────────────────────────────────────


@pytest.fixture
def scan_spy(monkeypatch):
    """Names the paths a load took: the one-pass scan, or the indexed store."""
    from ffast.readers import indexed, text

    if not text.fast_parser_usable():
        pytest.skip("Optional fast parser unavailable")
    calls = []
    original = indexed.IndexedStore._scan_source

    def spy(self, progress):
        calls.append("one-pass")
        return original(self, progress)

    monkeypatch.setattr(indexed.IndexedStore, "_scan_source", spy)
    return calls


def _caches(monkeypatch, tmp_path):
    from ffast.io import frame_cache, frame_index

    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")


def test_saving_mode_takes_the_one_pass_path_for_a_small_file(
    tmp_path, small_budget, monkeypatch, scan_spy
):
    _caches(monkeypatch, tmp_path)
    small_budget.limit = 64 * 2**20          # the file is far below twice this
    ds = dataset(write_xyz(tmp_path / "small.xyz", n=300), memory_mode="saving")
    assert scan_spy == ["one-pass"]
    assert ds.N == 300


def test_an_auto_load_that_does_not_fit_takes_the_one_pass_path_with_an_index_on_disk(
    tmp_path, small_budget, monkeypatch, scan_spy
):
    from ffast.io import frame_index

    _caches(monkeypatch, tmp_path)
    small_budget.limit = 16 * 2**20
    path = write_xyz(tmp_path / "medium.xyz", n=6000)
    assert Path(path).stat().st_size < 2 * small_budget.limit   # indexed to decide
    frame_index.get_frame_index(path)
    assert frame_index.has_cached_or_running_index(path)
    ds = dataset(path)
    assert ds.memory_mode == "saving"
    assert scan_spy == ["one-pass"]


def test_a_strided_saving_load_keeps_the_indexed_path(
    tmp_path, small_budget, monkeypatch, scan_spy
):
    _caches(monkeypatch, tmp_path)
    small_budget.limit = 64 * 2**20
    ds = dataset(write_xyz(tmp_path / "strided.xyz", n=300), memory_mode="saving", stride=2)
    assert scan_spy == []
    assert ds.memory_mode == "saving"
    assert ds.N == 150


def test_two_budgets_that_both_save_leave_the_same_batch_files(
    tmp_path, monkeypatch, scan_spy
):
    _caches(monkeypatch, tmp_path)
    path = write_xyz(tmp_path / "same.xyz", n=300)
    names = []
    for gib in (3, 5):
        owner = resources.Budget(ram_bytes=gib * 2**30, parsed_bytes=2**30,
                                 result_bytes=8 * 2**20)
        monkeypatch.setattr(resources, "_budget", owner)
        shutil_rmtree(tmp_path / "parsed")
        shutil_rmtree(tmp_path / "index")
        ds = dataset_cached(path)
        folder = ds.storage.folder
        names.append(sorted(p.name for p in folder.glob("*.npz")))
        del ds
        gc.collect()
    assert names[0] and names[0] == names[1]
    assert scan_spy == ["one-pass", "one-pass"]


def shutil_rmtree(folder):
    import shutil

    shutil.rmtree(folder, ignore_errors=True)


def dataset_cached(path, **kwargs):
    return ReaderDataset(
        path,
        chunks.read_arrays(XyzReader, path, workers=1, memory_mode="saving", **kwargs),
        "xyz",
    )


def test_a_full_array_load_refused_for_memory_starts_again_in_saving_mode(
    tmp_path, small_budget, monkeypatch, scan_spy
):
    from ffast.readers import text

    _caches(monkeypatch, tmp_path)
    small_budget.limit = 512 * 2**20          # the estimate fits: full arrays are planned

    def refuse(*args, **kwargs):
        raise NotEnoughMemory("free memory fell short")

    monkeypatch.setattr(text, "_read_fast", refuse)
    ds = dataset(write_xyz(tmp_path / "late.xyz", n=300))
    assert ds.memory_mode == "saving"
    assert scan_spy == ["one-pass"]


# ── cache levels of a one-pass scan follow the projected file size (ADR 0106) ─


def _scan_store(tmp_path, small_budget, monkeypatch, parsed_limit, path):
    """A one-pass scan with 1 MiB windows, so a few thousand frames make several."""
    from ffast.io import frame_cache, frame_index
    from ffast.readers import text

    if not text.fast_parser_usable():
        pytest.skip("Optional fast parser unavailable")
    small_budget.limit = 16 * 2**20
    small_budget.parsed_limit = parsed_limit
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    loaded = chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)
    small_budget.cache.clear()
    small_budget.cache_bytes = 0
    store = loaded.arrays.store
    assert len(store.ranges) > 2
    return loaded, store


def _batch_names(store):
    import zipfile

    out = []
    for a, b in store.ranges:
        with zipfile.ZipFile(store._cache_path(a, b)) as archive:
            out.append({n[:-4] for n in archive.namelist()})
    return out


def test_a_one_pass_scan_with_room_keeps_positions_in_every_batch(
    tmp_path, small_budget, monkeypatch
):
    path = write_xyz(tmp_path / "room.xyz", n=3000)
    _, store = _scan_store(tmp_path, small_budget, monkeypatch, 64 * 2**20, path)
    for names in _batch_names(store):
        assert {"numbers", "positions", "forces:forces"} <= names
        assert "__omitted__" not in names


def test_a_one_pass_scan_between_two_levels_keeps_forces_but_not_positions(
    tmp_path, small_budget, monkeypatch
):
    path = write_xyz(tmp_path / "between.xyz", n=3000)
    size = Path(path).stat().st_size
    # The whole cache at level 3 is about 1.6x the file, level 2 about 0.6x.
    _, store = _scan_store(tmp_path, small_budget, monkeypatch, int(size * 1.0), path)
    for names in _batch_names(store):
        assert {"numbers", "forces:forces", "energy:energy"} <= names
        assert "positions" not in names and "__omitted__" in names


def _padded_xyz(path, parts):
    """``parts`` is (frames, padding bytes in the comment line) per stretch of
    the file: a long comment makes the file bigger for the same parsed data."""
    atoms = 40
    with open(path, "w") as f:
        i = 0
        for frames, pad in parts:
            for _ in range(frames):
                f.write(f"{atoms}\nProperties=species:S:1:pos:R:3:forces:R:3 energy={i} "
                        f"note={'x' * max(1, pad)}\n")
                for j in range(atoms):
                    f.write(f"C {i}.125 {j}.25 -1.5 0.1 0.2 0.3\n")
                i += 1
    return str(path)


def test_a_projection_that_grows_mid_scan_drops_a_level_and_never_adds_it_back(
    tmp_path, small_budget, monkeypatch
):
    # Stretch 1 has little parsed data per byte of text; stretch 2 has more,
    # so the projected cache grows past the allowance; stretch 3 is like 1.
    path = _padded_xyz(tmp_path / "grows.xyz", [(1000, 1500), (1000, 0), (1000, 1500)])
    _, store = _scan_store(tmp_path, small_budget, monkeypatch, 6 * 2**20, path)
    kept = ["positions" in names for names in _batch_names(store)]
    assert kept[0] and not kept[-1]
    assert kept == sorted(kept, reverse=True)       # True ... True False ... False


def test_a_column_left_out_of_a_one_pass_cache_is_read_from_the_source(
    tmp_path, small_budget, monkeypatch
):
    path = write_xyz(tmp_path / "left-out.xyz", n=3000)
    size = Path(path).stat().st_size
    loaded, store = _scan_store(tmp_path, small_budget, monkeypatch, int(size * 1.0), path)
    expected = dataset(path)
    np.testing.assert_array_equal(
        np.asarray(store.column("positions")[:]), np.asarray(expected.R_flat))
