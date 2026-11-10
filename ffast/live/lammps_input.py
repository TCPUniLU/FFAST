"""Place the FFAST include in a copy of a LAMMPS input ("Run with FFAST", ADR 0086).

Reads a LAMMPS input the way LAMMPS splits it into commands (``&`` joins
lines, ``#`` starts a comment outside quotes, ``\"\"\"`` blocks span lines)
and decides where the FFAST include goes, or why it cannot go anywhere safely.

Rules (``docs/implementation/2026-11-06-run-with-ffast.md`` P1–P6, R2, R7):

- The include goes after the last atom-creating command (``read_data``,
  ``read_restart``, ``create_atoms``) before the first ``run``/``minimize``.
- A ``minimize`` before the first ``run``: a one-off ``write_dump`` snapshot
  goes after the atoms exist and the include goes on the line before the first
  ``run``; the scorer proves atom order from the snapshot.
- Refused: ``clear``, ``jump``/``next``, a second ``read_data``/``read_restart``,
  ``replicate``, ``delete_atoms``, ``rerun``, atoms created after the dump
  starts or where FFAST cannot see them (another ``include``, an ``if``),
  ``minimize`` after the first ``run``, units other than metal/real, no
  ``run``, a ``pair_coeff * *`` element order other than the reference's.

Pure: text in, text out. Standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ATOM_MAKERS = ("read_data", "read_restart", "create_atoms")
ACCEPTED_UNITS = ("metal", "real")
ELEMENT_RE = re.compile(r"^(?:[A-Z][a-z]?|NULL)$")
WRAPPER = "~/.ffast/bin/ffast-lammps"


@dataclass
class Command:
    """One LAMMPS command: name, arguments (quotes removed) and its physical lines (1-based)."""
    name: str
    args: list[str]
    first: int
    last: int
    raw_args: list[str] = field(default_factory=list)   # arguments with their quotes


@dataclass
class Refusal:
    """Why the include cannot be placed, the line at fault and what to edit by hand."""
    reason: str
    line: int | None
    hand_edit: str

    def text(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return f"{where}{self.reason}. Edit by hand: {self.hand_edit}"


@dataclass
class Check:
    status: str          # "ok" | "warn"
    text: str
    line: int | None = None


@dataclass
class InputPlan:
    """The outcome of :func:`plan_input`."""
    refusal: Refusal | None = None
    checks: list[Check] = field(default_factory=list)
    replace: dict[tuple[int, int], list[str]] = field(default_factory=dict)  # (first, last) -> lines
    insert: dict[int, list[str]] = field(default_factory=dict)              # after line L -> lines
    include_after: int | None = None
    snapshot_after: int | None = None
    already_included: bool = False
    pair_styles: list[str] = field(default_factory=list)
    hybrid: bool = False          # pair_styles are candidate words of a hybrid style
    plugins: bool = False

    def apply(self, text: str, header: list[str] = ()) -> str:
        """The edited copy of ``text``; ``header`` lines go first."""
        if self.refusal is not None:
            raise ValueError("a refused input has no copy")
        return apply_edits(text, self.replace, self.insert, header)


# ── reading ─────────────────────────────────────────────────────────────────

def _strip_comment(line: str, in_triple: bool) -> tuple[str, bool]:
    """``line`` without its ``#`` comment; tracks an open ``\"\"\"`` block."""
    out = []
    i, quote = 0, None
    while i < len(line):
        if in_triple:
            j = line.find('"""', i)
            if j < 0:
                out.append(line[i:])
                return "".join(out), True
            out.append(line[i:j + 3])
            i, in_triple = j + 3, False
            continue
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif line.startswith('"""', i):
            out.append('"""')
            i += 3
            in_triple = True
            continue
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out), in_triple


def _split_args(text: str) -> tuple[list[str], list[str]]:
    """Split on whitespace; quoted text (``'``, ``"``, ``\"\"\"``) stays one word."""
    words, raws = [], []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        start = i
        if text.startswith('"""', i):
            j = text.find('"""', i + 3)
            j = n if j < 0 else j
            words.append(text[i + 3:j])
            i = min(n, j + 3)
        elif text[i] in "\"'":
            q = text[i]
            j = text.find(q, i + 1)
            j = n if j < 0 else j
            words.append(text[i + 1:j])
            i = min(n, j + 1)
        else:
            j = i
            while j < n and not text[j].isspace():
                j += 1
            words.append(text[i:j])
            i = j
        raws.append(text[start:i])
    return words, raws


