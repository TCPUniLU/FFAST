"""Loading a session from somewhere that is not a saved session (ADR 0063
follow-up): it raises, so the window's ack says why instead of "Loaded"."""
from types import SimpleNamespace

import pytest

from ffast.session.persistence import SessionPersistence


@pytest.mark.parametrize("make_path", [
    lambda tmp: tmp / "nonexistent" / "x",     # no such folder
    lambda tmp: tmp,                           # a folder, but no info.json
])
def test_loading_where_no_session_was_saved_raises(tmp_path, make_path):
    path = make_path(tmp_path)

    with pytest.raises(FileNotFoundError, match="No saved session at"):
        SessionPersistence(SimpleNamespace()).load(str(path))


def test_a_session_saved_before_the_fingerprint_change_says_so_once(tmp_path, caplog):
    """ADR 0107: the file loads, but under a new fingerprint, so the units and
    cached predictions keyed by the old one are not found. One warning."""
    import json
    import logging

    data = tmp_path / "a.xyz"
    data.write_text("1\n\nH 0 0 0\n")
    other = tmp_path / "b.xyz"
    other.write_text("1\n\nH 0 0 0\n")
    session = tmp_path / "session"
    session.mkdir()
    objects = {fp: {"type": "dataset", "name": p.name, "path": str(p), "units": {"energy": "eV"}}
               for fp, p in (("old-fingerprint-a", data), ("old-fingerprint-b", other))}
    (session / "info.json").write_text(json.dumps({"objects": objects}))

    env = SimpleNamespace(
        objects=SimpleNamespace(load=lambda info: None),
        datasetTypes={"ase (auto)": object},
        datasets=SimpleNamespace(get=lambda fingerprint: None),
        loadDataset=lambda *a, **k: None,
        getDatasetFromPath=lambda path: object(),
        lookForGhosts=lambda: None,
        cache={},
    )
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        SessionPersistence(env).load(str(session))

    warnings = [r.getMessage() for r in caplog.records if "ADR 0107" in r.getMessage()]
    assert warnings == [
        "session was saved before FFAST changed its fingerprints (ADR 0107); units and "
        "cached predictions saved with it are not restored."]
