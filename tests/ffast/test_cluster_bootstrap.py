"""Stage 1 of cluster auto-bootstrap (ADR 0028): the pure decision helpers."""

import hashlib
from pathlib import Path

import pytest

from cluster.backend import ClusterError
from cluster.config import ClusterProfile
from cluster.bootstrap import (
    provision_node,
    HEAVY_DEPS,
    _dist_name,
    light_dependencies,
    needs_provision,
    provision_launch_cmd,
    read_dependencies,
    server_launch_cmd,
    wheel_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dist_name_strips_version_and_extras():
    assert _dist_name("torch>=2.8.0") == "torch"
    assert _dist_name("scikit-learn>=1.6.1") == "scikit-learn"
    assert _dist_name("pyside6>=6.8,<6.9") == "pyside6"
    assert _dist_name("tomli-w>=1.0") == "tomli-w"
    assert _dist_name("  numpy<2.0.0 ") == "numpy"


def test_light_dependencies_drops_heavy_keeps_light():
    deps = [
        "ase>=3.26.0", "numpy<2.0.0", "scikit-learn>=1.6.1", "scipy>=1.13.1",
        "torch>=2.8.0", "msgpack>=1.0.0", "websockets>=12.0", "pydantic>=2.0",
        "tomli-w>=1.0", "jaxtyping>=0.2.34",
    ]
    light = light_dependencies(deps)
    assert [_dist_name(d) for d in light] == [
        "msgpack", "websockets", "pydantic", "tomli-w", "jaxtyping",
    ]
    # None of the heavy, module-provided deps survive.
    assert not (HEAVY_DEPS & {_dist_name(d) for d in light})


def test_light_dependencies_matches_real_pyproject():
    deps = read_dependencies(REPO_ROOT / "pyproject.toml")
    light = {_dist_name(d) for d in light_dependencies(deps)}
    assert "torch" not in light and "numpy" not in light
    assert {"msgpack", "websockets", "pydantic"} <= light


def test_wheel_sha256_matches_hashlib(tmp_path):
    p = tmp_path / "ffast-2.0.0-py3-none-any.whl"
    payload = b"not-a-real-wheel" * 100_000  # exceeds the 1 MiB read chunk
    p.write_bytes(payload)
    assert wheel_sha256(p) == hashlib.sha256(payload).hexdigest()


def test_needs_provision():
    assert needs_provision("abc", None) is True          # never installed
    assert needs_provision("abc", "abc") is False         # up to date
    assert needs_provision("abc", "abc\n") is False        # trailing newline
    assert needs_provision("abc", "def") is True           # code changed


def test_server_launch_cmd():
    s = server_launch_cmd("~/.ffast/venv", ["Python/3.11", "PyTorch"])
    assert s.startswith(
        "module load Python/3.11 && module load PyTorch"
        " && source ~/.ffast/venv/bin/activate && "
    )
    # Compatibility gate runs before the real launch.
    assert "grep -qe '--token-hash'" in s
    assert s.endswith("&& ffast-server")
    # No modules -> just activate + verify + run.
    assert server_launch_cmd("~/.ffast/venv", []).startswith(
        "source ~/.ffast/venv/bin/activate && "
    )


def test_provision_launch_cmd():
    s = provision_launch_cmd(
        venv_path="~/.ffast/venv",
        remote_wheel="~/.ffast/ffast-2.0.0-py3-none-any.whl",
        light_deps=["msgpack", "websockets"],
        modules=["Python/3.11"],
        marker_path="~/.ffast/installed.sha256",
        wheel_sha="deadbeef",
    )
    # One &&-chained command run AS the SLURM job (venv+install on the node).
    assert s.startswith("module load Python/3.11 && ")
    assert "([ -d ~/.ffast/venv ] || python -m venv --system-site-packages ~/.ffast/venv)" in s
    assert "source ~/.ffast/venv/bin/activate" in s
    assert "--no-deps --force-reinstall ~/.ffast/ffast-2.0.0-py3-none-any.whl" in s
    assert "python -m pip install msgpack websockets" in s
    # Compatibility gate runs AFTER install but BEFORE the marker, so a bad
    # install is never recorded as current.
    assert "grep -qe '--token-hash'" in s
    assert s.index("grep -qe '--token-hash'") < s.index("printf '%s' deadbeef")
    # Marker written just before launch; command ends with bare ffast-server so
    # connect_to_cluster's --port/--token flags attach to it.
    assert "printf '%s' deadbeef > ~/.ffast/installed.sha256 && ffast-server" in s
    assert s.endswith("&& ffast-server")


def test_provision_launch_cmd_no_light_deps():
    s = provision_launch_cmd("~/v", "~/w.whl", [], [], "~/m", "sha")
    installs = [p for p in s.split(" && ") if p.startswith("python -m pip install")]
    assert len(installs) == 1  # only the wheel install, no light-deps step
    assert "--no-deps --force-reinstall" in installs[0]


async def test_provision_node_build_failure_raises_clustererror(tmp_path):
    """A wheel-build failure surfaces as ClusterError (the type connect handles
    and logs), not an unhandled RuntimeError. tmp_path has no pyproject.toml, so
    the build fails before any SSH — cluster-free."""
    profile = ClusterProfile(name="x", host="nonexistent.invalid", provision=True)
    with pytest.raises(ClusterError):
        await provision_node(profile, project_root=tmp_path)
