import json
import sys
from pathlib import Path

import pytest

from ffast.cli.main import main

_DATA = Path(__file__).resolve().parents[2] / "examples" / "data"
_DATASET = _DATA / "dataset.xyz"
_PREDICTION = _DATA / "prediction.xyz"
_has_example_data = _DATASET.exists() and _PREDICTION.exists()


def test_config_validate_ok(tmp_path, capsys):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    main(["config", "validate", str(config_file)])
    assert "OK" in capsys.readouterr().out


def test_config_validate_bad(tmp_path, capsys):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("unknown_key = 42\n")
    with pytest.raises(SystemExit) as exc:
        main(["config", "validate", str(config_file)])
    assert exc.value.code == 1


def test_config_validate_missing_file(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["config", "validate", str(tmp_path / "missing.toml")])
    assert exc.value.code == 1


def test_metrics_list(tmp_path, capsys):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    main(["metrics", "list", "--config", str(config_file)])
    out = capsys.readouterr().out
    assert "ffast.force_mae" in out
    assert "ffast.energy_mae" in out


def test_metrics_inspect(tmp_path, capsys):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    main(["metrics", "inspect", "ffast.force_mae", "--config", str(config_file)])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["id"] == "ffast.force_mae"
    assert data["shape"] == "N_atoms"


def test_metrics_inspect_unknown(tmp_path, capsys):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    with pytest.raises(SystemExit) as exc:
        main(["metrics", "inspect", "ffast.nonexistent", "--config", str(config_file)])
    assert exc.value.code == 1


# ── metrics run ──────────────────────────────────────────────────────────────
# These error-path tests exit before the headless Environment is started, so
# they stay fast and have no I/O dependencies.

def test_metrics_run_unknown_metric(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["metrics", "run", "ffast.nonexistent", "--dataset", str(tmp_path / "x.xyz")])
    assert exc.value.code == 1


def test_metrics_run_unknown_param(capsys):
    with pytest.raises(SystemExit) as exc:
        main([
            "metrics", "run", "ffast.force_mae",
            "--dataset", "examples/data/dataset.xyz",
            "--prediction", "examples/data/prediction.xyz",
            "--param", "bogus=1",
        ])
    assert exc.value.code == 1
    assert "unknown parameter 'bogus'" in capsys.readouterr().err


def test_metrics_run_needs_prediction(capsys):
    """A prediction-dependent metric without --prediction fails with guidance."""
    with pytest.raises(SystemExit) as exc:
        main([
            "metrics", "run", "ffast.force_mae_global",
            "--dataset", "examples/data/dataset.xyz",
        ])
    assert exc.value.code == 1
    assert "requires model predictions" in capsys.readouterr().err


def test_metrics_run_missing_dataset_file(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["metrics", "run", "ffast.gyradius", "--dataset", "no/such/file.xyz"])
    assert exc.value.code == 1
    assert "dataset not found" in capsys.readouterr().err


def test_metrics_validate_ok(tmp_path, capsys, monkeypatch):
    # Validate against a controlled fresh registry: other suite tests register
    # throwaway metrics into the process-global registry (some with refs that
    # intentionally fail freeze), which would otherwise make this assertion
    # order-dependent. The graph-validation logic itself is covered exhaustively
    # in test_metric_graph.py.
    import ffast.cli.main as cli_main
    from ffast.metrics import dims
    from ffast.metrics.registry import MetricRegistry

    fresh = MetricRegistry()
    fresh.metric(
        id="t.ok_ref",
        inputs={"r": "reference.energies"},
        shape=(dims.scalar,),
        unit="energy",
    )(lambda r: r)
    monkeypatch.setattr(cli_main, "_default_registry", fresh)

    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    main(["metrics", "validate", "--config", str(config_file)])
    out = capsys.readouterr().out
    assert "OK" in out
    assert "graph valid" in out


@pytest.mark.skipif(not _has_example_data, reason="examples/data not present")
def test_metrics_run_with_prediction_end_to_end(capsys):
    """Full path: load dataset + prediction file, compute a scalar metric."""
    import asyncio
    # Other suite tests may close/clear the process-global event loop; the
    # headless Environment's TaskManager grabs asyncio.get_event_loop() at
    # construction, which raises on a cleared loop. A real `ffast-cli` process
    # always has one — ensure the same here so this test is order-independent.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main([
        "metrics", "run", "ffast.force_mae_global",
        "--dataset", str(_DATASET),
        "--prediction", str(_PREDICTION),
        "--json",
    ])
    data = json.loads(capsys.readouterr().out)
    assert data["metric_id"] == "ffast.force_mae_global"
    assert data["shape"] == "scalar"
    assert data["unit"] == "force"
    assert isinstance(data["values"], (int, float))
    assert data["values"] >= 0.0
