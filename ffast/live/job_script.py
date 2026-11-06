"""Wrap the LAMMPS launch in a copy of a Slurm job script ("Run with FFAST", ADR 0086).

Finds the one command that starts LAMMPS (``srun lmp -in run.in``,
``mpirun -np 4 lmp_mpi -in run.in``, ``lmp < run.in``; ``\\`` continuations
joined), checks it reads the picked input, and writes the copy with
``$HOME/.ffast/bin/ffast-lammps`` in front of it and the input copy's name in
place of the input. Also lists the job's ``module`` lines and the LAMMPS module
among them, for the module check (plan P7, P8).

Edits are made on the raw text, never by re-quoting: ``$VARIABLES`` and quotes
in the job script stay exactly as written. Pure; standard library only.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

from ffast.live.lammps_input import Refusal

WRAPPER = "$HOME/.ffast/bin/ffast-lammps"
LAUNCHERS = ("srun", "mpirun", "mpiexec", "ibrun")
SEPARATORS = ("&&", "||", ";", "|", "&")
LMP_RE = re.compile(r"(?:(?<=\s)|^)(?:[^\s;&|]*/)?(?:lmp[^\s;&|]*|lammps)(?=\s|$)")
MODULE_RE = re.compile(r"^\s*(?:module\s+(load|add|purge|use|unload|swap|restore)\b|ml\b)(.*)$")


@dataclass
class Logical:
    first: int     # physical lines, 1-based
    last: int
    text: str      # continuations joined with one space


@dataclass
class Launch:
    first: int
    last: int
    text: str                      # the launch's logical line, as written
    executable: str                # e.g. "lmp", "/opt/lammps/bin/lmp_mpi"
    input_token: str               # as written after -in / -i / <
    input_path: str                # resolved against the job script's folder
    wrapped: bool                  # already runs through ffast-lammps
    launcher: str | None = None    # srun / mpirun / … or None
    warnings: list[str] = field(default_factory=list)


def logical_lines(text: str) -> list[Logical]:
    out, pending, first = [], "", None
    for number, line in enumerate(text.splitlines(), start=1):
        if first is None:
            first = number
        if line.rstrip().endswith("\\") and not line.lstrip().startswith("#"):
            pending += line.rstrip()[:-1] + " "
            continue
        out.append(Logical(first, number, pending + line))
        pending, first = "", None
    if pending:
        out.append(Logical(first, number, pending))
    return out


def _tokens(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    lex.commenters = "#"
    try:
        return list(lex)
    except ValueError:            # unbalanced quotes: let the caller refuse
        return []


def _segment(tokens: list[str], index: int) -> list[str]:
    """The simple command around ``tokens[index]`` (between shell separators)."""
    start = index
    while start > 0 and tokens[start - 1] not in SEPARATORS:
        start -= 1
    end = index
    while end < len(tokens) and tokens[end] not in SEPARATORS:
        end += 1
    return tokens[start:end]


def _is_lmp(token: str) -> bool:
    name = os.path.basename(token)
    return name.startswith("lmp") or name == "lammps"


PREFIXES = ("time", "exec", "env", "nohup", "command")


def _starts_command(tokens: list[str], index: int) -> bool:
    """True when ``tokens[index]`` is what its simple command runs: the command
    word itself, or the program of a launcher (``srun … lmp``), after optional
    ``VAR=value``/``time``/``exec`` prefixes. ``echo lammps`` is not a launch."""
    seg = _segment(tokens, index)
    k = seg.index(tokens[index])
    words = [w for w in seg[:k] if not re.match(r"^[A-Za-z_]\w*=", w) and w not in PREFIXES]
    if not words:
        return True
    return os.path.basename(words[0]) in LAUNCHERS or "ffast-lammps" in words[0]


def find_launch(text: str, input_path: str, job_dir: str) -> Launch | Refusal:
    """The one command of ``text`` that starts LAMMPS on ``input_path``, or why not."""
    hits = []
    for logical in logical_lines(text):
        if logical.text.lstrip().startswith("#"):
            continue
        tokens = _tokens(logical.text)
        for i, tok in enumerate(tokens):
            if _is_lmp(tok) and _starts_command(tokens, i):
                hits.append((logical, tokens, i))
                break
    if not hits:
        return Refusal("no command starts LAMMPS (an `lmp…` executable)", None,
                       f"run LAMMPS as `{WRAPPER} srun lmp -in <input>` in the job script")
    if len(hits) > 1:
        return Refusal(f"LAMMPS is started {len(hits)} times (lines "
                       f"{', '.join(str(h[0].first) for h in hits)}); FFAST wraps exactly one launch",
                       hits[1][0].first, f"put `{WRAPPER}` in front of the launch to be watched")
    logical, tokens, i = hits[0]
    seg = _segment(tokens, i)
    k = seg.index(tokens[i])
    lmp_args = seg[k + 1:]
    for bad in ("-partition", "-p"):
        if bad in lmp_args:
            return Refusal(f"`lmp {bad}` runs several LAMMPS partitions; FFAST watches one", logical.first,
                           "run without -partition")
    token = None
    for j, arg in enumerate(lmp_args):
        if arg in ("-in", "-i") and j + 1 < len(lmp_args):
            token = lmp_args[j + 1]
            break
        if arg == "<" and j + 1 < len(lmp_args):
            token = lmp_args[j + 1]
            break
        if arg.startswith("<") and len(arg) > 1:
            token = arg[1:]
            break
    if token is None:
        # `<` is not punctuation for shlex here; look in the raw text.
        m = re.search(r"<\s*(\S+)", logical.text[logical.text.find(tokens[i]):])
        token = m.group(1) if m else None
    if token is None:
        return Refusal("the LAMMPS command names no input file (`-in FILE` or `< FILE`)", logical.first,
                       f"name the input in the job script: `{WRAPPER} srun lmp -in <input>`")
    if "$" in token:
        return Refusal(f"the input file is named through a variable ({token}); FFAST cannot tell which file runs",
                       logical.first, "write the input file name out in the launch line")
    resolved = os.path.normpath(token if os.path.isabs(token) else os.path.join(job_dir, token))
    if resolved != os.path.normpath(input_path):
        return Refusal(f"the job script runs {token}, not the picked input {input_path}", logical.first,
                       "pick the input the job script runs, or change the job script")
    launcher = next((t for t in seg[:k] if os.path.basename(t) in LAUNCHERS), None)
    launch = Launch(logical.first, logical.last, logical.text, tokens[i], token, resolved,
                    wrapped=any("ffast-lammps" in t for t in seg[:k]), launcher=launcher)
    if any(re.match(r"^\s*cd\s+(?!\$SLURM_SUBMIT_DIR|\"\$SLURM_SUBMIT_DIR\"|\$\{SLURM_SUBMIT_DIR\})", ln.text)
           for ln in logical_lines(text) if ln.first < logical.first) and not os.path.isabs(token):
        launch.warnings.append("the job script changes folder (`cd`) before LAMMPS starts; "
                               f"{token} was taken to be in the job script's folder")
    return launch


def copy_name(name: str) -> str:
    """``run.in`` → ``run.ffast.in``; ``in.aspirin`` → ``in.ffast.aspirin``; ``job`` → ``job.ffast``."""
    head, base = os.path.split(name)
    stem, dot, suffix = base.partition(".")
    new = f"{stem}.ffast.{suffix}" if dot else f"{base}.ffast"
    return os.path.join(head, new) if head else new


def wrap(text: str, launch: Launch, input_copy: str, marker: str) -> str:
    """The job-script copy: marker after the shebang, the launch wrapped, the input renamed."""
    lines = text.splitlines()
    line = launch.text
    start = line.find(launch.executable)
    if not launch.wrapped:
        before = line[:start]
        if launch.launcher:
            at = before.rfind(launch.launcher)
            # `srun` may sit after `exec`, `time` or VAR=value prefixes; wrap from it.
            line = line[:at] + WRAPPER + " " + line[at:]
        else:
            line = line[:start] + WRAPPER + " " + line[start:]
        start = line.find(launch.executable)
    tail = line[start:]
    tail, n = re.subn(r"((?:-in|-i)\s+|<\s*)([\"']?)" + re.escape(launch.input_token) + r"\2(?=\s|$)",
                      lambda m: m.group(1) + m.group(2) + input_copy + m.group(2), tail, count=1)
    if n != 1:
        raise ValueError(f"could not find the input {launch.input_token!r} in the launch line")
    line = line[:start] + tail
    indent = re.match(r"\s*", lines[launch.first - 1]).group(0)
    new = lines[:launch.first - 1] + [indent + line.strip()] + lines[launch.last:]
    at = 1 if new and new[0].startswith("#!") else 0
    new.insert(at, marker)
    return "\n".join(new) + "\n"


def module_lines(text: str) -> list[str]:
    """The job's ``module …`` / ``ml …`` lines, in order, as written."""
    return [ln.text.strip() for ln in logical_lines(text)
            if MODULE_RE.match(ln.text) and not ln.text.lstrip().startswith("#")]


def lammps_module(lines: list[str]) -> str | None:
    """The first loaded module whose name contains ``lammps`` (any case)."""
    for line in lines:
        m = MODULE_RE.match(line)
        if not m:
            continue
        verb, rest = m.group(1), m.group(2).split()
        if verb is None and rest and rest[0] in ("purge", "use", "unload", "save", "restore", "list", "spider"):
            continue
        if verb in (None, "load", "add"):
            for name in rest:
                if "lammps" in name.lower():
                    return name
    return None
