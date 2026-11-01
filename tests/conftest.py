"""Suite-wide fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _frame_index_cache_in_tmp(tmp_path_factory, monkeypatch):
    """Loading an .xyz file saves its frame index under ~/.ffast/frame-index;
    a test saves it in a temporary folder instead, never in the real home."""
    try:
        from ffast.io import frame_index
    except ImportError:          # before the frame index exists
        yield
        return
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path_factory.mktemp("frame-index"))
    yield
