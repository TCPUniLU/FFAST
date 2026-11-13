"""The cluster form's SSH key help (gap 8 of the Qt-removal audit,
2026-11-13): the launcher lists the private keys in ~/.ssh and checks a typed
path, on the machine that will use the key."""
import os

from cluster.ssh_keys import check_key, ssh_keys

PRIVATE = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----\n"


def _ssh_dir(tmp_path):
    d = tmp_path / ".ssh"
    d.mkdir()
    (d / "id_ed25519").write_text(PRIVATE)
    (d / "id_ed25519.pub").write_text("ssh-ed25519 AAAA me@host\n")
    (d / "id_rsa_cluster").write_text(PRIVATE.replace("OPENSSH", "RSA"))
    (d / "known_hosts").write_text("host ssh-ed25519 AAAA\n")
    (d / "config").write_text("Host x\n")
    (d / "sub").mkdir()
    return d


def test_the_list_holds_only_private_keys(tmp_path):
    d = _ssh_dir(tmp_path)
    assert ssh_keys(str(d)) == [str(d / "id_ed25519"), str(d / "id_rsa_cluster")]


def test_the_list_of_a_missing_folder_is_empty(tmp_path):
    assert ssh_keys(str(tmp_path / "none")) == []


def test_the_list_names_home_as_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _ssh_dir(tmp_path)
    assert ssh_keys() == ["~/.ssh/id_ed25519", "~/.ssh/id_rsa_cluster"]


def test_a_typed_path_is_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = _ssh_dir(tmp_path)
    assert check_key("~/.ssh/id_ed25519") == {"ok": True, "note": "Private key found"}
    assert check_key("")["ok"] is True and "ssh-agent" in check_key("")["note"]
    assert check_key("~/.ssh/nope") == {"ok": False, "note": "No such file"}
    assert check_key(str(d / "sub")) == {"ok": False, "note": "That is a folder"}
    pub = check_key("~/.ssh/id_ed25519.pub")
    assert pub["ok"] is False and ".pub" in pub["note"]
    assert check_key("~/.ssh/known_hosts") == {"ok": False, "note": "Not a private key file"}


def test_an_unreadable_key_says_so(tmp_path):
    if os.geteuid() == 0:
        return   # root reads everything
    key = tmp_path / "locked"
    key.write_text(PRIVATE)
    key.chmod(0)
    try:
        assert check_key(str(key)) == {"ok": False, "note": "Cannot read it"}
    finally:
        key.chmod(0o600)
