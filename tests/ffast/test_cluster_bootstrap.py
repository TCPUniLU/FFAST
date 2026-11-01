"""Stage 1 of cluster auto-bootstrap (ADR 0028): the pure decision helpers."""

from pathlib import Path

import pytest

import cluster.bootstrap as bootstrap
from cluster.backend import ClusterError
from cluster.config import ClusterProfile
from cluster.bootstrap import (
    provision_node,
    HEAVY_DEPS,
    NODE_DIR,
    _dist_name,
    install_marker,
    light_dependencies,
    needs_provision,
    provision_launch_cmd,
    read_dependencies,
    server_launch_cmd,
    timed,
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


def _checkout(root: Path, files: dict | None = None) -> Path:
    """A minimal checkout: a pyproject plus the given files."""
    files = {
        "pyproject.toml": '[project]\ndependencies = ["torch>=2.8", "msgpack>=1.0"]\n',
        "ffast/__init__.py": "",
        "server.py": "def cli(): pass\n",
        "ffast/config/default.json": "{}",
        **(files or {}),
    }
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return root


def test_install_marker_is_the_same_for_the_same_checkout(tmp_path):
    a = _checkout(tmp_path / "a")
    b = _checkout(tmp_path / "b")
    assert install_marker(a, ["msgpack"]) == install_marker(b, ["msgpack"])
    # File times do not count: touching a file keeps the marker.
    (b / "server.py").touch()
    assert install_marker(a, ["msgpack"]) == install_marker(b, ["msgpack"])


def test_install_marker_changes_with_code_json_or_deps(tmp_path):
    base = install_marker(_checkout(tmp_path / "a"), ["msgpack"])
    code = install_marker(_checkout(tmp_path / "b", {"server.py": "X = 1\n"}), ["msgpack"])
    plugin = install_marker(
        _checkout(tmp_path / "c", {"modules/tabs/configTabs.py": "N = 1\n"}), ["msgpack"])
    json_ = install_marker(
        _checkout(tmp_path / "d", {"ffast/config/default.json": '{"a": 1}'}), ["msgpack"])
    deps = install_marker(_checkout(tmp_path / "e"), ["msgpack", "pydantic"])
    assert len({base, code, plugin, json_, deps}) == 5


def test_install_marker_of_this_checkout_is_stable():
    light = light_dependencies(read_dependencies(REPO_ROOT / "pyproject.toml"))
    assert install_marker(REPO_ROOT, light) == install_marker(REPO_ROOT, light)


def test_needs_provision():
    assert needs_provision("abc", None) is True          # never installed
    assert needs_provision("abc", "abc") is False         # up to date
    assert needs_provision("abc", "abc\n") is False        # trailing newline
    assert needs_provision("abc", "def") is True           # code changed


def test_timed_wraps_a_step_in_the_jobs_own_shell():
    s = timed("activate", "source ~/v/bin/activate")
    # A { ...; } group, not a ( ... ) subshell, so `source` / `module load`
    # still apply to the later steps.
    assert s.startswith("{ ") and s.endswith("; }")
    assert "&& source ~/v/bin/activate &&" in s
    assert "printf 'ffast-bootstrap step %s %d.%d s\\n' 'activate'" in s


def test_server_launch_cmd():
    s = server_launch_cmd("~/.ffast/venv", ["Python/3.11", "PyTorch"])
    assert s == " && ".join([
        timed("module load Python/3.11", "module load Python/3.11"),
        timed("module load PyTorch", "module load PyTorch"),
        timed("activate", "source ~/.ffast/venv/bin/activate"),
        timed("check server", bootstrap._VERIFY_SERVER),
        "ffast-server",
    ])
    # Compatibility gate runs before the real launch.
    assert "grep -qe '--token-hash'" in s
    assert s.endswith("&& ffast-server")
    # No modules -> just activate + verify + run.
    assert server_launch_cmd("~/.ffast/venv", []).startswith(
        timed("activate", "source ~/.ffast/venv/bin/activate") + " && "
    )


def test_provision_launch_cmd():
    s = provision_launch_cmd(
        venv_path="~/.ffast/venv",
        remote_wheel="~/.ffast/ffast-2.0.0-py3-none-any.whl",
        light_deps=["msgpack", "websockets"],
        modules=["Python/3.11"],
        marker_path="~/.ffast/installed.sha256",
        marker="deadbeef",
    )
    # One &&-chained command run AS the SLURM job (venv+install on the node).
    assert s.startswith(timed("module load Python/3.11", "module load Python/3.11") + " && ")
    assert "([ -d ~/.ffast/venv ] || python -m venv --system-site-packages ~/.ffast/venv)" in s
    assert "source ~/.ffast/venv/bin/activate" in s
    # uv is added to the venv once, then installs the wheel and the deps.
    assert "([ -x ~/.ffast/venv/bin/uv ] || python -m pip install uv)" in s
    assert s.index("bin/uv ]") < s.index("uv pip install --no-deps")
    assert "uv pip install --no-deps --reinstall ~/.ffast/ffast-2.0.0-py3-none-any.whl" in s
    assert "uv pip install msgpack websockets" in s
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
    assert s.count("uv pip install") == 1  # only the wheel, no light-deps step
    assert "--no-deps --reinstall" in s
    assert "'install deps'" not in s


async def test_provision_node_not_a_checkout_raises_clustererror(tmp_path):
    """A folder that is not a checkout surfaces as ClusterError (the type connect
    handles and logs). tmp_path has no pyproject.toml, so it fails before any
    SSH — cluster-free."""
    profile = ClusterProfile(name="x", host="nonexistent.invalid", provision=True)
    with pytest.raises(ClusterError):
        await provision_node(profile, project_root=tmp_path)


# ── provision_node: skip + SSH-failure branches (SSH surface faked) ───────────
#
# These exercise the *login-node* logic without a real cluster by stubbing the
# three module-level SSH/subprocess entry points (build_server_wheel, _ssh_run,
# _scp_push). The checkout is a minimal tree, so its install marker is known and
# the "already up to date" marker can be matched.

def _install_fake_build(monkeypatch):
    builds = []

    def _fake_build(project_root, out_dir):
        p = Path(out_dir) / "ffast-2.0.0-py3-none-any.whl"
        p.write_bytes(b"fake-wheel")
        builds.append(p)
        return p

    monkeypatch.setattr(bootstrap, "build_server_wheel", _fake_build)
    return builds


async def test_provision_node_skips_when_node_up_to_date(tmp_path, monkeypatch):
    """When the on-node marker already matches this checkout, provisioning is
    skipped: no build, no mkdir, no scp — just the plain launch command."""
    builds = _install_fake_build(monkeypatch)
    _checkout(tmp_path)
    marker = install_marker(tmp_path, ["msgpack>=1.0"])
    ssh_calls = []
    scp_calls = []

    async def _fake_ssh_run(profile, *args, stdin=None):
        ssh_calls.append(args)
        if args[0] == "cat":                     # marker read → matches local sha
            return (marker, "", 0)
        return ("", "", 0)

    async def _fake_scp(profile, local, remote):
        scp_calls.append((local, remote))

    monkeypatch.setattr(bootstrap, "_ssh_run", _fake_ssh_run)
    monkeypatch.setattr(bootstrap, "_scp_push", _fake_scp)

    profile = ClusterProfile(
        name="x", host="login.fake", provision=True,
        modules=["Python/3.11"], venv_path="~/.ffast/venv",
    )
    cmd = await provision_node(profile, project_root=tmp_path)

    # Skip path returns the "activate existing venv" launch command verbatim.
    assert cmd == server_launch_cmd("~/.ffast/venv", ["Python/3.11"])
    assert builds == []                                      # nothing built
    assert scp_calls == []                                   # nothing staged
    assert all(a[0] != "mkdir" for a in ssh_calls)           # no provisioning


async def test_provision_node_builds_and_records_the_marker_when_stale(tmp_path, monkeypatch):
    """An old marker (e.g. a wheel sha256 from before ADR 0074) reinstalls, and
    the job records this checkout's marker."""
    builds = _install_fake_build(monkeypatch)
    _checkout(tmp_path)
    marker = install_marker(tmp_path, ["msgpack>=1.0"])

    async def _fake_ssh_run(profile, *args, stdin=None):
        if args[0] == "cat":
            return ("an-old-wheel-sha256", "", 0)
        return ("", "", 0)

    async def _fake_scp(profile, local, remote):
        pass

    monkeypatch.setattr(bootstrap, "_ssh_run", _fake_ssh_run)
    monkeypatch.setattr(bootstrap, "_scp_push", _fake_scp)

    profile = ClusterProfile(name="x", host="login.fake", provision=True,
                             venv_path="~/v")
    cmd = await provision_node(profile, project_root=tmp_path)
    assert len(builds) == 1
    assert f"printf '%s' {marker} > ~/v/.ffast-install.sha256" in cmd
    # Quoted, or the shell reads `>=1.0` as a redirect to a file `=1.0`.
    assert "uv pip install 'msgpack>=1.0'" in cmd


async def test_provision_node_mkdir_failure_raises_clustererror(tmp_path, monkeypatch):
    """A failed remote mkdir (bad SSH auth / wrong host) surfaces as ClusterError."""
    _install_fake_build(monkeypatch)
    _checkout(tmp_path)

    async def _fake_ssh_run(profile, *args, stdin=None):
        if args[0] == "cat":
            return ("", "no such file", 1)      # no marker → needs provisioning
        if args[0] == "mkdir":
            return ("", "Permission denied", 1)  # mkdir fails
        return ("", "", 0)

    monkeypatch.setattr(bootstrap, "_ssh_run", _fake_ssh_run)

    profile = ClusterProfile(name="x", host="login.fake", provision=True)
    with pytest.raises(ClusterError, match=f"Could not create {NODE_DIR}"):
        await provision_node(profile, project_root=tmp_path)


async def test_provision_node_scp_failure_raises_clustererror(tmp_path, monkeypatch):
    """A failed wheel scp propagates the ClusterError raised by _scp_push."""
    _install_fake_build(monkeypatch)
    _checkout(tmp_path)

    async def _fake_ssh_run(profile, *args, stdin=None):
        if args[0] == "cat":
            return ("", "", 1)                   # no marker → needs provisioning
        return ("", "", 0)                       # mkdir succeeds

    async def _fake_scp(profile, local, remote):
        raise ClusterError("Failed to copy the server wheel", "scp exit 1")

    monkeypatch.setattr(bootstrap, "_ssh_run", _fake_ssh_run)
    monkeypatch.setattr(bootstrap, "_scp_push", _fake_scp)

    profile = ClusterProfile(name="x", host="login.fake", provision=True)
    with pytest.raises(ClusterError, match="Failed to copy the server wheel"):
        await provision_node(profile, project_root=tmp_path)
