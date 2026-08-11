import json
import sys
from pathlib import Path

import numpy as np
import pytest

from ffast.cli.main import main

_DATA = Path(__file__).resolve().parents[2] / "examples" / "data" / "variable-sized-molecular"
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
            "--dataset", "examples/data/variable-sized-molecular/dataset.xyz",
            "--prediction", "examples/data/variable-sized-molecular/prediction.xyz",
            "--param", "bogus=1",
        ])
    assert exc.value.code == 1
    assert "unknown parameter 'bogus'" in capsys.readouterr().err


def test_metrics_run_needs_prediction(capsys):
    """A prediction-dependent metric without --prediction fails with guidance."""
    with pytest.raises(SystemExit) as exc:
        main([
            "metrics", "run", "ffast.force_mae_global",
            "--dataset", "examples/data/variable-sized-molecular/dataset.xyz",
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


def test_metrics_validate_bad_expr_exits_1(tmp_path, capsys):
    # A shape-mismatched Expression Metric is a Configuration Failure: validate
    # surfaces the precise error at config-load and exits non-zero (ADR 0042 —
    # errors surface at config-load, not plot time).
    config_file = tmp_path / "ffast.toml"
    config_file.write_text(
        '[[metrics.expr]]\n'
        'id = "projtest.mixedcli"\n'
        'expr = "e + f"\n'
        '[metrics.expr.vars]\n'
        'e = "reference.energies"\n'
        'f = "reference.forces"\n'
    )
    with pytest.raises(SystemExit) as exc:
        main(["metrics", "validate", "--config", str(config_file)])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "projtest.mixedcli" in err
    assert "config-load" in err.lower() or "Metric Shapes" in err


# ── dataset keys ───────────────────────────────────────────────────────────

def _write_xyz_with_fields(path):
    """Write a single-frame extxyz (raw text, for full control over the
    Properties/comment line) with a numeric-scalar info field (``myval``) and a
    per-atom array field (``charge``), so `dataset keys` reports both as usable."""
    path.write_text(
        "3\n"
        'Properties=species:S:1:pos:R:3:foo:R:1 myval=2.5 pbc="F F F"\n'
        "H 0.0 0.0 0.0 0.1\n"
        "H 0.0 0.0 1.0 -0.2\n"
        "O 0.0 1.0 0.0 0.1\n"
    )
    return path


def test_dataset_keys_lists_frame_and_atom_fields(tmp_path, capsys):
    path = _write_xyz_with_fields(tmp_path / "d.xyz")
    main(["dataset", "keys", str(path)])
    out = capsys.readouterr().out
    assert "reference.info.<key>" in out
    assert "reference.atoms.<key>" in out
    # The numeric-scalar info field and per-atom array field are both usable (✓).
    assert "myval" in out
    assert "foo" in out
    assert "✓" in out


def test_dataset_keys_unreadable_file_exits_1(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["dataset", "keys", str(tmp_path / "does_not_exist.xyz")])
    assert exc.value.code == 1
    assert "could not read" in capsys.readouterr().err


# ── stages list / inspect / test ─────────────────────────────────────────────

def test_stages_list_includes_builtins(capsys):
    main(["stages", "list"])
    out = capsys.readouterr().out
    assert "ffast.atom_colors" in out
    assert "ffast.atom_positions" in out


def test_stages_inspect_emits_schema_json(capsys):
    main(["stages", "inspect", "ffast.atom_colors"])
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "ffast.atom_colors"


def test_stages_inspect_unknown_exits_1(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["stages", "inspect", "ffast.nonexistent_stage"])
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_stages_test_reports_no_tests_for_testless_stage(capsys):
    # ffast.atom_colors carries no embedded tests → the command reports that and
    # exits 0 (the no-tests branch), rather than running or failing anything.
    main(["stages", "test", "ffast.atom_colors"])
    out = capsys.readouterr().out
    assert "ffast.atom_colors: no tests" in out


def test_stages_test_unknown_exits_1(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["stages", "test", "ffast.nonexistent_stage"])
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


# ── metrics test ─────────────────────────────────────────────────────────────

def test_metrics_test_reports_ok(tmp_path, capsys):
    # ffast.accel_mae carries embedded correctness/determinism tests; the command
    # runs them twice and prints "OK <label>" for each, exiting 0.
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    main(["metrics", "test", "ffast.accel_mae", "--config", str(config_file)])
    out = capsys.readouterr().out
    assert "OK" in out
    assert "ffast.accel_mae[0]" in out


def test_metrics_test_unknown_exits_1(tmp_path, capsys):
    config_file = tmp_path / "ffast.toml"
    config_file.write_text("")
    with pytest.raises(SystemExit) as exc:
        main(["metrics", "test", "ffast.nonexistent", "--config", str(config_file)])
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


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
