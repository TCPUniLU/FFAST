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

from cluster.backend import ClusterError

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


# ── Stage 2: the SLURM-job command (pure) ────────────────────────────────────
#
# The venv build + install run *inside the SLURM job* (on the allocated compute
# node), not on the login node — login nodes commonly cap CPU/memory or forbid
# `pip`, and the work belongs under the allocation. The login node only stages
# the wheel (a file copy). So both commands below are the *job* command; connect
# appends `--port`/`--token-hash`, which attach to the trailing `ffast-server`.

# Fail loudly in the job log if the activated ffast-server is stale/incompatible
# (doesn't accept the flags the client passes) — instead of launching it and
# erroring on `--token-hash` after the fact, then hanging the client on retries.
_VERIFY_SERVER = (
    "( ffast-server --help 2>&1 | grep -qe '--token-hash' "
    "|| { echo 'ffast-bootstrap ERROR: installed ffast-server lacks --token-hash "
    "(stale/incompatible install)'; exit 1; } )"
)


def server_launch_cmd(venv_path: str, modules: list[str]) -> str:
    """Job command for an already-provisioned node: modules → activate → run.

    Used when the on-node marker already matches the local wheel, so no install
    is needed — the SLURM job just activates the existing venv (ADR 0028).
    """
    parts = [f"module load {m}" for m in modules]
    parts.append(f"source {venv_path}/bin/activate")
    parts.append(_VERIFY_SERVER)
    parts.append("ffast-server")
    return " && ".join(parts)


