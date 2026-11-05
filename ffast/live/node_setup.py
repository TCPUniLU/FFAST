"""Set a cluster node up for live LAMMPS runs (ADR 0086); run by the bootstrap job.

Writes ``~/.ffast/live.json`` (the modules and venv ``ffast-lammps`` starts the
scorer with) and puts the wrapper in ``~/.ffast/bin``: ``ffast-lammps`` is a
small shell script running ``ffast_lammps.py`` with the job's own ``python3``,
so a job script needs neither the venv nor FFAST's modules::

    ~/.ffast/bin/ffast-lammps srun lmp -in run.in
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from ffast.live import run_folder as rf

LAUNCHER = """#!/bin/sh
# Written by the FFAST cluster bootstrap. Runs LAMMPS with the FFAST scorer beside it.
exec python3 -I "{script}" "$@"
"""


def setup_node(home: Path, venv: str, modules: list[str]) -> Path:
    """Write live.json and the wrapper under ``home`` (normally ``~/.ffast``); return the bin dir."""
    home = Path(home).expanduser()
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    shutil.copyfile(here / "wrapper.py", bin_dir / "ffast_lammps.py")
    shutil.copyfile(here / "run_folder.py", bin_dir / "run_folder.py")
    shutil.copyfile(here / "lammps_log.py", bin_dir / "lammps_log.py")
    launcher = bin_dir / "ffast-lammps"
    launcher.write_text(LAUNCHER.format(script=bin_dir / "ffast_lammps.py"))
    os.chmod(launcher, 0o755)
    rf.write_json_atomic(home / "live.json", {"venv": venv, "modules": list(modules)})
    return bin_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ffast.live.node_setup")
    p.add_argument("--home", default="~/.ffast")
    p.add_argument("--venv", required=True)
    p.add_argument("--module", action="append", default=[])
    a = p.parse_args(argv)
    bin_dir = setup_node(Path(a.home), a.venv, a.module)
    print(f"ffast-bootstrap live LAMMPS wrapper: {bin_dir / 'ffast-lammps'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
