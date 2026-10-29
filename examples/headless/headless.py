import os
import sys
from pathlib import Path

# Set working directory and Python path to the FFAST project root
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from ffast.core.environment import startHeadlessEnvironment

# Initialize headless environment
env = startHeadlessEnvironment()

# Load dataset (use "sGDML" for .npz or "ase (auto)" for ASE formats)
env.taskLoadDataset("examples/data/dataset.xyz", "ase (auto)")
env.waitForTasks(verbose=True)

# Get the loaded dataset and its fingerprint
dataset = env.getDatasetFromPath("examples/data/dataset.xyz")

# Load pre-computed predictions (ASE file with energies and forces)
env.loadPrepredictedDataset("examples/data/prediction.xyz", dataset.fingerprint)

# Get the model created from the predictions (ghost model)
model = env.models.all()[0]

# Queue metric computations
# A base metric plus its keep-box setting names each error (ADR 0065):
# [kept boxes;combine;statistic], "dataset" when nothing is kept.
ENERGY_MAE = "ffast.energy_difference[dataset;mae]"
ENERGY_RMSE = "ffast.energy_difference[dataset;rmse]"
FORCE_MAE = "ffast.force_difference[dataset;component;mae]"
FORCE_RMSE = "ffast.force_difference[dataset;component;rmse]"
metrics = [
    (ENERGY_MAE, {}),
    (ENERGY_RMSE, {}),
    ("ffast.energy_difference_shifted[dataset;mae]", {}),
    ("ffast.energy_difference_shifted[dataset;rmse]", {}),
    (FORCE_MAE, {}),
    (FORCE_RMSE, {}),
    ("ffast.energy_difference", {}),
    ("ffast.force_difference[atom+structure;component;mae]", {}),
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

e_mae   = get_metric(ENERGY_MAE)
e_rmse  = get_metric(ENERGY_RMSE)
f_mae   = get_metric(FORCE_MAE)
f_rmse  = get_metric(FORCE_RMSE)

print(f"Energy MAE:  {e_mae:.4f}")
print(f"Energy RMSE: {e_rmse:.4f}")
print(f"Force MAE:   {f_mae:.4f}")
print(f"Force RMSE:  {f_rmse:.4f}")

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