def provision_launch_cmd(
    venv_path: str,
    remote_wheel: str,
    light_deps: list[str],
    modules: list[str],
    marker_path: str,
    wheel_sha: str,
) -> str:
    """Job command that provisions the venv *then* launches the server (ADR 0028).

    Run as the SLURM job on the allocated node: load modules, create a
    ``--system-site-packages`` venv (once), install the staged ``ffast`` wheel
    (``--no-deps``) plus the light pure-Python deps, write the sha256 marker, and
    exec ``ffast-server``. ``&&``-chained so any failure aborts before launch;
    the marker is written last so a failed install never marks the node current.
    Ends with ``ffast-server`` so connect's ``--port`` etc. attach to it.
    """
    parts = [f"module load {m}" for m in modules]
    parts.append(
        f"([ -d {venv_path} ] || python -m venv --system-site-packages {venv_path})"
    )
    parts.append(f"source {venv_path}/bin/activate")
    parts.append(f"python -m pip install --no-deps --force-reinstall {remote_wheel}")
    if light_deps:
        parts.append("python -m pip install " + " ".join(light_deps))
    # Verify the just-installed server is compatible BEFORE writing the marker,
    # so a bad install is never recorded as current.
    parts.append(_VERIFY_SERVER)
    parts.append(f"printf '%s' {wheel_sha} > {marker_path}")
    parts.append("ffast-server")
    return " && ".join(parts)


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
    """Run a command on the login node; return (stdout, stderr, returncode).

    Logs the remote command and, on a non-zero exit, the exit code + stderr —
    so any failure is actionable in ``debug.log`` and the terminal.
    """
    logger.info("bootstrap ssh %s: %s", profile.host, " ".join(args))
    proc = await asyncio.create_subprocess_exec(
        *_ssh_base(profile), *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(stdin.encode() if stdin is not None else None)
    out_s, err_s = out.decode(errors="replace"), err.decode(errors="replace")
    if proc.returncode != 0:
        logger.error(
            "bootstrap ssh FAILED (exit %s) on %s: %s\nstderr:\n%s",
            proc.returncode, profile.host, " ".join(args), err_s.strip(),
        )
    return out_s, err_s, proc.returncode


async def _scp_push(profile, local: Path, remote: str) -> None:
    """Copy a local file to ``remote`` on the login node via scp (binary-safe)."""
    cmd = ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=15"]
    if getattr(profile, "identity_file", ""):
        cmd += ["-i", os.path.expanduser(profile.identity_file)]
    target = f"{profile.username}@{profile.host}" if profile.username else profile.host
    cmd += [str(local), f"{target}:{remote}"]
    logger.info("bootstrap scp %s -> %s:%s", local.name, profile.host, remote)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        err_s = err.decode(errors="replace").strip()
        logger.error(
            "bootstrap scp FAILED (exit %s): %s -> %s:%s\nstderr:\n%s",
            proc.returncode, local, profile.host, remote, err_s,
        )
        raise ClusterError(
            f"Failed to copy the server wheel to {profile.host} "
            f"(scp exit {proc.returncode}). Check SSH key auth and disk quota.",
            err_s,
        )


async def tail_job_log(profile, job_id: str, lines: int = 40) -> str:
    """Best-effort tail of the SLURM job's output log (``''`` if unavailable).

    Since provisioning now runs *inside the job*, a failure there (bad module,
    pip error on the compute node) shows up in the job log, not as a client-side
    exception. Fetching it makes those failures actionable locally (ADR 0028).
    """
    for path in (f"~/slurm-{job_id}.out", f"~/slurm-{job_id}.err"):
        try:
            out, _, rc = await _ssh_run(profile, "tail", "-n", str(lines), path)
        except Exception:
            continue
        if rc == 0 and out.strip():
            return f"{path} (last {lines} lines):\n{out}"
    return ""


async def provision_node(
    profile,
    project_root: Optional[Path] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """Stage the ffast wheel on the login node; return the SLURM job command.

    The login node only does light, policy-safe work — read the marker, and on a
    hash mismatch ``mkdir`` + ``scp`` the freshly built wheel. The heavy work
    (venv creation + ``pip install``) is deferred into the returned *job* command
    so it runs under the SLURM allocation, not on the login node (ADR 0028).
    Integration-level (needs a reachable login node); not unit-tested.
    """
    def _progress(msg: str) -> None:
        logger.info(msg)
        if progress_cb is not None:
            progress_cb(msg)

    root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    venv = getattr(profile, "venv_path", "") or DEFAULT_VENV
    modules = list(getattr(profile, "modules", []) or [])
    logger.info(
        "auto-bootstrap start: host=%s venv=%s modules=%s root=%s",
        profile.host, venv, modules, root,
    )

    with tempfile.TemporaryDirectory(prefix="ffast-wheel-") as tmp:
        _progress("Building ffast wheel…")
        try:
            wheel = build_server_wheel(root, Path(tmp))
        except Exception as exc:  # build tooling / not a repo checkout
            logger.exception("auto-bootstrap: wheel build failed")
            raise ClusterError(
                f"Could not build the ffast wheel from {root}: {exc}. "
                f"Run from a repo checkout with uv/build/pip available.",
                str(exc),
            )
        sha = wheel_sha256(wheel)
        logger.info("auto-bootstrap: built %s (sha %s)", wheel.name, sha[:12])

        # Marker lives INSIDE the venv, not a global path — so switching
        # venv_path (or replacing the venv) always re-provisions correctly.
        marker_path = f"{venv}/.ffast-wheel.sha256"
        marker_out, _, marker_rc = await _ssh_run(profile, "cat", marker_path)
        node_marker = marker_out if marker_rc == 0 else None

        if not needs_provision(sha, node_marker):
            _progress(
                f"Server up to date on {profile.host} (sha {sha[:12]}); "
                f"job will launch the existing venv"
            )
            return server_launch_cmd(venv, modules)

        _progress(f"Staging ffast wheel to {profile.host}…")
        remote_wheel = f"{NODE_DIR}/{wheel.name}"
        _, mk_err, mk_rc = await _ssh_run(profile, "mkdir", "-p", NODE_DIR)
        if mk_rc != 0:
            raise ClusterError(
                f"Could not create {NODE_DIR} on {profile.host} — check SSH key "
                f"auth (BatchMode) and that the host/username are correct.",
                mk_err,
            )
        await _scp_push(profile, wheel, remote_wheel)
        _progress("Wheel staged; venv build + install will run inside the SLURM job")

    light = light_dependencies(read_dependencies(root / "pyproject.toml"))
    cmd = provision_launch_cmd(venv, remote_wheel, light, modules, marker_path, sha)
    logger.info("auto-bootstrap: job will provision-and-launch: %s", cmd)
    return cmd
