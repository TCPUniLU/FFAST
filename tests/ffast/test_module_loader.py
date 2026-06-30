import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ffast.config.loader import load_metric_modules, load_project_config
from ffast.config.models import MetricModuleConfig
from ffast.metrics.registry import MetricRegistry


CUSTOM_MODULE = """\
from ffast.metrics.registry import metric

@metric(
    id="test.custom_metric",
    inputs={"x": "reference.x"},
    shape="scalar",
    unit="energy",
)
def custom_metric(x):
    return x.mean()
"""


def test_load_metric_modules_registers_metric(tmp_path):
    module_file = tmp_path / "custom.py"
    module_file.write_text(CUSTOM_MODULE)

    config_file = tmp_path / "ffast.toml"
    config_file.write_text(f'[[metrics.modules]]\npath = "custom.py"\n')

    config = load_project_config(config_file)
    load_metric_modules(config, config_file)

    from ffast.metrics.registry import _default_registry
    _, fn = _default_registry.get("test.custom_metric")
    assert callable(fn)


def test_disabled_module_not_loaded(tmp_path):
    module_file = tmp_path / "disabled.py"
    module_file.write_text(CUSTOM_MODULE.replace("test.custom_metric", "test.disabled_metric"))

    config_file = tmp_path / "ffast.toml"
    config_file.write_text(
        '[[metrics.modules]]\npath = "disabled.py"\nenabled = false\n'
    )

    config = load_project_config(config_file)
    load_metric_modules(config, config_file)

    from ffast.metrics.registry import _default_registry
    try:
        _default_registry.get("test.disabled_metric")
        assert False, "should not be registered"
    except KeyError:
        pass


# ── import-path loading ─────────────────────────────────────────────────────

def test_load_metric_module_by_import_path(tmp_path, monkeypatch):
    pkg_dir = tmp_path / "imp_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "metrics_mod.py").write_text(
        CUSTOM_MODULE.replace("test.custom_metric", "test.import_path_metric")
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    config_file = tmp_path / "ffast.toml"
    config_file.write_text(
        '[[metrics.modules]]\nimport_path = "imp_pkg.metrics_mod"\n'
    )

    config = load_project_config(config_file)
    load_metric_modules(config, config_file)

    from ffast.metrics.registry import _default_registry
    _, fn = _default_registry.get("test.import_path_metric")
    assert callable(fn)


# ── source validation ───────────────────────────────────────────────────────

def test_module_config_requires_a_source():
    with pytest.raises(ValidationError):
        MetricModuleConfig()


def test_module_config_rejects_both_sources():
    with pytest.raises(ValidationError):
        MetricModuleConfig(path="custom.py", import_path="pkg.mod")


def test_module_config_accepts_path_only():
    cfg = MetricModuleConfig(path="custom.py")
    assert cfg.path == "custom.py"
    assert cfg.import_path is None


def test_module_config_accepts_import_path_only():
    cfg = MetricModuleConfig(import_path="pkg.mod")
    assert cfg.import_path == "pkg.mod"
    assert cfg.path is None
