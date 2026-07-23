"""Packaging gates for ADR 0045 Phase 6 (distribution).

Issue #26 acceptance criteria, verified against a real built wheel:

  * `pipx install` yields a working ``ffast`` launch on a clean environment
    with no system Qt/GL — i.e. the ``ffast`` console script points at the web
    launcher (not the Qt desktop), and the core wheel carries no Qt/GL
    dependency.
  * The install pulls no CDN assets at runtime — the vendored static web
    assets (Three.js, Plotly, and every ``.js``/``.html``) actually ship
    inside the wheel.

Building a wheel is slow, so the ``built_wheel`` fixture is module-scoped and
the whole module is marked ``integration``. The pure-metadata checks
(entry points, core deps) read ``pyproject.toml`` directly and stay fast.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

try:  # tomllib is stdlib on 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - <3.11
    import tomli as tomllib


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


class TestEntryPoints:
    """The ``ffast`` command must launch the web client (ADR 0045 Phase 6:
    Qt retirement), while the Qt desktop stays reachable as an escape hatch."""

    def test_ffast_points_at_web_launcher(self, pyproject):
        scripts = pyproject["project"]["scripts"]
        assert scripts["ffast"] == "ffast.renderers.web.launcher:main", (
            "ADR 0045 Phase 6 flips `ffast` to the web launcher; it must not "
            "point at the Qt desktop (main:cli)"
        )

    def test_qt_desktop_reachable_via_ffast_qt(self, pyproject):
        scripts = pyproject["project"]["scripts"]
        assert scripts.get("ffast-qt") == "main:cli", (
            "the Qt desktop must stay reachable as `ffast-qt` after the flip"
        )

    def test_headless_scripts_present(self, pyproject):
        scripts = pyproject["project"]["scripts"]
        assert scripts["ffast-server"] == "server:cli"
        assert scripts["ffast-cli"] == "ffast.cli.main:main"


class TestCoreDependencies:
    """A clean web/headless install must not drag in Qt/GL — those live only
    in the optional ``[gui]`` extra."""

    _QT_GL_TOKENS = ("pyside", "pyqt", "vispy", "pyopengl", "qasync")

    def test_core_deps_have_no_qt_or_gl(self, pyproject):
        deps = " ".join(pyproject["project"]["dependencies"]).lower()
        for token in self._QT_GL_TOKENS:
            assert token not in deps, f"core deps must not include {token!r}"

    def test_gui_extra_carries_qt(self, pyproject):
        gui = " ".join(
            pyproject["project"]["optional-dependencies"]["gui"]
        ).lower()
        assert "pyside6" in gui and "vispy" in gui


@pytest.mark.integration
class TestWheelContents:
    """Build a real wheel and assert the vendored web assets ship inside it —
    the difference between a working `ffast` after `pipx install` and a
    launcher that 404s every asset."""

    @pytest.fixture(scope="class")
    def wheel_names(self, tmp_path_factory) -> list[str]:
        out = tmp_path_factory.mktemp("wheel")
        # Build via the PEP 517 backend directly (setuptools.build_meta) rather
        # than `python -m build`: the backend is guaranteed present (it is this
        # project's build-backend) whereas `build`/`pip` may not be installed
        # in the test env. Run in a subprocess with cwd=REPO_ROOT so the backend
        # reads pyproject.toml from the source tree without importing it here.
        script = (
            "from setuptools import build_meta as b; "
            f"print(b.build_wheel({str(out)!r}))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"wheel build failed:\n{proc.stderr}"
        wheels = list(out.glob("*.whl"))
        assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
        with zipfile.ZipFile(wheels[0]) as zf:
            return zf.namelist()

    def test_index_html_ships(self, wheel_names):
        assert "ffast/renderers/web/static/index.html" in wheel_names

    def test_viewer_js_ships(self, wheel_names):
        assert "ffast/renderers/web/static/ffast-viewer.js" in wheel_names

    def test_vendored_three_ships(self, wheel_names):
        assert (
            "ffast/renderers/web/static/vendor/three/three.module.min.js"
            in wheel_names
        )

    def test_vendored_plotly_ships(self, wheel_names):
        assert (
            "ffast/renderers/web/static/vendor/plotly/plotly-basic.min.js"
            in wheel_names
        )

    def test_pane_modules_ship(self, wheel_names):
        # The sidebar panes live in a static/ subdir; a non-recursive glob would
        # silently drop them.
        panes = [n for n in wheel_names if "static/panes/" in n and n.endswith(".js")]
        assert len(panes) >= 5, f"expected the pane .js modules to ship, got {panes}"

    def test_all_static_js_ships(self, wheel_names):
        """Every .js/.html under static/ on disk must be in the wheel."""
        static_dir = REPO_ROOT / "ffast" / "renderers" / "web" / "static"
        on_disk = {
            "ffast/renderers/web/static/" + str(p.relative_to(static_dir)).replace("\\", "/")
            for p in static_dir.rglob("*")
            if p.suffix in (".js", ".html")
        }
        missing = on_disk - set(wheel_names)
        assert not missing, f"static assets missing from wheel: {sorted(missing)}"
