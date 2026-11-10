"""Find and check single-process LAMMPS builds on the server machine."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ffast.live.lammps_check import MPI_HINTS, check_lammps


def checked_builds(executable=None, *, runner=None, environ=None):
    """PATH candidates (or one chosen path), each with its actual check result."""
    env = os.environ if environ is None else environ
    paths = []
    if executable:
        path = shutil.which(
            os.path.expanduser(executable), path=env.get("PATH", "")
        )
        paths.append(path or os.path.expanduser(executable))
    else:
        for directory in env.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            try:
                candidates = sorted(Path(directory).glob("lmp*"))
            except OSError:
                continue
            for p in candidates:
                if (
                    (p.name == "lmp" or p.name.startswith("lmp_"))
                    and p.is_file()
                    and os.access(p, os.X_OK)
                ):
                    paths.append(str(p.absolute()))
    rows, seen = [], set()
    for path in paths:
        resolved = str(Path(path).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        result = check_lammps([], None, path, runner=runner, local=True)
        _, why = result.checks(needs_snapshot=True, local=True)
        if why and any(h in (result.error or "") for h in MPI_HINTS):
            why = "starts only under mpirun: one process only for now; choose a build that starts directly"
        rows.append(
            {
                "executable": path,
                "ok": why is None,
                "version": result.version,
                "reason": why
                or "FFAST snapshot, dump, STOP check and fix halt: passed",
            }
        )
    return rows
