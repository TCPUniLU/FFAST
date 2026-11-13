# Scripting and configuration

[README](../README.md) · [Documentation](README.md) · [Using FFAST](usage.md)

## Scripting without a UI

For batch work, drive the Environment directly. A complete example is in
[examples/headless/headless.py](../examples/headless/headless.py):

```python
from ffast.core.environment import startHeadlessEnvironment

env = startHeadlessEnvironment()
env.taskLoadDataset("examples/data/variable-sized molecular/dataset.xyz", "ase (auto)")
env.waitForTasks(verbose=True)

dataset = env.getDatasetFromPath("examples/data/variable-sized molecular/dataset.xyz")
env.loadPrepredictedDataset("examples/data/variable-sized molecular/prediction.xyz", dataset.fingerprint)
model = env.models.all()[0]

energy_mae = "ffast.energy_difference[dataset;mae]"    # a keep-box setting
key = env.data.make_metric_cache_key(energy_mae, {}, model, dataset)
env.data.taskGenerateMetric(energy_mae, {}, model, dataset, key)
env.waitForTasks(verbose=True)
print(float(env.data.getCacheByKey(key, subChecks=False).values))

env.persistence.save("results")     # opens later in the UI
env.headlessQuit()
```

`waitForTasks()` returns as soon as the work has settled, so a script does not
lose seconds to a fixed sleep. To stop a script from hanging on work that never
ends, pass `stall_timeout_s=60`: it raises `TimeoutError` naming the unfinished
work once nothing has progressed for that many seconds (off by default). The
old `dt` argument is ignored and gives a deprecation warning.

For a single number, don't write a script; `ffast-cli metrics run` already does
it.

## Configuration files

| File | Holds |
|------|-------|
| `ffast/config/default.json` | App defaults: colours, units, bond detection |
| `ffast/config/user.json` | Your overrides of the above (create it; same keys) |
| `ffast/chemistry/atoms.py` | Element data: colours, covalent radii, names |
| `config/clusters.json` | Saved cluster profiles |
| `ffast.toml` | Project config: metrics, dataset fields, tabs |

Settings in `default.json` you are most likely to want:

| Key | Default | Meaning |
|-----|---------|---------|
| `bondsLenience` | 1.1 | Multiplier on the bond detection distance (was `loupeBondsLenience`, still read) |
