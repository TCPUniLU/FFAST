import os
import sys
from pathlib import Path

# Set working directory and Python path to the FFAST project root
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from client.environment import startHeadlessEnvironment

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

e_mae   = get_metric("ffast.energy_mae")
e_rmse  = get_metric("ffast.energy_rmse")
f_mae   = get_metric("ffast.force_mae_global")
f_rmse  = get_metric("ffast.force_rmse_global")

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
