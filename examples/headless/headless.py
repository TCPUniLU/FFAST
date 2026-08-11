"""Compute error metrics for a dataset / prediction pair without a UI.

Run it as-is for the bundled SPICE example, or point it at any other pair:

    python examples/headless/headless.py
    python examples/headless/headless.py DATASET PREDICTION [--results DIR]

Every pair under examples/data/ works; see that directory's README for what
each one is and which units it uses.

Files holding more than one energy-like key need --energy-key.  The desktop app
asks; a script cannot, so it takes the first key it finds — and for the AM26 and
MPtrj examples that key is one the prediction copied verbatim from the
reference, which reports a perfect score instead of the model's real error:

    python examples/headless/headless.py \
        examples/data/fixed-sized-periodic/am26_subbed_100.extxyz \
        examples/data/fixed-sized-periodic/predictions_am26_subbed.xyz \
        --energy-key energy
"""

import argparse
import os
import sys
from pathlib import Path

# Set working directory and Python path to the FFAST project root
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from ffast.core.environment import startHeadlessEnvironment

DATASET = "examples/data/variable-sized-molecular/dataset.xyz"
PREDICTION = "examples/data/variable-sized-molecular/prediction.xyz"

# Metrics reported at the end; the rest are their dependencies.
REPORTED = {
    "Energy MAE":  "ffast.energy_mae",
    "Energy RMSE": "ffast.energy_rmse",
    "Force MAE":   "ffast.force_mae_global",
    "Force RMSE":  "ffast.force_rmse_global",
}


def loaderFor(path):
    """"sGDML" reads .npz; everything else goes through the ASE smart loader."""
    return "sGDML" if str(path).endswith(".npz") else "ase (auto)"


def main(datasetPath=DATASET, predictionPath=PREDICTION, savePath="results",
         energyKey=None, forceKey=None):
    # Initialize headless environment
    env = startHeadlessEnvironment()

    # Load dataset. energyKey/forceKey pick between several candidate keys;
    # leave them None when the file holds only one of each.
    env.taskLoadDataset(
        datasetPath, loaderFor(datasetPath),
        selected_energy_key=energyKey, selected_force_key=forceKey,
    )
    env.waitForTasks(verbose=True)

    # Get the loaded dataset and its fingerprint
    dataset = env.getDatasetFromPath(datasetPath)
    if dataset is None:
        print(f"Dataset failed to load: {datasetPath}")
        env.headlessQuit()
        return 1

    # Load pre-computed predictions (energies and forces from some model)
    env.loadPrepredictedDataset(
        predictionPath, dataset.fingerprint,
        selected_energy_key=energyKey, selected_force_key=forceKey,
    )
    env.waitForTasks(verbose=True)

    if not env.models.all():
        print(f"Prediction failed to load: {predictionPath}")
        env.headlessQuit()
        return 1

    # The ghost model created from the prediction file
    model = env.models.all()[0]

    # Queue metric computations
    metrics = [
        ("ffast.energy_mae",        {}),
        ("ffast.energy_rmse",       {}),
        ("ffast.energy_mae_shifted",  {}),
        ("ffast.energy_rmse_shifted", {}),
        ("ffast.force_mae_global",  {}),
        ("ffast.force_rmse_global", {}),
        ("ffast.energy_difference", {}),
        ("ffast.force_mae",         {"norm": "l1"}),
    ]
    for metric_id, params in metrics:
        key = env.data.make_metric_cache_key(metric_id, params, model, dataset)
        env.data.taskGenerateMetric(metric_id, params, model, dataset, key)
    env.waitForTasks(verbose=True)

    # Retrieve computed metrics from cache
    def get_metric(metric_id, params={}):
        key = env.data.make_metric_cache_key(metric_id, params, model, dataset)
        result = env.data.getCacheByKey(key, subChecks=False)
        return float(result.values) if result is not None else None

    results = {label: get_metric(mid) for label, mid in REPORTED.items()}
    for label, value in results.items():
        # A metric is None when its task failed; the reason is in the log above.
        print(f"{label + ':':<13}{value:.4f}" if value is not None else f"{label + ':':<13}FAILED")

    # Save session for later use in the GUI
    # Creates a directory at the given path containing:
    #   info.json      - dataset/model metadata
    #   cache/*.npz    - all computed data (errors, distributions, metrics)
    # Load it in the GUI via File > Load (Ctrl+l).
    env.persistence.save(savePath)
    print(f"\nSession saved to: {savePath}")

    # Clean up
    env.headlessQuit()

    return 0 if all(v is not None for v in results.values()) else 1


# Metric workers are spawned as separate processes, which re-import this file.
# Without this guard the re-import re-runs the script body and multiprocessing
# refuses to start the worker at all.
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", default=DATASET)
    parser.add_argument("prediction", nargs="?", default=PREDICTION)
    parser.add_argument("--results", default="results", help="session output directory")
    parser.add_argument("--energy-key", default=None, help="energy key, when the file has several")
    parser.add_argument("--force-key", default=None, help="force key, when the file has several")
    args = parser.parse_args()
    sys.exit(main(args.dataset, args.prediction, args.results,
                  args.energy_key, args.force_key))
