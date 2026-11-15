"""``ffast install-fast-reader``: install the fast reader (``ffast-xyz``,
ADR 0100) into the Python that runs FFAST. It is not on PyPI.

1. A ready-made wheel from the GitHub Release ``ffast-xyz-v<RELEASE>``: the
   one that fits this computer, checked against the release's SHA256SUMS,
   installed as that file (never by name, so no other ``ffast-xyz`` can come
   in its place).
2. Else (no internet, no release, no wheel for this computer) built from
   this checkout's source, which needs Rust: ``cargo`` is looked for on PATH,
   then in ``$CARGO_HOME/bin``, then in ``~/.cargo/bin`` (where rustup puts
   it, often not on PATH), and that folder goes first on PATH for the build.

``--wheel FILE`` installs a wheel copied by hand (a cluster node without
internet); ``--from-source`` skips the download. Whatever fails, FFAST still
works, with the slower Python parser, and the message says what is needed.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

RELEASE = "0.1.0"          # the ffast-xyz this FFAST installs; = rust/ffast-xyz/Cargo.toml
REPO = "https://github.com/TCPUniLU/FFAST"
RUSTUP = "https://rustup.rs"
WORKS_WITHOUT = "FFAST works without it: .xyz files load with the slower Python parser."
NEEDS_RUST = (
    "Rust is required to build the fast reader (ffast-xyz), and it was not found "
    "(looked on PATH, in $CARGO_HOME/bin and in ~/.cargo/bin).\n"
    f"Install Rust from {RUSTUP} (one command), then run `ffast install-fast-reader` again.\n"
    + WORKS_WITHOUT)
TAIL_LINES = 25
TIMEOUT_S = 30


def release_url(name: str) -> str:
    return f"{REPO}/releases/download/ffast-xyz-v{RELEASE}/{name}"


def source_dir() -> Path:
    """``rust/ffast-xyz`` of the checkout this FFAST runs from."""
    import ffast
    return Path(ffast.__file__).resolve().parents[1] / "rust" / "ffast-xyz"


# ── the ready-made wheel ────────────────────────────────────────────────────

def fetch(url: str) -> bytes:
    """The bytes at ``url``; OSError (URLError included) when it cannot."""
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as reply:
        return reply.read()


def parse_sums(text: str) -> dict[str, str]:
    """``sha256sum`` output: {file name: hex digest}."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def python_tags(python=sys.executable) -> list[str]:
    """The wheel tags ``python`` accepts, best first."""
    if Path(python).resolve() == Path(sys.executable).resolve():
        from packaging.tags import sys_tags
        return [str(t) for t in sys_tags()]
    code = "from packaging.tags import sys_tags; print('\\n'.join(map(str, sys_tags())))"
    got = subprocess.run([python, "-c", code], capture_output=True, text=True)
    if got.returncode != 0:      # no packaging there: ask our own (right when it is the same kind)
        from packaging.tags import sys_tags
        return [str(t) for t in sys_tags()]
    return got.stdout.split()


def pick_wheel(names, tags) -> str | None:
    """The wheel among ``names`` whose tags come first in ``tags``."""
    from packaging.utils import InvalidWheelFilename, parse_wheel_filename
    rank = {tag: i for i, tag in enumerate(tags)}
    best, best_rank = None, len(rank)
    for name in names:
        try:
            _, _, _, wheel_tags = parse_wheel_filename(name)
        except InvalidWheelFilename:
            continue
        r = min((rank.get(str(t), len(rank)) for t in wheel_tags), default=len(rank))
        if r < best_rank:
            best, best_rank = name, r
    return best


def download_wheel(folder: Path, python=sys.executable, out=print) -> Path | None:
    """The release's wheel for this computer, checked, in ``folder``; None
    (with the reason said) when there is none to have."""
    try:
        import packaging.tags  # noqa: F401  (comes with ase, through matplotlib)
    except ImportError:
        out("Cannot tell which ready-made fast reader fits this computer (no `packaging`).")
        return None
    try:
        sums = parse_sums(fetch(release_url("SHA256SUMS")).decode())
    except (OSError, UnicodeDecodeError) as exc:
        out(f"Could not download the ready-made fast reader {RELEASE} ({exc}).")
        return None
    name = pick_wheel(sums, python_tags(python))
    if name is None:
        out(f"The release has no ready-made fast reader for this computer ({python_tags(python)[0]}).")
        return None
    out(f"Downloading {name}…")
    try:
        data = fetch(release_url(name))
    except OSError as exc:
        out(f"Could not download {name} ({exc}).")
        return None
    if hashlib.sha256(data).hexdigest() != sums[name]:
        out(f"{name} does not match its checksum in SHA256SUMS: not installed.")
        return None
    path = folder / name
    path.write_bytes(data)
    return path


# ── building from source ────────────────────────────────────────────────────

