"""Smoke test: examples/headless/headless.py still runs, on every bundled pair.

This is deliberately a wrapper around the example rather than a copy of it. The
example's other job is to be read, so it stays free of assertions; this test
runs it as a user would and checks the numbers that come out. It is the only
test that exercises the whole headless path end to end — plugin discovery,
loaders, the task manager, the metric worker pool, the cache and session save —
so it catches wiring that unit tests cannot see, at the cost of saying little
about *why* a failure happened.

Marked integration: it spawns five subprocesses and takes tens of seconds.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = REPO_ROOT / "examples" / "headless" / "headless.py"
_DATA = REPO_ROOT / "examples" / "data"

# Measured against the bundled files. Units differ per directory — see
# examples/data/README.md — so these are not comparable across rows.
_CASES = {
    "variable-sized-molecular": (
        "variable-sized-molecular/dataset.xyz",
        "variable-sized-molecular/prediction.xyz",
        None,
        {"Energy MAE": 0.0218, "Energy RMSE": 0.0318,
         "Force MAE": 0.0293, "Force RMSE": 0.0534},
    ),
    "fixed-sized-molecular": (
        "fixed-sized-molecular/md22_stachyose_sampled.xyz",
        "fixed-sized-molecular/predictions_md22_stachyose.xyz",
        None,
        {"Energy MAE": 3.4917, "Energy RMSE": 4.5126,
         "Force MAE": 5.3341, "Force RMSE": 6.1890},
    ),
    # AM26 and MPtrj hold several energy-like keys, and the one the loader would
    # pick unaided (energy_per_at / energy_per_atom) is copied verbatim into the
    # prediction file — scoring a perfect zero against itself. --energy-key
    # names the predicted one. 6.29 eV over 216 atoms is the 29 meV/atom the
    # README quotes from an independent calculation.
    "fixed-sized-periodic": (
        "fixed-sized-periodic/am26_subbed_100.extxyz",
        "fixed-sized-periodic/predictions_am26_subbed.xyz",
        "energy",
        {"Energy MAE": 6.2916, "Energy RMSE": 8.0942,
         "Force MAE": 1.9337, "Force RMSE": 2.1457},
    ),
    "variable-sized-periodic": (
        "variable-sized-periodic/mptrj_sampled_over_200.extxyz",
        "variable-sized-periodic/predictions_mptrj_sampled.xyz",
        "energy",
        {"Energy MAE": 0.1132, "Energy RMSE": 0.1914,
         "Force MAE": 0.0928, "Force RMSE": 0.2250},
    ),
    # An npz prediction, read by the sGDML loader. The huge energy error and the
    # 44 eV/A force error are real: this prediction is in eV while its reference
    # is in kcal/mol (README, "Two defects"). Pinned as-is so that if anyone ever
    # converts the file, this test says so instead of quietly agreeing.
    "fixed-sized-subsystem": (
        "fixed-sized-subsystem/graphene_sampled.xyz",
        "fixed-sized-subsystem/graphene_prediction.npz",
        None,
        {"Energy MAE": 2489612.9584, "Energy RMSE": 2489612.9585,
         "Force MAE": 44.3516, "Force RMSE": 49.3268},
    ),
}

_LINE = re.compile(r"^(Energy MAE|Energy RMSE|Force MAE|Force RMSE):\s+(\S+)$", re.M)


def _run(dataset, prediction, energy_key, results_dir):
    argv = [sys.executable, str(_SCRIPT), str(dataset), str(prediction),
            "--results", str(results_dir)]
    if energy_key is not None:
        argv += ["--energy-key", energy_key]
    return subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)


@pytest.mark.integration
@pytest.mark.parametrize("case", sorted(_CASES))
def test_headless_example_reports_expected_metrics(case, tmp_path):
    rel_dataset, rel_prediction, energy_key, expected = _CASES[case]
    dataset, prediction = _DATA / rel_dataset, _DATA / rel_prediction
    if not (dataset.exists() and prediction.exists()):
        pytest.skip(f"example data missing: {rel_dataset}")

    proc = _run(dataset, prediction, energy_key, tmp_path / "results")

    assert proc.returncode == 0, f"example failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    assert "FAILED" not in proc.stdout, f"a metric did not compute:\n{proc.stdout[-3000:]}"

    reported = {label: float(value) for label, value in _LINE.findall(proc.stdout)}
    assert set(reported) == set(expected), f"missing metric lines: {proc.stdout[-2000:]}"

    for label, want in expected.items():
        got = reported[label]
        assert got == pytest.approx(want, rel=1e-3), f"{case} {label}: {got} != {want}"

    assert (tmp_path / "results" / "info.json").exists(), "session was not saved"
