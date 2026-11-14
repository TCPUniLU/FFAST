"""``ffast install-fast-reader`` (ADR 0100): looks for Rust, builds the fast
reader from source with Rust on PATH, and says Rust is required when it is
missing or the build fails. No real build runs here: ``subprocess.run`` is
replaced."""
import os
import subprocess
import types
from pathlib import Path

import pytest

from ffast.readers import install_fast as inst


def _cargo(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "cargo"
    exe.write_text("#!/bin/sh\necho cargo 1.80\n")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "ffast-xyz"
    src.mkdir()
    (src / "Cargo.toml").write_text("[package]\n")
    return src


@pytest.fixture
def no_path_cargo(monkeypatch, tmp_path):
    """No cargo on PATH and a home without Rust, unless a test adds it."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.delenv("CARGO_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


def test_cargo_is_found_on_path_then_cargo_home_then_dot_cargo(tmp_path, no_path_cargo):
    home = tmp_path / "home"
    assert inst.find_cargo() is None
    dot = _cargo(home / ".cargo" / "bin")
    assert inst.find_cargo() == dot
    cargo_home = _cargo(tmp_path / "ch" / "bin")
    assert inst.find_cargo(env={"PATH": "", "CARGO_HOME": str(tmp_path / "ch")}) == cargo_home
    on_path = _cargo(tmp_path / "bin")
    assert inst.find_cargo(env={"PATH": str(tmp_path / "bin"), "CARGO_HOME": str(tmp_path / "ch")}) == on_path


def test_a_cargo_that_does_not_run_is_not_rust(tmp_path, no_path_cargo):
    broken = tmp_path / "home" / ".cargo" / "bin"
    broken.mkdir(parents=True)
    (broken / "cargo").write_text("#!/bin/sh\nexit 1\n")
    (broken / "cargo").chmod(0o755)
    assert inst.find_cargo() is None


def test_without_rust_it_says_rust_is_required_and_builds_nothing(source, no_path_cargo, monkeypatch):
    calls, said = [], []
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: calls.append(a) or None)
    assert inst.install(source, out=said.append) == 1
    assert calls == []
    assert "Rust is required" in said[0] and inst.RUSTUP in said[0] and "slower Python parser" in said[0]


def test_it_builds_with_rusts_folder_first_on_path_and_checks_the_import(source, tmp_path, no_path_cargo,
                                                                        monkeypatch):
    cargo = _cargo(tmp_path / "home" / ".cargo" / "bin")
    real_run, seen, said = subprocess.run, [], []

    def run(cmd, env=None, **kw):
        if cmd[0] == str(cargo):
            return real_run(cmd, env=env, **kw)
        seen.append((cmd, env))
        if "-c" in cmd:
            from ffast.readers.text import FAST_CONTRACT
            return types.SimpleNamespace(returncode=0, stdout=f"{FAST_CONTRACT}\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="built", stderr="")
    monkeypatch.setattr(inst.subprocess, "run", run)
    assert inst.install(source, python="/py", out=said.append) == 0
    (build, env), (check, _) = seen
    assert str(source) in build and env["PATH"].split(os.pathsep)[0] == str(cargo.parent)
    assert check[0] == "/py" and "import ffast_xyz" in check[-1]
    assert said[-1].startswith("The fast reader is installed")


def test_a_failed_build_shows_its_end_and_says_rust_is_needed(source, tmp_path, no_path_cargo, monkeypatch):
    cargo = _cargo(tmp_path / "home" / ".cargo" / "bin")
    real_run, said = subprocess.run, []
    log = "\n".join(f"line {i}" for i in range(100)) + "\nerror: linker `cc` not found"

    def run(cmd, env=None, **kw):
        if cmd[0] == str(cargo):
            return real_run(cmd, env=env, **kw)
        return types.SimpleNamespace(returncode=1, stdout=log, stderr="")
    monkeypatch.setattr(inst.subprocess, "run", run)
    assert inst.install(source, out=said.append) == 1
    assert "error: linker `cc` not found" in said[-1] and "line 0\n" not in said[-1]
    assert "needs Rust" in said[-1] and "slower Python parser" in said[-1]


def test_a_built_package_with_another_contract_is_reported(source, tmp_path, no_path_cargo, monkeypatch):
    cargo = _cargo(tmp_path / "home" / ".cargo" / "bin")
    real_run, said = subprocess.run, []

    def run(cmd, env=None, **kw):
        if cmd[0] == str(cargo):
            return real_run(cmd, env=env, **kw)
        return types.SimpleNamespace(returncode=0, stdout="999\n" if "-c" in cmd else "", stderr="")
    monkeypatch.setattr(inst.subprocess, "run", run)
    assert inst.install(source, out=said.append) == 1
    assert "contract 999" in said[-1]


def test_without_the_source_it_says_where_to_get_it(tmp_path):
    said = []
    assert inst.install(tmp_path / "nowhere", out=said.append) == 1
    assert "not installed from a checkout" in said[0]


def test_uv_reinstalls_only_the_fast_reader_and_pip_is_the_fallback(monkeypatch):
    monkeypatch.setattr(inst.shutil, "which", lambda name, **k: "/bin/uv" if name == "uv" else None)
    cmd = inst.install_command("/py", "/src")
    assert cmd[:3] == ["/bin/uv", "pip", "install"] and "--reinstall-package" in cmd and "--reinstall" not in cmd
    monkeypatch.setattr(inst.shutil, "which", lambda name, **k: None)
    assert inst.install_command("/py", "/src") == ["/py", "-m", "pip", "install", "--force-reinstall",
                                                    "--no-deps", "/src"]


def test_the_checkout_holds_the_source():
    assert (inst.source_dir() / "Cargo.toml").is_file()


def test_the_ffast_command_runs_it(monkeypatch):
    import launcher
    got = []
    monkeypatch.setattr(inst, "main", lambda argv: got.append(argv) or 0)
    with pytest.raises(SystemExit) as stop:
        launcher.main(["install-fast-reader", "--source", "x"])
    assert stop.value.code == 0 and got == [["--source", "x"]]
