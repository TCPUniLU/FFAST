"""Cluster server auto-bootstrap — Stage 1 (ADR 0028).

Build the ``ffast`` wheel locally and decide whether a cluster node needs
(re)provisioning. The decision logic is pure and cluster-free so it is
unit-testable; only the wheel build shells out. Later stages (push the wheel
over SSH, provision the venv via ``module load`` + ``pip``, wire into
``connect_to_cluster``) build on these primitives.
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import sys
import tomllib
from pathlib import Path

logger = logging.getLogger("FFAST")

# Heavy scientific deps expected from the cluster's `module load` — compiled and
# CUDA-/BLAS-coupled, so they must match the node rather than be pip-installed.
# Every other entry in pyproject `dependencies` is light and pure-Python and is
# pip-installed on top of the module-provided base (ADR 0028).
HEAVY_DEPS = frozenset({"torch", "numpy", "scipy", "scikit-learn", "ase"})

# On-node marker holding the sha256 of the currently installed wheel.
NODE_MARKER = "~/.ffast/installed.sha256"

_REQ_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def _dist_name(requirement: str) -> str:
    """Distribution name from a PEP 508 requirement (``torch>=2.8`` -> ``torch``)."""
    m = _REQ_NAME.match(requirement.strip())
    return (m.group(0) if m else requirement.strip()).lower()


def read_dependencies(pyproject_path: Path) -> list[str]:
    """Return ``project.dependencies`` from a ``pyproject.toml``."""
    data = tomllib.loads(Path(pyproject_path).read_text())
    return list(data["project"]["dependencies"])


def light_dependencies(dependencies: list[str]) -> list[str]:
    """The pure-Python deps to ``pip install`` alongside ``ffast --no-deps``.

    ``dependencies`` is pyproject's ``project.dependencies``; the heavy
    scientific deps (:data:`HEAVY_DEPS`) are dropped because the cluster provides
    them via ``module load``.
    """
    return [d for d in dependencies if _dist_name(d) not in HEAVY_DEPS]


def wheel_sha256(wheel_path: Path) -> str:
    """Hex sha256 of a built wheel — the provisioning identity marker (ADR 0028)."""
    h = hashlib.sha256()
    with open(wheel_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def needs_provision(local_sha: str, node_marker: str | None) -> bool:
    """True iff the node must be (re)provisioned.

    ``node_marker`` is the contents of the on-node marker file (or ``None`` when
    absent). Re-provision when it is missing or its hash differs from the locally
    built wheel — so a code change without a version bump still reinstalls, while
    an unchanged wheel makes reconnects a no-op.
    """
    return node_marker is None or node_marker.strip() != local_sha


def build_server_wheel(project_root: Path, out_dir: Path) -> Path:
    """Build the ``ffast`` wheel (no deps) into ``out_dir``; return its path.

    Tries the available build frontend in order — ``uv`` (the project's own tool,
    and the common case since its venvs ship no pip), then PyPA ``build``, then
    ``pip wheel`` — so it works whether the desktop env is uv- or pip-managed.
    Integration-level, not unit-tested.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    root = str(project_root)
    candidates = [
        ["uv", "build", "--wheel", "--out-dir", str(out_dir), root],
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir), root],
        [sys.executable, "-m", "pip", "wheel", "--no-deps",
         "--wheel-dir", str(out_dir), root],
    ]
    logger.info("Building ffast wheel from %s", project_root)
    last_err: Exception | None = None
    for cmd in candidates:
        try:
            subprocess.run(cmd, check=True)
            break
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            last_err = exc
    else:
        raise RuntimeError(
            f"no working wheel builder (tried uv/build/pip); last error: {last_err}"
        )
    wheels = sorted(out_dir.glob("ffast-*.whl"))
    if not wheels:
        raise RuntimeError(f"wheel build produced no ffast-*.whl in {out_dir}")
    return wheels[-1]
