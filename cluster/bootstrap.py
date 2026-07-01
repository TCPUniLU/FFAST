"""Cluster server auto-bootstrap — Stage 1 (ADR 0028).

Build the ``ffast`` wheel locally and decide whether a cluster node needs
(re)provisioning. The decision logic is pure and cluster-free so it is
unit-testable; only the wheel build shells out. Later stages (push the wheel
over SSH, provision the venv via ``module load`` + ``pip``, wire into
``connect_to_cluster``) build on these primitives.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("FFAST")

# Heavy scientific deps expected from the cluster's `module load` — compiled and
# CUDA-/BLAS-coupled, so they must match the node rather than be pip-installed.
# Every other entry in pyproject `dependencies` is light and pure-Python and is
# pip-installed on top of the module-provided base (ADR 0028).
HEAVY_DEPS = frozenset({"torch", "numpy", "scipy", "scikit-learn", "ase"})

# On-node layout (ADR 0028): a stable ~/.ffast holds the venv, pushed wheel, and
# the sha256 marker of the currently installed wheel.
NODE_DIR = "~/.ffast"
NODE_MARKER = "~/.ffast/installed.sha256"
DEFAULT_VENV = "~/.ffast/venv"

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


# ── Stage 2: node-side scripts (pure) ────────────────────────────────────────

def server_launch_cmd(venv_path: str, modules: list[str]) -> str:
    """The shell command the SLURM job runs to start the provisioned server.

    Loads the cluster modules, activates the provisioned venv, then execs
    ``ffast-server``; ``connect_to_cluster`` appends ``--port`` etc. (ADR 0028).
    """
    parts = [f"module load {m}" for m in modules]
    parts.append(f"source {venv_path}/bin/activate")
    parts.append("ffast-server")
    return " && ".join(parts)


def provision_script(
    venv_path: str,
    remote_wheel: str,
    light_deps: list[str],
    modules: list[str],
    marker_path: str,
    wheel_sha: str,
) -> str:
    """Bash run on the login node to (re)provision the server venv (ADR 0028).

    Loads the cluster modules for the heavy scientific stack, creates a
    ``--system-site-packages`` venv over them (once), installs the pushed
    ``ffast`` wheel with ``--no-deps`` plus the light pure-Python deps, and
    writes the wheel's sha256 to the marker. Idempotent: safe to re-run.
    """
    lines = ["set -e", f"mkdir -p {NODE_DIR}"]
    lines += [f"module load {m}" for m in modules]
    lines.append(
        f"[ -d {venv_path} ] || python -m venv --system-site-packages {venv_path}"
    )
    lines.append(f"source {venv_path}/bin/activate")
    lines.append(f"python -m pip install --no-deps --force-reinstall {remote_wheel}")
    if light_deps:
        lines.append("python -m pip install " + " ".join(light_deps))
    # Marker written last, so a failed install never marks the node current.
    lines.append(f"printf '%s' {wheel_sha} > {marker_path}")
    return "\n".join(lines) + "\n"


# ── Stage 2: SSH transport (integration; mirrors cluster/connection.py) ───────

def _ssh_base(profile) -> list[str]:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=15"]
    if getattr(profile, "identity_file", ""):
        cmd += ["-i", os.path.expanduser(profile.identity_file)]
    host = profile.host
    cmd.append(f"{profile.username}@{host}" if profile.username else host)
    return cmd


async def _ssh_run(profile, *args: str, stdin: Optional[str] = None):
    """Run a command on the login node; return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        *_ssh_base(profile), *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(stdin.encode() if stdin is not None else None)
    return out.decode(errors="replace"), err.decode(errors="replace"), proc.returncode


async def _scp_push(profile, local: Path, remote: str) -> None:
    """Copy a local file to ``remote`` on the login node via scp (binary-safe)."""
    cmd = ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=15"]
    if getattr(profile, "identity_file", ""):
        cmd += ["-i", os.path.expanduser(profile.identity_file)]
    target = f"{profile.username}@{profile.host}" if profile.username else profile.host
    cmd += [str(local), f"{target}:{remote}"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"scp push failed (exit {proc.returncode}): {err.decode(errors='replace')}"
        )


async def provision_node(
    profile,
    project_root: Optional[Path] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """Ensure the login node has a current ffast server venv; return its launch cmd.

    Builds the wheel locally, compares its sha256 to the on-node marker, and only
    re-pushes + reinstalls on a mismatch (ADR 0028). Returns the shell command the
    SLURM job should run to start the provisioned server. Integration-level (needs
    a reachable login node); not unit-tested.
    """
    def _progress(msg: str) -> None:
        logger.info(msg)
        if progress_cb is not None:
            progress_cb(msg)

    root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    venv = getattr(profile, "venv_path", "") or DEFAULT_VENV
    modules = list(getattr(profile, "modules", []) or [])

    with tempfile.TemporaryDirectory(prefix="ffast-wheel-") as tmp:
        _progress("Building ffast wheel…")
        wheel = build_server_wheel(root, Path(tmp))
        sha = wheel_sha256(wheel)

        marker_out, _, marker_rc = await _ssh_run(profile, "cat", NODE_MARKER)
        node_marker = marker_out if marker_rc == 0 else None

        if not needs_provision(sha, node_marker):
            _progress(f"Server up to date on {profile.host} (sha {sha[:12]}), skipping provision")
            return server_launch_cmd(venv, modules)

        _progress(f"Provisioning server on {profile.host}…")
        remote_wheel = f"{NODE_DIR}/{wheel.name}"
        _, mk_err, mk_rc = await _ssh_run(profile, "mkdir", "-p", NODE_DIR)
        if mk_rc != 0:
            raise RuntimeError(f"could not create {NODE_DIR} on {profile.host}: {mk_err}")
        await _scp_push(profile, wheel, remote_wheel)

        light = light_dependencies(read_dependencies(root / "pyproject.toml"))
        script = provision_script(venv, remote_wheel, light, modules, NODE_MARKER, sha)
        _, err, rc = await _ssh_run(profile, "bash", "-s", stdin=script)
        if rc != 0:
            raise RuntimeError(f"provisioning failed on {profile.host} (exit {rc}):\n{err}")
        _progress(f"Server provisioned on {profile.host} (sha {sha[:12]})")

    return server_launch_cmd(venv, modules)
