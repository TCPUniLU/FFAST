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


def main():
    # Initialize headless environment
    env = startHeadlessEnvironment()

    # Load dataset (use "sGDML" for .npz or "ase (auto)" for ASE formats)
    env.taskLoadDataset(DATASET, "ase (auto)")
    env.waitForTasks(verbose=True)

    # Get the loaded dataset and its fingerprint
    dataset = env.getDatasetFromPath(DATASET)

    # Load pre-computed predictions (ASE file with energies and forces)
    env.loadPrepredictedDataset(PREDICTION, dataset.fingerprint)

    # Get the model created from the predictions (ghost model)
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

    results = {
        "Energy MAE":  get_metric("ffast.energy_mae"),
        "Energy RMSE": get_metric("ffast.energy_rmse"),
        "Force MAE":   get_metric("ffast.force_mae_global"),
        "Force RMSE":  get_metric("ffast.force_rmse_global"),
    }
    for label, value in results.items():
        # A metric is None when its task failed; the reason is in the log above.
        print(f"{label + ':':<13}{value:.4f}" if value is not None else f"{label + ':':<13}FAILED")

    # Save session for later use in the GUI
    # Creates a directory at the given path containing:
    #   info.json      - dataset/model metadata
    #   cache/*.npz    - all computed data (errors, distributions, metrics)
    # Load it in the GUI via File > Load (Ctrl+l).
    savePath = os.path.join(PROJECT_ROOT, "results")
    env.persistence.save(savePath)
    print(f"\nSession saved to: {savePath}")

    # Clean up
    env.headlessQuit()

    return 0 if all(v is not None for v in results.values()) else 1


# Metric workers are spawned as separate processes, which re-import this file.
# Without this guard the re-import re-runs the script body and multiprocessing
# refuses to start the worker at all.
if __name__ == "__main__":
    sys.exit(main())
