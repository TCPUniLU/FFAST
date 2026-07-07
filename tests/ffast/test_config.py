import tomllib

import pytest
from pydantic import ValidationError

from ffast.config.loader import discover_config, load_project_config, save_project_config
from ffast.config.models import AtomColorPresentation, MetricModuleConfig, MetricsConfig, ProjectConfig, VisualizationConfig


def test_empty_toml_gives_defaults(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    config = load_project_config(config_file)
    assert config.metrics.modules == []


def test_module_entry_parsed(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("""
[[metrics.modules]]
path = "metrics/custom.py"
""")
    config = load_project_config(config_file)
    assert len(config.metrics.modules) == 1
    assert config.metrics.modules[0].path == "metrics/custom.py"
    assert config.metrics.modules[0].enabled is True


def test_disabled_module(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("""
[[metrics.modules]]
path = "metrics/custom.py"
enabled = false
""")
    config = load_project_config(config_file)
    assert config.metrics.modules[0].enabled is False


def test_unknown_key_rejected(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("unknown_key = 42\n")
    with pytest.raises(ValidationError):
        load_project_config(config_file)


def test_unparseable_toml_raises_decode_error(tmp_path):
    # Syntactically broken TOML (unterminated array) surfaces as a tomllib
    # decode error out of load_project_config, not a pydantic error.
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("x = [1, 2\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_project_config(config_file)


def test_wrong_typed_field_raises_validation_error(tmp_path):
    # A structurally valid TOML whose value has the wrong type for its pydantic
    # field (vmin must be float|None; a non-numeric string cannot coerce).
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("""
[visualization.atom_color]
metric_id = "ffast.force_mae"
vmin = "not_a_number"
""")
    with pytest.raises(ValidationError):
        load_project_config(config_file)


def test_discover_finds_config_in_parent(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    subdir = tmp_path / "project" / "data"
    subdir.mkdir(parents=True)
    assert discover_config(subdir) == config_file


def test_discover_returns_none_when_missing(tmp_path):
    subdir = tmp_path / "project"
    subdir.mkdir()
    assert discover_config(subdir) is None


def test_atom_color_presentation_parsed(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("""
[visualization.atom_color]
metric_id = "ffast.force_mae"
colormap = "plasma"
vmin = 0.0
vmax = 1.0
""")
    config = load_project_config(config_file)
    ac = config.visualization.atom_color
    assert ac is not None
    assert ac.metric_id == "ffast.force_mae"
    assert ac.colormap == "plasma"
    assert ac.vmin == 0.0
    assert ac.vmax == 1.0


def test_atom_color_defaults(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("""
[visualization.atom_color]
metric_id = "ffast.force_mae"
""")
    config = load_project_config(config_file)
    ac = config.visualization.atom_color
    assert ac.colormap == "viridis"
    assert ac.vmin is None
    assert ac.vmax is None


def test_no_atom_color_is_none(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    config = load_project_config(config_file)
    assert config.visualization.atom_color is None


# --- write-path round-trip tests ---

def test_save_empty_config_produces_empty_toml(tmp_path):
    config_file = tmp_path / "ffast.toml"
    save_project_config(ProjectConfig(), config_file)
    reloaded = load_project_config(config_file)
    assert reloaded.metrics.modules == []
    assert reloaded.visualization.atom_color is None


def test_save_load_atom_color_round_trip(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config = ProjectConfig(
        visualization=VisualizationConfig(
            atom_color=AtomColorPresentation(
                metric_id="ffast.force_mae",
                colormap="plasma",
                vmin=0.0,
                vmax=2.5,
            )
        )
    )
    save_project_config(config, config_file)
    reloaded = load_project_config(config_file)
    ac = reloaded.visualization.atom_color
    assert ac is not None
    assert ac.metric_id == "ffast.force_mae"
    assert ac.colormap == "plasma"
    assert ac.vmin == 0.0
    assert ac.vmax == 2.5


def test_save_load_atom_color_defaults_omits_none(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config = ProjectConfig(
        visualization=VisualizationConfig(
            atom_color=AtomColorPresentation(metric_id="ffast.energy_mae")
        )
    )
    save_project_config(config, config_file)
    reloaded = load_project_config(config_file)
    ac = reloaded.visualization.atom_color
    assert ac.colormap == "viridis"
    assert ac.vmin is None
    assert ac.vmax is None


def test_save_load_metric_module_path(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config = ProjectConfig(
        metrics=MetricsConfig(
            modules=[
                MetricModuleConfig(path="metrics/custom.py"),
                MetricModuleConfig(path="metrics/other.py", enabled=False),
            ]
        )
    )
    save_project_config(config, config_file)
    reloaded = load_project_config(config_file)
    assert len(reloaded.metrics.modules) == 2
    assert reloaded.metrics.modules[0].path == "metrics/custom.py"
    assert reloaded.metrics.modules[0].enabled is True
    assert reloaded.metrics.modules[1].path == "metrics/other.py"
    assert reloaded.metrics.modules[1].enabled is False


def test_save_load_metric_module_import_path(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config = ProjectConfig(
        metrics=MetricsConfig(
            modules=[MetricModuleConfig(import_path="mypackage.metrics")]
        )
    )
    save_project_config(config, config_file)
    reloaded = load_project_config(config_file)
    assert reloaded.metrics.modules[0].import_path == "mypackage.metrics"


def test_save_overwrites_existing_file(tmp_path):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("unknown_key = 1\n")
    config = ProjectConfig()
    save_project_config(config, config_file)
    reloaded = load_project_config(config_file)
    assert reloaded.metrics.modules == []
