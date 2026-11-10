"""Check the LAMMPS a job script loads, on the FFAST server's node ("Run with FFAST").

Plan P8–P10 (``docs/implementation/2026-11-06-run-with-ffast.md``). MeluXina's
login node has no ``module`` command, so the check runs where the server runs:

1. ``bash -lc``: purge modules, run the job script's ``module`` lines in
   order, then ``<exe> -h`` → version and the installed pair styles.
2. The same environment runs a small input that uses every command FFAST
   adds: the start snapshot (``write_dump``), ``reset_timestep`` after it, the
   include file (dump with time/units/format, ``is_file()``, ``fix halt``), a
   short ``run``, then ``reset_timestep`` after the dump wrote and one more
   ``run``. ``print`` markers tell which stage LAMMPS reached before an error.
3. An MPI build that will not start as a single process outside ``srun`` is
   retried as ``srun --overlap -n 1 <exe>``.

Results are cached per (module lines, executable) for the server's lifetime.
The command runner is passed in, so tests need no LAMMPS.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ffast.live.include_file import include_text
from ffast.live.lammps_input import Check, snapshot_lines

TIMEOUT_S = 90
STAGES = ("snapshot", "reset_after_snapshot", "include", "run", "reset_after_dump", "run_again", "done")
VERSION_RE = re.compile(r"(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})")
MPI_HINTS = ("PMI", "MPI_Init", "pmix", "PMIx", "srun", "mpirun")

Runner = Callable[[str, Path, float], "tuple[int, str]"]   # (bash script, cwd, timeout) -> (rc, output)


@dataclass
class LammpsCheck:
    module: str | None
    module_lines: list[str]
    executable: str
    found: bool = False
    version: str | None = None
    pair_styles: set[str] = field(default_factory=set)
    reached: str | None = None        # last stage passed
    error: str | None = None          # LAMMPS's ERROR line, or the shell's last lines
    launched_with_srun: bool = False

    @property
    def ffast_commands_ok(self) -> bool:
        return self.reached is not None and STAGES.index(self.reached) >= STAGES.index("run")

    @property
    def snapshot_ok(self) -> bool:
        return self.reached is not None and STAGES.index(self.reached) >= STAGES.index("reset_after_snapshot")

    @property
    def reset_after_dump_ok(self) -> bool:
        return self.reached == "done"

    def checks(
        self, *, needs_snapshot: bool, local: bool = False
    ) -> tuple[list[Check], str | None]:
        """Lines for the dialog, and a refusal reason when FFAST cannot run on this build."""
        out = []
        if self.module:
            out.append(Check("ok", f"job script loads {self.module}"))
        elif not local:
            out.append(Check("warn", f"no LAMMPS module found in the job script; checked the "
                                     f"`{self.executable}` on the server's PATH"))
        if not self.found:
            return out, (
                f"`{self.executable}` does not start"
                + (
                    " on this computer"
                    if local
                    else " after the job script's module lines"
                )
                + (f": {self.error}" if self.error else "")
            )
        how = " (through srun)" if self.launched_with_srun else ""
        out.append(
            Check(
                "ok",
                f"`{self.executable}` starts {'on this computer' if local else 'on the server node'}{how}; "
                f"version {self.version or 'unknown'}",
            )
        )
        if needs_snapshot and not self.snapshot_ok:
            return out, f"this LAMMPS refuses the start snapshot (write_dump): {self.error}"
        if not self.ffast_commands_ok:
            return out, f"this LAMMPS cannot run FFAST's include: {self.error}"
        out.append(Check("ok", "test input with FFAST's snapshot, dump, STOP check and fix halt: passed"))
        if self.reset_after_dump_ok:
            out.append(Check("ok", "reset_timestep after a dump has written: allowed"))
        else:
            out.append(Check("warn", f"reset_timestep after a dump has written: refused by this build ({self.error})"))
        return out, None


def check_pair_styles(styles: list[str], hybrid: bool, plugins: bool, check: LammpsCheck) -> tuple[list[Check], str | None]:
    """Is the input's pair style installed (P10)?"""
    if plugins:
        return [Check("warn", "the input loads a plugin; its pair style is not checked")], None
    if not styles:
        return [Check("warn", "the input sets no pair_style")], None
    if not check.pair_styles:
        return [Check("warn", "the pair-style list of this LAMMPS could not be read")], None
    known = [s for s in styles if s in check.pair_styles]
    if hybrid:
        if not known:
            return [], f"none of the hybrid pair styles ({' '.join(styles)}) is installed in this LAMMPS"
        return [Check("ok", f"pair styles {' '.join(known)} installed (hybrid sub-styles)")], None
    missing = [s for s in styles if s not in check.pair_styles]
    if missing:
        return [], f"pair_style {' '.join(missing)} used by the input is not installed in this LAMMPS"
    return [Check("ok", f"pair_style {' '.join(styles)} used by the input is installed")], None


