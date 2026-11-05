"""The live-run setup the bootstrap job runs on a cluster node (ADR 0086)."""

import json
import os
import subprocess
import sys

from ffast.live.node_setup import setup_node


def test_setup_writes_the_settings_and_a_wrapper_that_runs_without_ffast(tmp_path):
    bin_dir = setup_node(tmp_path / ".ffast", "~/.ffast/venv", ["env/staging/2024.1", "Python/3.11.10"])
    settings = json.loads((tmp_path / ".ffast" / "live.json").read_text())
    assert settings == {"venv": "~/.ffast/venv", "modules": ["env/staging/2024.1", "Python/3.11.10"]}
    launcher = bin_dir / "ffast-lammps"
    assert os.access(launcher, os.X_OK)
    assert (bin_dir / "run_folder.py").is_file() and (bin_dir / "ffast_lammps.py").is_file()
    assert (bin_dir / "lammps_log.py").is_file()
    # Without site-packages (-S) the ffast package is out of reach: the copy
    # must import its siblings run_folder.py and lammps_log.py.
    p = subprocess.run([sys.executable, "-I", "-S", str(bin_dir / "ffast_lammps.py"), "--help"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 0 and "usage: ffast-lammps" in p.stderr
    p = subprocess.run([str(launcher), "srun", "lmp", "-partition", "2x2"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 2 and "multi-partition" in p.stderr
