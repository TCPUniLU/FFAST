"""A one-frame xyz file must load (homogeneity check needs <3 frames)."""
from types import SimpleNamespace

from ffast.plugins.loaders.ase import loadData

XYZ = """3
comment
O 0.0 0.0 0.0
H 0.9 0.0 0.0
H -0.3 0.9 0.0
"""


def _loader():
    captured = []
    loadData(SimpleNamespace(initialiseDatasetType=captured.append))
    return captured[0]


def test_single_frame_file_loads(tmp_path):
    path = tmp_path / "one.xyz"
    path.write_text(XYZ)
    loader, _ = _loader()(str(path), show_dialog=False)
    assert loader is not None
