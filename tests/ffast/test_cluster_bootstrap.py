"""Stage 1 of cluster auto-bootstrap (ADR 0028): the pure decision helpers."""

import hashlib
from pathlib import Path

from cluster.bootstrap import (
    HEAVY_DEPS,
    _dist_name,
    light_dependencies,
    needs_provision,
    read_dependencies,
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
