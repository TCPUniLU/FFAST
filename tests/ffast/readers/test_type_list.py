"""The Type list the page shows is the server's dataset loaders, then its readers."""
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.window_feed import WindowFeed


class _Env:
    datasets = {}
    models = {}
    cache = {}
    modelTypes = {}
    datasetTypes = {"ase (auto)": type("A", (), {"datasetFileExtension": "*"})()}


def _rows(env):
    event, _, kw = unpack(WindowFeed(env, lambda d: None).loader_types())
    assert event == control.LOADER_TYPES
    return kw["datasets"]


def test_a_reader_is_listed_after_the_dataset_types_with_all_its_endings(toy):
    assert _rows(_Env()) == [
        {"name": "ase (auto)", "extension": "*"},
        {"name": "toy", "extension": "*.toy", "extensions": ["*.toy"], "reader": True},
    ]


def test_a_reader_named_like_a_dataset_type_does_not_replace_or_double_it(toy):
    from ffast.readers import registry
    registry.unregister("toy")
    registry.register(type("Ase", (toy,), {"name": "ase (auto)", "extensions": ["*.toy"]}))
    assert _rows(_Env()) == [{"name": "ase (auto)", "extension": "*"}]
