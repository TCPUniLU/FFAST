"""Suite-wide fixtures."""
import pytest


@pytest.fixture(autouse=True, scope="session")
def _live_records_in_tmp(tmp_path_factory):
    """Making a reference records it under ~/.ffast/references, and Run with
    FFAST remembers folders in ~/.ffast/run-with-ffast.json; tests (and the
    servers they spawn) use temporary ones. Session-wide: module-scoped
    fixtures that make bundles run before function-scoped ones."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FFAST_REFERENCES", str(tmp_path_factory.mktemp("references")))
        mp.setenv("FFAST_RUN_WITH_FFAST", str(tmp_path_factory.mktemp("rwf") / "run-with-ffast.json"))
        yield


@pytest.fixture(autouse=True)
def _frame_index_cache_in_tmp(tmp_path_factory, monkeypatch):
    """Loading an .xyz file saves its frame index under ~/.ffast/frame-index,
    and its arrays under ~/.ffast/frame-arrays; a test saves them in temporary
    folders instead, never in the real home."""
    try:
        from ffast.io import frame_index
    except ImportError:          # before the frame index exists
        yield
        return
    folder = tmp_path_factory.mktemp("frame-index")
    monkeypatch.setattr(frame_index, "CACHE_DIR", folder)
    # Servers the test spawns start from this environment; they read the folder too.
    monkeypatch.setenv("FFAST_FRAME_INDEX_DIR", str(folder))
    from ffast.io import frame_cache
    arrays = tmp_path_factory.mktemp("frame-arrays")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", arrays)
    monkeypatch.setenv("FFAST_FRAME_ARRAYS_DIR", str(arrays))
    yield


@pytest.fixture(autouse=True)
def _event_bus_as_it_was():
    """The in-process event bus is one subscriber table per process, holding
    subscribers strongly (one Environment per process, by design). Every
    Environment, TaskManager or listener a test subscribes is dropped after
    it, so it cannot pile up nor run another test's event."""
    from tests.event_bus import restore_subscriptions, save_subscriptions
    saved = save_subscriptions()
    yield
    restore_subscriptions(saved)
