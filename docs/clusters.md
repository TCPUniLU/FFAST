# Running on a cluster

[README](../README.md) · [Documentation](README.md) · [Using FFAST](usage.md)

FFAST can put the whole compute half on a SLURM node while the UI stays on your
machine. Define a profile in `config/clusters.json`:

```json
{
  "profiles": [
    {
      "name": "MyCluster GPU",
      "host": "login.cluster.edu",
      "username": "myuser",
      "identity_file": "~/.ssh/id_ed25519",
      "ffast_server_cmd": "module load Python/3.11 && source ~/env/ffast_env/bin/activate && ffast-server",
      "partition": "gpu",
      "account": "my_account",
      "qos": "normal",
      "job_name": "ffast",
      "cores": 1,
      "cpus_per_task": 16,
      "ntasks_per_node": 1,
      "gpus_per_task": 1,
      "gpu_count": 0,
      "memory_mb": 8192,
      "time_limit": "00:30:00",
      "snapshot_interval_minutes": 5
    }
  ]
}
```

Key authentication only; there is no password prompt. `ffast_server_cmd` must end
by launching `ffast-server` on the node. `snapshot_interval_minutes` controls how
often the server checkpoints its state so a dropped connection is survivable; 0
turns it off.

Connecting submits the job, waits for it to start, opens an SSH tunnel to the
node and connects. If a job for that profile is already running you are offered
it instead of a second one. If FFAST is not installed on the cluster, it installs
itself: it builds a wheel locally, pushes it over SSH, creates a venv and pip
installs, leaving a checksum marker so it only does this when something changed.

Remote data is loaded by path on the cluster filesystem. For ASE files the server
reads the first frame and lets you choose which keys are energy and forces, and
offers a stride so you can subsample a trajectory that is too long to be useful
whole.

To drive a server you started yourself:

```bash
ffast-server --host 0.0.0.0 --port 8765 --web-port 9000
# then open http://<server-host>:9000/?port=8765
```
