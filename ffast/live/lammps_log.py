"""Follow ``log.lammps`` of a live run for what FFAST needs from it (ADR 0086).

- ``run`` commands → the planned length of the segment.
- ``a by b by c MPI processor grid`` → how many processes run LAMMPS.
- ``Fix halt condition for fix-id ffast_halt met on step X`` → stop observed.
- ``restart`` / ``write_restart`` before the first ``run`` → the run can be continued.

LAMMPS echoes each input line as written and, when it held variables, once more
after substitution (``run ${nsteps}`` then ``run 20000``; both MeluXina builds).
So a ``run`` line still holding ``$`` is skipped: its substituted echo follows.

Standard library only, Python 3.6 compatible: the wrapper imports it.
"""

import os
import re

RUN_RE = re.compile(r"^\s*run\s+(\S+)(.*)$")
GRID_RE = re.compile(r"^\s*(\d+)\s+by\s+(\d+)\s+by\s+(\d+)\s+MPI processor grid")
HALT_RE = re.compile(r"Fix halt condition for fix-id (\S+) met on step (\d+)")
RESTART_RE = re.compile(r"^\s*(restart|write_restart)\s")
MAX_READ = 1 << 20


class PlannedLength(object):
    """The steps a segment is planned to run, from its ``run`` commands.

    ``run N`` adds N steps after the previous run's end; ``run N upto`` runs
    until step N; ``start``/``stop``/``pre``/``post``/``every`` keywords leave
    N as the step count. A ``run`` whose N is not a plain integer makes the
    length unknown (``unknown`` names the line).
    """

    def __init__(self):
        self.ops = []          # ("add", n) or ("upto", n)
        self.unknown = None

    def feed(self, line):
        """Take one echoed input line; return True if the plan changed."""
        m = RUN_RE.match(line)
        if not m or "$" in line:
            return False
        n_text, rest = m.group(1), m.group(2).split()
        try:
            n = int(n_text)
        except ValueError:
            if self.unknown is None:
                self.unknown = line.strip()
                return True
            return False
        self.ops.append(("upto", n) if "upto" in rest else ("add", n))
        return True

    def end_step(self, first_step):
        """The step the plan ends at, counted from the segment's first step; None if unknown."""
        if self.unknown is not None or not self.ops:
            return None
        end = int(first_step)
        for kind, n in self.ops:
            end = n if kind == "upto" else end + n
        return end


class LogFollower(object):
    """Reads new complete lines of ``log.lammps`` and keeps what FFAST needs."""

    def __init__(self, path, halt_fix="ffast_halt"):
        self.path = str(path)
        self.halt_fix = halt_fix
        self._pos = 0
        self._pending = b""
        self.planned = PlannedLength()
        self.processes = None
        self.halt_step = None
        self.runs_seen = 0
        self.restart_before_run = False

    def poll(self):
        """Read new lines; return the ones that changed something, as (kind, value) pairs.

        Kinds: ``planned`` (run line), ``processes``, ``halt``, ``first_run``
        (value: whether a restart command came before it).
        """
        try:
            with open(self.path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                if size < self._pos:          # replaced: start over
                    self.__init__(self.path, self.halt_fix)
                fh.seek(self._pos)
                data = fh.read(MAX_READ)
        except (IOError, OSError):
            return []
        self._pos += len(data)
        data = self._pending + data
        cut = data.rfind(b"\n") + 1
        self._pending = data[cut:]
        changes = []
        for raw in data[:cut].splitlines():
            changes += self.feed(raw.decode("utf-8", "replace"))
        return changes

    def feed(self, line):
        out = []
        if RESTART_RE.match(line) and not self.runs_seen:
            self.restart_before_run = True
        if RUN_RE.match(line):
            if "$" not in line:
                if not self.runs_seen:
                    out.append(("first_run", self.restart_before_run))
                self.runs_seen += 1
            if self.planned.feed(line):
                out.append(("planned", line.strip()))
            return out
        m = GRID_RE.match(line)
        if m and self.processes is None:
            a, b, c = (int(x) for x in m.groups())
            self.processes = a * b * c
            out.append(("processes", self.processes))
            return out
        m = HALT_RE.search(line)
        if m and m.group(1) == self.halt_fix and self.halt_step is None:
            self.halt_step = int(m.group(2))
            out.append(("halt", self.halt_step))
        return out
