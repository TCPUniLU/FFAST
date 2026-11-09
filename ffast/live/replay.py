"""Replay a recorded LAMMPS dump through the real wrapper and scorer.

Standard library only. Accepts LAMMPS's -var and -log switches; GO releases
an optional gate and STOP halts even while waiting at that gate (ADR 0092).
"""

import argparse
import re
import time
from pathlib import Path


def frames(path):
    """Yield (step, whole frame), including optional UNITS and TIME headers."""
    # Frames can start with UNITS, TIME, or TIMESTEP, depending on the dump.
    text = Path(path).read_text()
    pattern = (
        r"((?:ITEM: UNITS\n[^\n]*\n)?(?:ITEM: TIME\n[^\n]*\n)?ITEM: TIMESTEP\n(\d+)\n.*?)"
        r"(?=(?:ITEM: UNITS|ITEM: TIME|ITEM: TIMESTEP)\n|\Z)"
    )
    for match in re.finditer(pattern, text, re.S):
        yield int(match[2]), match[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sleep", type=float, default=0.3)
    parser.add_argument("--gate-step", type=int)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("-var", action="append", nargs=2, default=[])
    parser.add_argument("-log", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.sleep < 0:
        parser.error("--sleep must be nonnegative")
    run_dir = Path(dict(args.var)["ffast_dir"])
    bundle = (args.bundle or args.source.parent / "toy.bundle").resolve()
    info = []
    for line in (args.source / "include.txt").read_text().splitlines():
        if line.startswith("bundle "):
            line = "bundle " + str(bundle)
        elif line.startswith("stop_on_alarm "):
            line = "stop_on_alarm 0"
        info.append(line)
    (run_dir / "include.txt").write_text("\n".join(info) + "\n")
    with (
        args.log.open("w") as log,
        (run_dir / "frames.lammpstrj").open("w") as dump,
    ):

        def say(line):
            log.write(line + "\n")
            log.flush()

        source_log = (args.source / "log.lammps").read_text().splitlines()
        say(source_log[0] + " (replayed by FFAST)")
        say("  1 by 1 by 1 MPI processor grid")
        for line in source_log:
            if re.match(r"^run\s", line):
                say(line)

        def stopped(step):
            if not (run_dir / "STOP").exists():
                return False
            say(
                "WARNING: Fix halt condition for fix-id ffast_halt met on step %d with value 1"
                % step
            )
            return True

        for step, text in frames(args.source / "frames.lammpstrj"):
            if stopped(step):
                break
            dump.write(text)
            dump.flush()
            if step == args.gate_step:
                while not (run_dir / "GO").exists():
                    if stopped(step):
                        return 0
                    time.sleep(0.01)
            time.sleep(args.sleep)
        say("Total wall time: 0:00:00")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
