"""The code fingerprint a server sends in HELLO_ACK, and the client's warning."""
from pathlib import Path

from ffast.session.code_fingerprint import (
    code_fingerprint,
    fingerprint_of,
    server_code_notice,
)
from ffast.visualization.protocol import ClientCapabilities, negotiate


def _tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


BASE = {
    "ffast/__init__.py": "",
    "ffast/metrics/expr.py": "X = 1\n",
    "ffast/renderers/web/static/app.js": "let a = 1;\n",
    "server.py": "def cli(): pass\n",
}


def test_same_code_gives_the_same_fingerprint(tmp_path):
    a = _tree(tmp_path / "a", BASE)
    b = _tree(tmp_path / "b", BASE)
    assert fingerprint_of(a) == fingerprint_of(b)
    assert len(fingerprint_of(a)) == 16


def test_a_changed_python_file_changes_it(tmp_path):
    a = _tree(tmp_path / "a", BASE)
    b = _tree(tmp_path / "b", {**BASE, "ffast/metrics/expr.py": "X = 2\n"})
    assert fingerprint_of(a) != fingerprint_of(b)


def test_a_changed_web_file_or_server_py_changes_it(tmp_path):
    a = _tree(tmp_path / "a", BASE)
    b = _tree(tmp_path / "b", {**BASE, "ffast/renderers/web/static/app.js": "let a = 2;\n"})
    c = _tree(tmp_path / "c", {**BASE, "server.py": "def cli(): return 1\n"})
    assert len({fingerprint_of(a), fingerprint_of(b), fingerprint_of(c)}) == 3


def test_a_renamed_file_changes_it(tmp_path):
    a = _tree(tmp_path / "a", BASE)
    moved = {k.replace("expr.py", "expr2.py"): v for k, v in BASE.items()}
    b = _tree(tmp_path / "b", moved)
    assert fingerprint_of(a) != fingerprint_of(b)


def test_caches_and_dotfiles_do_not_count(tmp_path):
    a = _tree(tmp_path / "a", BASE)
    b = _tree(tmp_path / "b", {
        **BASE,
        "ffast/metrics/__pycache__/expr.cpython-311.json": "{}",
        "ffast/renderers/web/static/.DS_Store.json": "{}",
        "ffast/notes.txt": "scratch",
    })
    assert fingerprint_of(a) == fingerprint_of(b)


def test_this_install_has_a_fingerprint():
    assert len(code_fingerprint()) == 16
    assert code_fingerprint() == code_fingerprint()


def test_the_server_sends_its_fingerprint_in_hello_ack():
    caps = negotiate(ClientCapabilities(protocol_version="1.0", renderer="vispy"))
    assert caps.code_fingerprint == code_fingerprint()
    assert caps.model_dump()["code_fingerprint"] == code_fingerprint()


def test_no_notice_when_the_code_matches():
    assert server_code_notice("abc", "abc") is None


def test_a_server_without_a_fingerprint_is_older():
    notice = server_code_notice(None, "abc")
    assert "older FFAST code" in notice
    assert "submit a new job" in notice


def test_a_different_fingerprint_is_different_code():
    assert "different FFAST code" in server_code_notice("abd", "abc")


# --- desktop client ---------------------------------------------------------

class _Session:
    def __init__(self, fingerprint):
        self.server_code_fingerprint = fingerprint


def test_desktop_sidebar_says_connected_when_the_code_matches():
    from ffast.core.connection_manager import _connected_progress

    fields = _connected_progress(_Session(code_fingerprint()), "Connected — ✕ to disconnect")
    assert fields == {"message": "Connected — ✕ to disconnect"}


def test_desktop_sidebar_warns_in_red_when_the_server_is_older():
    from ffast.core.connection_manager import _connected_progress

    fields = _connected_progress(_Session(None), "Connected — ✕ to disconnect")
    assert fields["error"] is True
    assert fields["message"].startswith("Connected — ✕ to disconnect. ")
    assert "older FFAST code" in fields["message"]


async def test_hello_returns_the_server_fingerprint():
    from cluster.connection import _do_hello
    from ffast.protocol import control
    from ffast.protocol.rpc import pack

    class _WS:
        async def send(self, data):
            pass

        async def recv(self):
            caps = negotiate(ClientCapabilities(protocol_version="1.0", renderer="vispy"))
            return pack(control.HELLO_ACK, [], {**caps.model_dump(), "role": "CONTROLLING"})

    ack = await _do_hello(_WS(), "token")
    assert ack["code_fingerprint"] == code_fingerprint()

