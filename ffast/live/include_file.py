"""The generic LAMMPS include file ``ffast bundle`` writes next to a bundle (ADR 0086).

The user adds ``include /abs/path/<name>.lmp`` once to an input script, after
the atoms exist. Run through ``ffast-lammps`` (which passes ``-var ffast_on 1
-var ffast_dir <run folder>``), it tells the scorer which bundle to use, sets
up the dump and the STOP check. Run without the wrapper, it prints that
monitoring is off and skips the rest with ``jump SELF``. LAMMPS ``if``
compares numbers only, hence the numeric switch.
"""

from __future__ import annotations

INCLUDE_FORMAT_VERSION = 1


DEFAULT_PERSIST_PS = 5.0


def include_text(bundle_path: str, elements: list[str], every: int = 100,
                 persist_ps: float = DEFAULT_PERSIST_PS) -> str:
    """The include file for ``bundle_path``; ``elements[i]`` is LAMMPS type ``i + 1``."""
    if any(ch.isspace() for ch in bundle_path):
        raise ValueError(f"bundle path {bundle_path!r} contains a space; LAMMPS cannot read it")
    if every < 1:
        raise ValueError("the dump interval must be at least 1 step")
    if not persist_ps > 0:
        raise ValueError("the persistence time must be positive")
    el = " ".join(elements)
    return f"""\
# Written by `ffast bundle`. Reference bundle: {bundle_path}
# Add once to a LAMMPS input, after the atoms exist:   include <this file>
# Run the job as `ffast-lammps srun lmp ...`; the wrapper sets ffast_on and ffast_dir.
# LAMMPS atom types 1..{len(elements)} are: {el}
# To change a setting for one input, define it before the include line
# (LAMMPS keeps the first definition of an index variable), e.g.
#   variable ffast_stop_on_alarm index 1
variable ffast_on    index 0
variable ffast_every index {int(every)}
variable ffast_persist_ps    index {persist_ps:g}   # an alarm needs this long a persisting anomaly
variable ffast_stop_on_alarm index 0   # 1: stop LAMMPS when an alarm fires
if "${{ffast_on}} == 0" then "print 'FFAST: not run through ffast-lammps; monitoring off'" "jump SELF ffast_end"
print \"\"\"ffast-include {INCLUDE_FORMAT_VERSION}
bundle {bundle_path}
every ${{ffast_every}}
persist_ps ${{ffast_persist_ps}}
stop_on_alarm ${{ffast_stop_on_alarm}}
elements {el}
end\"\"\" file ${{ffast_dir}}/include.txt screen no
dump        ffast all custom ${{ffast_every}} ${{ffast_dir}}/frames.lammpstrj id type element x y z
dump_modify ffast sort id flush yes element {el} time yes units yes format float %.17g
variable    ffast_stop equal is_file(${{ffast_dir}}/STOP)
fix         ffast_halt all halt ${{ffast_every}} v_ffast_stop != 0 error soft
label       ffast_end
"""
