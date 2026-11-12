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


def test_a_reader_takes_the_row_of_the_dataset_type_of_its_name_and_the_others_follow(toy):
    assert _rows(_Env()) == [
        {"name": "ase (auto)", "extension": "*", "extensions": ["*"], "reader": True},
        {"name": "sGDML", "extension": "*.npz", "extensions": ["*.npz"], "reader": True},
        {"name": "toy", "extension": "*.toy", "extensions": ["*.toy"], "reader": True},
    ]


def test_a_dataset_type_without_a_reader_keeps_its_row(toy):
    env = _Env()
    env.datasetTypes = {**env.datasetTypes, "Other": type("O", (), {"datasetFileExtension": "*.oth"})}
    assert {"name": "Other", "extension": "*.oth"} in _rows(env)