def read_commands(text: str) -> list[Command]:
    """The commands of a LAMMPS input, with the physical lines each spans."""
    commands = []
    pending, first, in_triple = "", None, False
    for number, line in enumerate(text.splitlines(), start=1):
        body, in_triple_after = _strip_comment(line, in_triple)
        if first is None:
            first = number
        if in_triple_after:
            pending += body + "\n"
            in_triple = True
            continue
        in_triple = False
        stripped = body.rstrip()
        if stripped.endswith("&"):
            pending += stripped[:-1] + " "
            continue
        pending += body
        words, raws = _split_args(pending)
        if words:
            commands.append(Command(words[0], words[1:], first, number, raws[1:]))
        pending, first = "", None
    if pending.strip():
        words, raws = _split_args(pending)
        commands.append(Command(words[0], words[1:], first, number, raws[1:]))
    return commands


def _commands_in_if(cmd: Command) -> list[str]:
    """The command names inside an ``if`` command's quoted branches."""
    names = []
    for raw, word in zip(cmd.raw_args[1:], cmd.args[1:]):
        if raw[:1] in "\"'" and word.strip():
            names.append(word.split()[0])
    return names


def apply_edits(text: str, replace: dict, insert: dict, header=()) -> str:
    lines = text.splitlines()
    out = list(header)
    out.extend(insert.get(0, []))
    skip_until = 0
    for number, line in enumerate(lines, start=1):
        span = next(((a, b) for (a, b) in replace if a == number), None)
        if span is not None:
            out.extend(replace[span])
            skip_until = span[1]
        elif number > skip_until:
            out.append(line)
        out.extend(insert.get(number, []))
    return "\n".join(out) + "\n"


# ── planning ────────────────────────────────────────────────────────────────

def include_lines(include_path: str, stop_on_alarm: bool) -> list[str]:
    lines = ["# Added by Run with FFAST: live monitoring (ADR 0086)"]
    if stop_on_alarm:
        lines.append("variable ffast_stop_on_alarm index 1")
    lines.append(f"include {include_path}")
    return lines


def snapshot_lines(elements: list[str]) -> list[str]:
    """The one-off snapshot of the atoms as loaded (R2); harmless without the wrapper."""
    el = " ".join(elements)
    return [
        "# Added by Run with FFAST: snapshot of the atoms as loaded, proves atom order",
        "variable ffast_on index 0",
        'if "${ffast_on} == 1" then "write_dump all custom ${ffast_dir}/start.lammpstrj '
        f'id type element x y z modify sort id element {el} time yes units yes format float %.17g"',
    ]


def _hand_edit(include_path: str, anchor: Command | None, before: Command | None) -> str:
    where = []
    if anchor is not None:
        where.append(f"after line {anchor.last} ({anchor.name})")
    if before is not None:
        where.append(f"before line {before.first} ({before.name})")
    place = " and ".join(where) or "after the command that creates the atoms"
    return f"put `include {include_path}` once {place}, and run the job as `{WRAPPER} srun lmp …`"


def _pair_coeff_order(commands: list[Command], elements: list[str]) -> tuple[Refusal | None, Check | None]:
    seen = None
    for cmd in commands:
        if cmd.name != "pair_coeff" or cmd.args[:2] != ["*", "*"]:
            continue
        tail = []
        for word in reversed(cmd.args[2:]):
            if not ELEMENT_RE.match(word):
                break
            tail.insert(0, word)
        if not tail:
            continue
        if len(tail) == len(elements) and all(t in ("NULL", e) for t, e in zip(tail, elements)):
            seen = cmd
            continue
        return Refusal(
            f"pair_coeff gives LAMMPS atom types the elements {' '.join(tail)}, the reference "
            f"has types {' '.join(elements)}; the forces would be computed for the wrong elements",
            cmd.first,
            f"write the data file and pair_coeff with types in the order {' '.join(elements)}",
        ), None
    if seen is not None:
        return None, Check("ok", f"pair_coeff * * … {' '.join(elements)} matches the reference's types", seen.first)
    return None, Check("warn", "no `pair_coeff * * … <elements>` line: the element order of the force field is not checked")


