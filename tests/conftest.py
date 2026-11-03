"""Suite-wide fixtures."""
import pytest


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
