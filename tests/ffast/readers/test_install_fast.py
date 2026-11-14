"""``ffast install-fast-reader`` (ADR 0100): a ready-made wheel from the
GitHub Release, checked against its SHA256SUMS; else a build from source,
which looks for Rust and says Rust is required when it is missing or the
build fails. No network and no real build here: ``fetch`` and
``subprocess.run`` are replaced."""
import hashlib
import os
import re
import subprocess
import types
from pathlib import Path

import pytest

from ffast.readers import install_fast as inst
from ffast.readers.text import FAST_CONTRACT

REPO_ROOT = Path(__file__).resolve().parents[3]
WHEELS = {
    "mac-arm": "ffast_xyz-0.1.0-cp311-abi3-macosx_11_0_arm64.whl",
    "mac-x86": "ffast_xyz-0.1.0-cp311-abi3-macosx_10_12_x86_64.whl",
    "linux-x86": "ffast_xyz-0.1.0-cp311-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    "linux-arm": "ffast_xyz-0.1.0-cp311-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
    "windows": "ffast_xyz-0.1.0-cp311-abi3-win_amd64.whl",
}


def _cargo(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "cargo"
    exe.write_text("#!/bin/sh\necho cargo 1.80\n")
    exe.chmod(0o755)
    return exe


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No network unless a test serves files."""
    def fetch(url):
        raise OSError("no network in tests")
    monkeypatch.setattr(inst, "fetch", fetch)


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


def _fake_run(cargo=None, build_ok=True, contract=FAST_CONTRACT, log="built"):
    """``subprocess.run``: the real one for the fake cargo; else records the call."""
    real_run, calls = subprocess.run, []

    def run(cmd, env=None, **kw):
        if cargo is not None and cmd[0] == str(cargo):
            return real_run(cmd, env=env, **kw)
        calls.append((cmd, env))
        if "-c" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=f"{contract}\n", stderr="")
        return types.SimpleNamespace(returncode=0 if build_ok else 1, stdout=log, stderr="")
    return run, calls


def _serve(monkeypatch, files):
    """The release holds ``files`` ({name: bytes}); every fetch is recorded."""
    asked = []

    def fetch(url):
        asked.append(url)
        name = url.rsplit("/", 1)[1]
        if name not in files:
            raise OSError(f"404 {name}")
        return files[name]
    monkeypatch.setattr(inst, "fetch", fetch)
    return asked


def _sums(wheels):
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in wheels.items()).encode()


# ── which wheel ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tags, want", [
    (["cp312-abi3-macosx_14_0_arm64", "cp311-abi3-macosx_11_0_arm64", "py3-none-any"], "mac-arm"),
    (["cp311-abi3-manylinux_2_28_x86_64", "cp311-abi3-manylinux_2_17_x86_64"], "linux-x86"),
    (["cp313-abi3-win_amd64", "cp311-abi3-win_amd64"], "windows"),
    (["cp311-abi3-musllinux_1_2_x86_64", "py3-none-any"], None),
])
def test_the_wheel_that_fits_the_computer_is_picked(tags, want):
    assert inst.pick_wheel(WHEELS.values(), tags) == (WHEELS[want] if want else None)


def test_this_pythons_tags_pick_one_of_the_release_wheels_or_none():
    tags = inst.python_tags()
    assert tags and tags[0].startswith("cp3")
    assert inst.pick_wheel(WHEELS.values(), tags) in (*WHEELS.values(), None)


def test_sha256sums_are_read_as_sha256sum_writes_them():
    assert inst.parse_sums("AB12  a.whl\nCD34 *b.whl\n\nbroken\n") == {"a.whl": "ab12", "b.whl": "cd34"}


# ── the ready-made wheel ────────────────────────────────────────────────────

def test_the_release_wheel_is_checked_and_installed_as_that_file(monkeypatch):
    wheels = {name: name.encode() * 3 for name in WHEELS.values()}
    asked = _serve(monkeypatch, {**wheels, "SHA256SUMS": _sums(wheels)})
    monkeypatch.setattr(inst, "python_tags", lambda python: ["cp311-abi3-win_amd64"])
    run, calls = _fake_run()
    monkeypatch.setattr(inst.subprocess, "run", run)
    said = []
    assert inst.install(python="/py", out=said.append) == 0
    assert asked == [inst.release_url("SHA256SUMS"), inst.release_url(WHEELS["windows"])]
    assert f"/releases/download/ffast-xyz-v{inst.RELEASE}/" in asked[0]
    (cmd, _), (check, _) = calls
    assert cmd[-1].endswith(WHEELS["windows"]) and Path(cmd[-1]).is_absolute()   # the file, not a name
    assert check[0] == "/py" and said[-1].startswith("The fast reader is installed")


def test_a_wheel_that_does_not_match_its_checksum_is_not_installed(monkeypatch, source, no_path_cargo):
    name = WHEELS["windows"]
    _serve(monkeypatch, {name: b"tampered", "SHA256SUMS": _sums({name: b"original"})})
    monkeypatch.setattr(inst, "python_tags", lambda python: ["cp311-abi3-win_amd64"])
    run, calls = _fake_run()
    monkeypatch.setattr(inst.subprocess, "run", run)
    said = []
    assert inst.install(source, out=said.append) == 1
    assert calls == []
    text = "\n".join(said)
    assert "does not match its checksum" in text and "Rust is required" in text


def test_no_wheel_for_this_computer_builds_from_source(monkeypatch, source, tmp_path, no_path_cargo):
    wheels = {WHEELS["windows"]: b"w"}
    _serve(monkeypatch, {**wheels, "SHA256SUMS": _sums(wheels)})
    monkeypatch.setattr(inst, "python_tags", lambda python: ["cp311-abi3-musllinux_1_2_x86_64"])
    cargo = _cargo(tmp_path / "home" / ".cargo" / "bin")
    run, calls = _fake_run(cargo)
    monkeypatch.setattr(inst.subprocess, "run", run)
    said = []
    assert inst.install(source, out=said.append) == 0
    assert "no ready-made fast reader for this computer" in said[0]
    assert calls[0][0][-1] == str(source)


def test_offline_without_rust_names_both_ways_to_get_it(source, no_path_cargo, monkeypatch):
    run, calls = _fake_run()
    monkeypatch.setattr(inst.subprocess, "run", run)
    said = []
    assert inst.install(source, out=said.append) == 1
    text = "\n".join(said)
    assert calls == []
    assert "Could not download" in text and "Rust is required" in text and inst.RUSTUP in text
    assert "--wheel <file>" in text and f"ffast-xyz-v{inst.RELEASE}" in text and "slower Python parser" in text


def test_a_wheel_copied_by_hand_is_installed_without_the_network(tmp_path, monkeypatch):
    wheel = tmp_path / WHEELS["linux-x86"]
    wheel.write_bytes(b"w")
    run, calls = _fake_run()
    monkeypatch.setattr(inst.subprocess, "run", run)
    assert inst.main(["--wheel", str(wheel)]) == 0          # the offline fixture would fail any fetch
    assert calls[0][0][-1] == str(wheel)


def test_from_source_skips_the_download(source, tmp_path, no_path_cargo, monkeypatch):
    _serve(monkeypatch, {})
    asked = []
    monkeypatch.setattr(inst, "fetch", lambda url: asked.append(url))
    cargo = _cargo(tmp_path / "home" / ".cargo" / "bin")
    run, _ = _fake_run(cargo)
    monkeypatch.setattr(inst.subprocess, "run", run)
    assert inst.install(source, from_source=True, out=lambda s: None) == 0
    assert asked == []


# ── building from source ────────────────────────────────────────────────────

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


def test_it_builds_with_rusts_folder_first_on_path_and_checks_the_import(source, tmp_path, no_path_cargo,
                                                                        monkeypatch):
    cargo = _cargo(tmp_path / "home" / ".cargo" / "bin")
    run, calls = _fake_run(cargo)
    monkeypatch.setattr(inst.subprocess, "run", run)
    said = []
    assert inst.build(source, python="/py", out=said.append) == 0
    (build, env), (check, _) = calls
    assert build[-1] == str(source) and env["PATH"].split(os.pathsep)[0] == str(cargo.parent)
    assert check[0] == "/py" and "import ffast_xyz" in check[-1]
    assert said[-1].startswith("The fast reader is installed")


def test_a_failed_build_shows_its_end_and_says_rust_is_needed(source, tmp_path, no_path_cargo, monkeypatch):
    cargo = _cargo(tmp_path / "home" / ".cargo" / "bin")
    log = "\n".join(f"line {i}" for i in range(100)) + "\nerror: linker `cc` not found"
    run, _ = _fake_run(cargo, build_ok=False, log=log)
    monkeypatch.setattr(inst.subprocess, "run", run)
    said = []
    assert inst.build(source, out=said.append) == 1
    text = "\n".join(said)
    assert "error: linker `cc` not found" in text and "line 0\n" not in text
    assert "needs Rust" in text and "slower Python parser" in text


def test_a_package_with_another_contract_is_reported(source, tmp_path, no_path_cargo, monkeypatch):
    cargo = _cargo(tmp_path / "home" / ".cargo" / "bin")
    run, _ = _fake_run(cargo, contract=999)
    monkeypatch.setattr(inst.subprocess, "run", run)
    said = []
    assert inst.build(source, out=said.append) == 1
    assert "contract 999" in said[-1]


def test_without_the_source_it_says_where_to_get_it(tmp_path):
    said = []
    assert inst.build(tmp_path / "nowhere", out=said.append) == 1
    assert "not installed from a checkout" in said[0]


def test_uv_reinstalls_only_the_fast_reader_and_pip_is_the_fallback(monkeypatch):
    monkeypatch.setattr(inst.shutil, "which", lambda name, **k: "/bin/uv" if name == "uv" else None)
    cmd = inst.install_command("/py", "/src")
    assert cmd[:3] == ["/bin/uv", "pip", "install"] and "--reinstall-package" in cmd and "--reinstall" not in cmd
    monkeypatch.setattr(inst.shutil, "which", lambda name, **k: None)
    assert inst.install_command("/py", "/src") == ["/py", "-m", "pip", "install", "--force-reinstall",
                                                    "--no-deps", "/src"]


# ── the parts that must agree ──────────────────────────────────────────────

def test_the_release_ffast_installs_is_the_crates_version():
    crate = re.search(r'^version = "(.+)"', (REPO_ROOT / "rust/ffast-xyz/Cargo.toml").read_text(), re.M)
    assert crate.group(1) == inst.RELEASE


def test_the_workflow_releases_wheels_and_checksums_on_the_tag():
    flow = (REPO_ROOT / ".github/workflows/ffast-xyz.yml").read_text()
    assert 'tags: ["ffast-xyz-v*"]' in flow
    assert "sha256sum *.whl > SHA256SUMS" in flow and "gh release create" in flow
    assert "pypi" not in flow.lower().replace("not published to pypi", "")


def test_the_checkout_holds_the_source():
    assert (inst.source_dir() / "Cargo.toml").is_file()


def test_the_ffast_command_runs_it(monkeypatch):
    import launcher
    got = []
    monkeypatch.setattr(inst, "main", lambda argv: got.append(argv) or 0)
    with pytest.raises(SystemExit) as stop:
        launcher.main(["install-fast-reader", "--source", "x"])
    assert stop.value.code == 0 and got == [["--source", "x"]]
