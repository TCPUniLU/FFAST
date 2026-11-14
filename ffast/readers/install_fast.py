"""``ffast install-fast-reader``: build the fast reader (``ffast-xyz``, ADR
0100) from this checkout's source into the Python that runs FFAST.

``ffast-xyz`` is not on PyPI: it is compiled here, so Rust must be installed.
The command looks for Rust's ``cargo`` on PATH, then in ``$CARGO_HOME/bin``,
then in ``~/.cargo/bin`` (where rustup puts it, often not on PATH), and builds
with that folder put first on PATH. Without Rust, or when the build fails, it
says what is needed; FFAST still works, with the slower Python parser.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

RUSTUP = "https://rustup.rs"
NEEDS_RUST = (
    "Rust is required to build the fast reader (ffast-xyz), and it was not found "
    "(looked on PATH, in $CARGO_HOME/bin and in ~/.cargo/bin).\n"
    f"Install Rust from {RUSTUP} (one command), then run `ffast install-fast-reader` again.\n"
    "FFAST works without it: .xyz files load with the slower Python parser.")
TAIL_LINES = 25


def source_dir() -> Path:
    """``rust/ffast-xyz`` of the checkout this FFAST runs from."""
    import ffast
    return Path(ffast.__file__).resolve().parents[1] / "rust" / "ffast-xyz"


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


def install_command(python=sys.executable, source=None) -> list[str]:
    """uv when it is there (uv's environments have no pip), else pip."""
    source = str(source or source_dir())
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", python, "--reinstall-package", "ffast-xyz", source]
    return [python, "-m", "pip", "install", "--force-reinstall", "--no-deps", source]


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


def install(source=None, python=sys.executable, out=print) -> int:
    """Build and install; 0 on success. Messages go to ``out``."""
    source = Path(source) if source else source_dir()
    if not (source / "Cargo.toml").is_file():
        out(f"The fast reader's source is not at {source}: this FFAST was not installed from a "
            "checkout. Clone https://github.com/TCPUniLU/FFAST and run "
            "`ffast install-fast-reader --source <checkout>/rust/ffast-xyz`.")
        return 1
    cargo = find_cargo()
    if cargo is None:
        out(NEEDS_RUST)
        return 1
    env = dict(os.environ)
    env["PATH"] = str(cargo.parent) + os.pathsep + env.get("PATH", "")
    out(f"Rust found: {cargo}. Building the fast reader from {source} (about a minute)…")
    cmd = install_command(python, source)
    try:
        built = subprocess.run(cmd, env=env, capture_output=True, text=True)
    except OSError as exc:
        out(f"Could not run {cmd[0]}: {exc}")
        return 1
    if built.returncode != 0:
        tail = "\n".join((built.stdout + built.stderr).strip().splitlines()[-TAIL_LINES:])
        out(f"{tail}\n\nThe fast reader did not build (above: the end of the build output). It needs "
            f"Rust; if Rust is old, update it with `rustup update` (or reinstall from {RUSTUP}). "
            "FFAST works without it: .xyz files load with the slower Python parser.")
        return 1
    problem = check_installed(python, env)
    if problem:
        out(f"The fast reader was built but cannot be used: {problem}.")
        return 1
    out("The fast reader is installed. Restart FFAST to use it.")
    return 0


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="ffast install-fast-reader",
        description="Build the fast .xyz reader (ffast-xyz) from source; needs Rust.")
    parser.add_argument("--source", help="the ffast-xyz source folder (default: this checkout's)")
    args = parser.parse_args(argv)
    return install(args.source)
