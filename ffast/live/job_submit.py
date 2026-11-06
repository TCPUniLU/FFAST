"""Submit a "Run with FFAST" job script from the FFAST server's node (plan P13).

The other path, from the laptop over SSH, is ``RemoteSlurmBackend.submit_file``
through the web launcher's cluster broker. Both run ``sbatch <script>`` from the
script's own folder, so ``$SLURM_SUBMIT_DIR`` is that folder, as when the user
submits by hand.

The server itself runs inside a Slurm job. Its ``SLURM_*`` and ``SBATCH_*``
variables (memory per CPU, account, partition, …) would be read by ``sbatch``
as defaults for the new job, so they are removed first.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Callable

TIMEOUT_S = 60


class SubmitError(Exception):
    """``sbatch`` failed; the message holds its error output."""


def slurm_free_env(environ: dict) -> dict:
    return {k: v for k, v in environ.items() if not (k.startswith("SLURM_") or k.startswith("SBATCH_"))}


def submit_on_node(folder: str, script: str, *, run: Callable = subprocess.run,
                   environ: dict | None = None) -> str:
    """``sbatch script`` in ``folder`` with the server job's Slurm variables removed; the job id."""
    env = slurm_free_env(dict(os.environ if environ is None else environ))
    try:
        p = run(["sbatch", script], cwd=folder, env=env, timeout=TIMEOUT_S,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise SubmitError("sbatch is not available on the server's node") from exc
    except subprocess.TimeoutExpired as exc:
        raise SubmitError(f"sbatch did not answer within {TIMEOUT_S} s") from exc
    out = p.stdout.decode(errors="replace") if isinstance(p.stdout, bytes) else (p.stdout or "")
    err = p.stderr.decode(errors="replace") if isinstance(p.stderr, bytes) else (p.stderr or "")
    if p.returncode != 0:
        raise SubmitError(f"sbatch failed (exit {p.returncode}): {err.strip() or out.strip()}")
    m = re.search(r"Submitted batch job (\d+)", out)
    if not m:
        raise SubmitError(f"could not read the job id from sbatch: {out.strip()!r}")
    return m.group(1)
