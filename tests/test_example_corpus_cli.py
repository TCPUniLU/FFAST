"""Each published pair computes the expected errors through the real CLI."""
import json
import subprocess
import sys

import ase.io
import numpy as np
import pytest

from tests.ffast.test_example_corpus import DATA, PAIRS, ROOT, expected_arrays


@pytest.mark.integration
@pytest.mark.parametrize("folder,reference,prediction,count,ref_prefix,pred_prefix", PAIRS)
def test_default_cli_errors_match_independent_ase_calculation(
        folder, reference, prediction, count, ref_prefix, pred_prefix):
    ref, pred = [DATA / folder / name for name in (reference, prediction)]
    ref_e, ref_f = expected_arrays(ase.io.read(ref, index=":"), ref_prefix)
    pred_e, pred_f = expected_arrays(ase.io.read(pred, index=":"), pred_prefix)
    expected = {
        "ffast.energy_difference[dataset;mae]": np.mean(np.abs(pred_e - ref_e)),
        "ffast.force_difference[dataset;component;mae]": np.mean(np.abs(pred_f - ref_f)),
    }
    for metric, value in expected.items():
        result = subprocess.run(
            [sys.executable, "-c", "from ffast.cli.main import main; main()",
             "metrics", "run", metric, "--dataset", str(ref),
             "--prediction", str(pred), "--json"],
            cwd=ROOT, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        answer = json.loads(result.stdout)
        assert answer["metric_id"] == metric
        assert answer["values"] == pytest.approx(float(value), rel=1e-10, abs=1e-10)
