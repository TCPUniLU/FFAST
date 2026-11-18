"""One side of a comparison: a checkout of FFAST run with its own code (ADR 0105 §3)."""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NATIVE_REL = Path("rust") / "ffast-xyz" / "python"


class SideError(Exception):
    """A side cannot be timed as asked; the message says what to do."""


@dataclass(frozen=True)
class Side:
    name: str
    root: Path


def parse_side(text):
    name, sep, path = text.partition("=")
    if not sep or not name.strip() or not path.strip():
        raise SideError(f"--side wants NAME=PATH, got {text!r}")
    return Side(name.strip(), Path(path.strip()).expanduser().resolve())


def parse_sides(texts):
    sides = [parse_side(t) for t in texts]
    names = [s.name for s in sides]
    for name in names:
        if names.count(name) > 1:
            raise SideError(f"side name {name!r} is used twice")
    return sides


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _compiled(root):
    folder = Path(root) / NATIVE_REL / "ffast_xyz"
    return any(folder.glob("_ffast_xyz*.so")) or any(folder.glob("_ffast_xyz*.pyd"))


def side_env(side, harness_root, base_env):
    """``(environment, info)`` for running ``side``'s code in a subprocess."""
    harness_root = Path(harness_root)
    head = _git(side.root, "rev-parse", "HEAD")
    commit = head.stdout.strip() if head.returncode == 0 else None
    status = _git(side.root, "status", "--porcelain")
    dirty = status.returncode == 0 and bool(status.stdout.strip())
    if _compiled(side.root):
        native, native_from = side.root / NATIVE_REL, "side"
    else:
        native, native_from = harness_root / NATIVE_REL, "harness"
        build = (f"build the parser in {side.root}: "
                 "cd rust/ffast-xyz && maturin develop --release")
        if commit is None:
            raise SideError(f"side {side.name!r} has no compiled parser and no git commit; {build}")
        diff = _git(harness_root, "diff", "--quiet", commit, "HEAD", "--",
                    "rust/ffast-xyz/src", "rust/ffast-xyz/python")
        if diff.returncode != 0:
            raise SideError(
                f"side {side.name!r} has no compiled parser, and its parser source differs "
                f"from the harness checkout's (or its commit is unknown there); {build}")
    env = dict(base_env)
    paths = [str(side.root), str(native)]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = ":".join(paths)
    return env, {"commit": commit, "dirty": dirty, "native_from": native_from}


def check_side(side, env):
    """Refuse a side whose ``ffast`` is not the one in its own folder.

    The venv's editable install points at the main checkout; if PYTHONPATH did
    not win, both sides would silently time the same code.
    """
    done = subprocess.run(
        [sys.executable, "-c",
         "import ffast, ffast_xyz; print(ffast.__file__); print(ffast_xyz.__file__)"],
        env=env, cwd=side.root, capture_output=True, text=True)
    if done.returncode != 0:
        raise SideError(f"side {side.name!r}: cannot import ffast and ffast_xyz:\n{done.stderr.strip()}")
    ffast_file = Path(done.stdout.splitlines()[0]).resolve()
    if side.root not in ffast_file.parents:
        raise SideError(f"side {side.name!r}: ffast is imported from {ffast_file}, "
                        f"not from {side.root}")
    return done.stdout.splitlines()