# ── parsing ─────────────────────────────────────────────────────────────────

def parse_help(text: str) -> tuple[str | None, set[str]]:
    """(version, pair styles) from ``lmp -h``."""
    version = None
    for line in text.splitlines():
        if "Large-scale Atomic/Molecular Massively Parallel Simulator" in line:
            m = VERSION_RE.search(line)
            version = m.group(1) if m else line.strip()
            break
    styles: set[str] = set()
    inside = False
    for line in text.splitlines():
        if line.startswith("* "):
            inside = line.strip().lower().startswith("* pair styles")
            continue
        if inside:
            styles.update(w for w in line.split() if not w.startswith("("))
    return version, styles


def parse_run(output: str) -> tuple[str | None, str | None]:
    """(last stage reached, LAMMPS's ERROR line) from the test run's output."""
    reached = None
    error = None
    for line in output.splitlines():
        m = re.match(r"^FFAST-CHECK (\w+)", line.strip())
        if m and m.group(1) in STAGES:
            reached = m.group(1)
        if line.startswith("ERROR") and error is None:
            error = line.strip()
    return reached, error


def check_input(folder: Path) -> str:
    """A two-atom input using every command Run with FFAST adds."""
    include = folder / "ffast_check.lmp"
    include.write_text(include_text(str(folder / "none.bundle"), ["H"], every=1))
    lines = [
        "units metal", "atom_style atomic", "boundary f f f",
        "region box block -5 5 -5 5 -5 5", "create_box 1 box",
        "create_atoms 1 single 0 0 0", "create_atoms 1 single 1.5 0 0",
        "mass 1 1.008", "pair_style zero 3.0", "pair_coeff * *",
        "variable ffast_on index 1", f"variable ffast_dir index {folder}",
        *snapshot_lines(["H"]),
        'print "FFAST-CHECK snapshot"',
        "reset_timestep 0",
        'print "FFAST-CHECK reset_after_snapshot"',
        f"include {include}",
        'print "FFAST-CHECK include"',
        "fix ffast_nve all nve", "run 2",
        'print "FFAST-CHECK run"',
        "reset_timestep 0",
        'print "FFAST-CHECK reset_after_dump"',
        "run 2",
        'print "FFAST-CHECK run_again"',
        'print "FFAST-CHECK done"',
    ]
    return "\n".join(lines) + "\n"


# ── running ─────────────────────────────────────────────────────────────────

def _subprocess_runner(script: str, cwd: Path, timeout: float) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    try:
        p = subprocess.run(["bash", "-lc", script], cwd=cwd, env=env, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f} s"
    return p.returncode, p.stdout.decode(errors="replace")


_CACHE: dict[tuple, LammpsCheck] = {}


def _scratch() -> Path:
    base = Path(tempfile.gettempdir())
    if any(ch.isspace() for ch in str(base)):
        base = Path("~/.ffast/tmp").expanduser()
        base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="ffast-lmp-check-", dir=base))


def check_lammps(
    module_lines: list[str],
    module: str | None,
    executable: str,
    *,
    runner: Runner | None = None,
    use_cache: bool = True,
    local: bool = False,
) -> LammpsCheck:
    """Load the job's modules and try FFAST's commands in that LAMMPS (P9)."""
    key = (tuple(module_lines), executable, local)
    if use_cache and key in _CACHE:
        return _CACHE[key]
    runner = runner or _subprocess_runner
    result = LammpsCheck(module, list(module_lines), executable)
    folder = _scratch()
    prelude = (
        ""
        if local
        else "\n".join(
            ["module --force purge >/dev/null 2>&1 || true", *module_lines]
        )
    )
    exe = (
        executable
        if not local and "$" in executable
        else shlex.quote(executable)
    )

    rc, out = runner(f"{prelude}\n{exe} -h", folder, TIMEOUT_S)
    if rc != 0 or "Large-scale Atomic/Molecular" not in out:
        result.error = _tail(out)
        return result            # not cached: a module system hiccup should be retried
    result.found = True
    result.version, result.pair_styles = parse_help(out)

    (folder / "test.in").write_text(check_input(folder))
    run = f"{exe} -in test.in -log test.log -echo screen"
    rc, out = runner(f"{prelude}\n{run}", folder, TIMEOUT_S)
    reached, error = parse_run(out)
    if (
        not local
        and reached is None
        and error is None
        and any(h in out for h in MPI_HINTS)
    ):
        rc, out = runner(f"{prelude}\nsrun --overlap -n 1 {run}", folder, TIMEOUT_S)
        reached, error = parse_run(out)
        result.launched_with_srun = True
    result.reached = reached
    result.error = error or (None if reached == "done" else _tail(out))
    if use_cache:
        _CACHE[key] = result
    return result


def _tail(text: str, n: int = 3) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return " / ".join(lines[-n:]) if lines else "no output"