def find_cargo(env=None, home=None) -> Path | None:
    """Rust's ``cargo`` that runs: on PATH, in $CARGO_HOME/bin, in ~/.cargo/bin."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    exe = "cargo.exe" if os.name == "nt" else "cargo"
    places = [shutil.which("cargo", path=env.get("PATH"))]
    if env.get("CARGO_HOME"):
        places.append(str(Path(env["CARGO_HOME"]) / "bin" / exe))
    places.append(str(home / ".cargo" / "bin" / exe))
    for place in places:
        if place and Path(place).is_file() and _runs([place, "--version"], env):
            return Path(place)
    return None


def _runs(cmd, env) -> bool:
    try:
        return subprocess.run(cmd, env=dict(env), capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ── installing ──────────────────────────────────────────────────────────────

def install_command(python=sys.executable, what=None) -> list[str]:
    """Install ``what`` (a wheel file or a source folder); uv when it is there
    (uv's environments have no pip), else pip."""
    what = str(what or source_dir())
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", python, "--no-deps", "--reinstall-package", "ffast-xyz", what]
    return [python, "-m", "pip", "install", "--force-reinstall", "--no-deps", what]


def check_installed(python=sys.executable, env=None) -> str | None:
    """None when ``ffast_xyz`` imports in ``python`` with the contract FFAST
    expects; else why not."""
    from ffast.readers.text import FAST_CONTRACT
    got = subprocess.run([python, "-c", "import ffast_xyz; print(ffast_xyz.CONTRACT)"],
                         env=dict(env or os.environ), capture_output=True, text=True)
    if got.returncode != 0:
        return (got.stderr.strip().splitlines() or ["it does not import"])[-1]
    if got.stdout.strip() != str(FAST_CONTRACT):
        return f"it follows contract {got.stdout.strip()}, this FFAST expects {FAST_CONTRACT}"
    return None


def _run_install(cmd, env, out) -> bool:
    try:
        done = subprocess.run(cmd, env=env, capture_output=True, text=True)
    except OSError as exc:
        out(f"Could not run {cmd[0]}: {exc}")
        return False
    if done.returncode != 0:
        out("\n".join((done.stdout + done.stderr).strip().splitlines()[-TAIL_LINES:]))
        return False
    return True


def _finish(python, env, out) -> int:
    problem = check_installed(python, env)
    if problem:
        out(f"The fast reader was installed but cannot be used: {problem}.")
        return 1
    out("The fast reader is installed. Restart FFAST to use it.")
    return 0


def install_wheel(wheel, python=sys.executable, out=print) -> int:
    if not _run_install(install_command(python, wheel), dict(os.environ), out):
        out(f"Installing {Path(wheel).name} failed (above: the end of its output). " + WORKS_WITHOUT)
        return 1
    return _finish(python, None, out)


def build(source=None, python=sys.executable, out=print) -> int:
    """Build from source with Rust; 0 on success."""
    source = Path(source) if source else source_dir()
    if not (source / "Cargo.toml").is_file():
        out(f"The fast reader's source is not at {source}: this FFAST was not installed from a "
            f"checkout. Clone {REPO} and run "
            "`ffast install-fast-reader --source <checkout>/rust/ffast-xyz`.")
        return 1
    cargo = find_cargo()
    if cargo is None:
        out(NEEDS_RUST)
        return 1
    env = dict(os.environ)
    env["PATH"] = str(cargo.parent) + os.pathsep + env.get("PATH", "")
    out(f"Rust found: {cargo}. Building the fast reader from {source} (about a minute)…")
    if not _run_install(install_command(python, source), env, out):
        out(f"\nThe fast reader did not build (above: the end of the build output). It needs "
            f"Rust; if Rust is old, update it with `rustup update` (or reinstall from {RUSTUP}). "
            + WORKS_WITHOUT)
        return 1
    return _finish(python, env, out)


def install(source=None, python=sys.executable, out=print, wheel=None, from_source=False) -> int:
    """The ready-made wheel, else a build from source; 0 on success."""
    if wheel:
        return install_wheel(wheel, python, out)
    if not from_source:
        with tempfile.TemporaryDirectory() as folder:
            got = download_wheel(Path(folder), python, out)
            if got is not None:
                return install_wheel(got, python, out)
        out("Building it from source instead.")
    code = build(source, python, out)
    if code and not from_source:
        out(f"On a computer without internet, download the wheel for it from "
            f"{REPO}/releases/tag/ffast-xyz-v{RELEASE} elsewhere, copy it over and run "
            "`ffast install-fast-reader --wheel <file>`.")
    return code


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="ffast install-fast-reader",
        description="Install the fast .xyz reader (ffast-xyz): a ready-made wheel from GitHub, "
                    "else built from source with Rust.")
    parser.add_argument("--wheel", help="install this wheel file (copied by hand)")
    parser.add_argument("--from-source", action="store_true", help="build from source; needs Rust")
    parser.add_argument("--source", help="the ffast-xyz source folder (default: this checkout's)")
    args = parser.parse_args(argv)
    return install(args.source, wheel=args.wheel, from_source=args.from_source)