def plan_input(
    text: str,
    *,
    include_path: str,
    elements: list[str],
    data_file: str | None = None,
    stop_on_alarm: bool = False,
    reset_after_dump_ok: bool = True,
    snapshot: bool = False,
) -> InputPlan:
    """Where the FFAST include goes in ``text``, or why it cannot go anywhere safely.

    ``data_file``: absolute path of a data file FFAST writes; the copy's
    ``read_data`` then names it. ``reset_after_dump_ok``: False when the module
    check showed this LAMMPS build refuses ``reset_timestep`` after a dump wrote.
    """
    plan = InputPlan()
    commands = read_commands(text)

    def refuse(reason, cmd, hand=None, anchor=None, before=None):
        plan.refusal = Refusal(reason, cmd.first if cmd else None,
                               hand or _hand_edit(include_path, anchor, before))
        return plan

    include_name = include_path.rsplit("/", 1)[-1]
    plan.already_included = any(
        c.name == "include" and c.args and (c.args[0] == include_path or c.args[0].rsplit("/", 1)[-1] == include_name)
        for c in commands)
    plan.plugins = any(c.name == "plugin" and c.args[:1] == ["load"] for c in commands)
    for c in commands:
        if c.name == "pair_style" and c.args:
            if c.args[0] in ("hybrid", "hybrid/overlay", "hybrid/scaled"):
                plan.hybrid = True
                plan.pair_styles.extend(a for a in c.args[1:] if re.match(r"^[a-z][\w/]*$", a))
            else:
                plan.pair_styles.append(c.args[0])

    units = [c for c in commands if c.name == "units"]
    unit = units[-1].args[0] if units and units[-1].args else "lj"
    if unit not in ACCEPTED_UNITS:
        return refuse(f"units {unit}: FFAST scores only metal and real (Å)", units[-1] if units else None,
                      "use `units metal` or `units real`")

    runs = [c for c in commands if c.name == "run"]
    first_run = runs[0] if runs else None
    if first_run is None:
        return refuse("the input has no `run` command, so there is nothing to watch", None,
                      "add the `run` the job should make")

    # Best guess of the right place, for the hand edit of a refusal.
    start_guess = next((c for c in commands if c.name in ("run", "minimize")), first_run)
    guess = [c for c in commands if c.name in ATOM_MAKERS and c.last < start_guess.first]
    guess_anchor = guess[-1] if guess else None

    # Commands that never fit, wherever they are.
    if not plan.already_included:
        for c in commands:
            if c.name == "clear":
                return refuse("`clear` deletes the atoms and FFAST's dump with them", c,
                              f"split the input at line {c.first} into two inputs and run each "
                              f"as its own job; add the include to the one to be watched")
            if c.name in ("jump", "next"):
                loop = "a loop" if c.name == "next" or len(c.args) < 2 or _label_before(commands, c) else "a jump"
                after_anchor = guess_anchor.last if guess_anchor else 0
                labels = [x for x in commands if x.name == "label" and after_anchor < x.first < start_guess.first]
                before = labels[0] if labels else start_guess
                return refuse(f"`{c.name}` makes {loop}: LAMMPS could run the include again, or skip it", c,
                              anchor=guess_anchor, before=before)
    for c in commands:
        if c.name == "rerun":
            return refuse("`rerun` reads frames from files; there is no simulation to watch", c,
                          "watch the run that wrote those files instead")
        if c.name in ("replicate", "delete_atoms"):
            return refuse(f"`{c.name}` changes the atoms; FFAST scores one molecule with a fixed atom list", c,
                          f"remove line {c.first}, or prepare the system in a separate input")
        if c.name == "if":
            inner = _commands_in_if(c)
            bad = [n for n in inner if n in ATOM_MAKERS + ("clear", "jump", "next", "replicate", "delete_atoms")]
            if bad:
                return refuse(f"`{bad[0]}` inside `if` runs only sometimes; atoms are created where FFAST "
                              f"cannot see" if bad[0] in ATOM_MAKERS else
                              f"`{bad[0]}` inside `if` can make a loop or delete the atoms", c)

    makers = [c for c in commands if c.name in ATOM_MAKERS]
    readers = [c for c in makers if c.name in ("read_data", "read_restart")]
    for c in readers:
        if c.name == "read_data" and "add" in c.args[1:]:
            return refuse("`read_data … add` adds atoms to a box that already has some; "
                          "FFAST needs one set of atoms read once", c,
                          "merge the data files into one and read it once")
    if len(readers) > 1:
        return refuse(f"a second `{readers[1].name}` (first on line {readers[0].first}) replaces or adds atoms",
                      readers[1], "keep one `read_data` or `read_restart`, then add the include after it")

    first_min = next((c for c in commands if c.name == "minimize"), None)
    start = first_run if first_min is None or first_run.first < first_min.first else first_min
    if first_min is not None and first_run.first < first_min.first:
        return refuse("`minimize` after the first `run`: minimizer frames would be scored as dynamics, "
                      "all at the same time", first_min,
                      "minimize in a separate input or before the first `run`")
    before = [c for c in makers if c.last < start.first]
    after = [c for c in makers if c.first > start.first]
    if after:
        return refuse(f"`{after[0].name}` after the first `{start.name}` adds atoms once the dump has started",
                      after[0], f"create all atoms before line {start.first}")
    if not before:
        hidden = [c for c in commands if c.name == "include" and c.first < start.first]
        if hidden:
            return refuse("no `read_data`, `read_restart` or `create_atoms` in this file; atoms are created "
                          "where FFAST cannot see", hidden[0],
                          f"put `include {include_path}` after the file that creates the atoms "
                          f"(line {hidden[0].first}), before line {start.first}")
        return refuse("no `read_data`, `read_restart` or `create_atoms` before the first "
                      f"`{start.name}`", start)
    anchor = before[-1]

    # Data file swap (P6).
    if data_file is not None:
        if anchor.name != "read_data" or not readers:
            return refuse("a new data file was asked for, but the atoms come from "
                          f"`{anchor.name}`", anchor,
                          "untick “Write a new data file”, or read the atoms with `read_data`")
        rd = readers[0]
        if "$" in rd.args[0]:
            return refuse("`read_data` names its file through a variable; FFAST cannot swap it safely", rd,
                          "untick “Write a new data file”, or write the file name out")
        styles = [c for c in commands if c.name == "atom_style" and c.first < rd.first]
        style = styles[-1].args[0] if styles and styles[-1].args else "atomic"
        if style != "atomic":
            return refuse(f"atom_style {style}: FFAST writes `atomic` data files", styles[-1],
                          "untick “Write a new data file”, or use `atom_style atomic`")
        indent = re.match(r"\s*", text.splitlines()[rd.first - 1]).group(0)
        plan.replace[(rd.first, rd.last)] = [
            indent + " ".join(["read_data", data_file] + rd.raw_args[1:])]
        plan.checks.append(Check("ok", f"read_data reads the new data file {data_file}", rd.first))

    if not plan.already_included:
        for c in commands:
            # LAMMPS refuses reset_timestep only once a dump has written a frame
            # (Output::reset_timestep, last_dump >= 0); the dump writes its
            # first frame when the first run starts, so only a reset after the
            # first run matters. A reset between minimize and run is fine.
            if c.name == "reset_timestep" and c.first > first_run.first and not reset_after_dump_ok:
                return refuse("`reset_timestep` after the first `run`, once the dump has written; "
                              "this LAMMPS build refuses that", c,
                              f"move line {c.first} before the first `run`, or remove it")

    pc_refusal, pc_check = _pair_coeff_order(commands, elements)
    if pc_refusal is not None:
        plan.refusal = pc_refusal
        return plan

    if plan.already_included:
        inc = next(c for c in commands if c.name == "include"
                   and (c.args[0] == include_path or c.args[0].rsplit("/", 1)[-1] == include_name))
        if stop_on_alarm:
            plan.insert.setdefault(inc.first - 1, []).append("variable ffast_stop_on_alarm index 1")
        plan.include_after = inc.first - 1
        plan.checks.append(Check("ok", f"the input already includes the FFAST file (line {inc.first}); "
                                       "copied without a second include", inc.first))
    elif first_min is not None or snapshot:
        plan.snapshot_after = anchor.last
        plan.insert.setdefault(anchor.last, []).extend(snapshot_lines(elements))
        plan.include_after = first_run.first - 1
        plan.insert.setdefault(first_run.first - 1, []).extend(include_lines(include_path, stop_on_alarm))
        plan.checks.append(
            Check(
                "ok",
                f"snapshot after line "
                f"{anchor.last} ({anchor.name}), include before line {first_run.first} (run)",
                first_run.first,
            )
        )
    else:
        plan.include_after = anchor.last
        plan.insert.setdefault(anchor.last, []).extend(include_lines(include_path, stop_on_alarm))
        plan.checks.append(Check("ok", f"include goes after line {anchor.last} ({anchor.name})", anchor.last))

    defined = next((c for c in commands if c.name == "variable" and c.args[:1] == ["ffast_stop_on_alarm"]), None)
    if defined is not None and stop_on_alarm:
        plan.checks.append(Check("warn", f"the input sets ffast_stop_on_alarm itself (line {defined.first}); "
                                         "that value wins", defined.first))
    plan.checks.append(Check("ok", f"units {unit}; {len(runs)} `run` line{'s' if len(runs) != 1 else ''}"))
    if pc_check is not None:
        plan.checks.append(pc_check)
    if not any(c.name in ("restart", "write_restart") for c in commands):
        plan.checks.append(Check("warn", "no `restart` or `write_restart`: a stopped run cannot be continued"))
    if plan.plugins:
        plan.checks.append(Check("warn", "the input loads a plugin; its pair style cannot be checked in advance"))
    return plan


def _label_before(commands: list[Command], jump: Command) -> bool:
    """True when ``jump`` targets a label that appears before it (a loop)."""
    target = jump.args[1] if len(jump.args) > 1 else None
    return any(c.name == "label" and c.args[:1] == [target] and c.first < jump.first for c in commands)
