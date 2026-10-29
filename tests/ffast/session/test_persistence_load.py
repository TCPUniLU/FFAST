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
