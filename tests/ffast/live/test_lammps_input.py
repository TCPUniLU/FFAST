"""Where "Run with FFAST" puts the include in real-world LAMMPS inputs, or why it refuses."""

from pathlib import Path

import pytest

from ffast.live.lammps_input import plan_input, read_commands

INPUTS = Path(__file__).parent / "fixtures" / "run_with_ffast" / "inputs"
INCLUDE = "/project/home/pXXXXXX/refs/aspirin_mace.lmp"
ELEMENTS = ["C", "H", "O"]

# fixture: (include after line, snapshot after line) or ("refused", line at fault, words in the reason)
TABLE = {
    "simple.in": (6, None),
    "create_atoms.in": (7, None),
    "restart.in": (3, None),
    "minimize.in": (12, 3),
    "minimize_no_reset.in": (7, 2),
    "several_runs.in": (3, None),
    "run_every.in": (2, None),
    "print_block.in": (7, None),
    "read_data_variable.in": (3, None),
    "already_included.in": (2, None),
    "jump_loop.in": ("refused", 10, "loop"),
    "jump_self.in": ("refused", 7, "loop"),
    "jump_forward.in": ("refused", 5, "jump"),
    "clear.in": ("refused", 7, "clear"),
    "two_read_data.in": ("refused", 3, "second `read_data`"),
    "read_data_add.in": ("refused", 3, "add"),
    "read_data_in_include.in": ("refused", 2, "cannot see"),
    "read_data_in_if.in": ("refused", 3, "cannot see"),
    "replicate.in": ("refused", 3, "replicate"),
    "delete_atoms.in": ("refused", 4, "delete_atoms"),
    "minimize_between_runs.in": ("refused", 5, "minimize"),
    "units_lj.in": ("refused", 1, "units lj"),
    "pair_coeff_order.in": ("refused", 4, "wrong elements"),
}


def _plan(name, **kw):
    return plan_input((INPUTS / name).read_text(), include_path=INCLUDE, elements=ELEMENTS, **kw)


def test_every_fixture_is_in_the_table():
    assert sorted(p.name for p in INPUTS.glob("*.in")) == sorted(TABLE)


@pytest.mark.parametrize("name", sorted(TABLE))
def test_where_the_include_lands(name):
    expected = TABLE[name]
    plan = _plan(name)
    if expected[0] == "refused":
        assert plan.refusal is not None, f"{name} should be refused"
        assert plan.refusal.line == expected[1]
        assert expected[2] in plan.refusal.reason
        assert "include" in plan.refusal.hand_edit or plan.refusal.hand_edit
        return
    assert plan.refusal is None, plan.refusal and plan.refusal.text()
    assert (plan.include_after, plan.snapshot_after) == expected


@pytest.mark.parametrize("name", [n for n, e in TABLE.items() if e[0] != "refused"])
def test_the_copy_has_the_include_once_where_planned(name):
    plan = _plan(name)
    copy = plan.apply((INPUTS / name).read_text(), header=["# marker"])
    commands = read_commands(copy)
    includes = [c for c in commands if c.name == "include" and c.args[0] == INCLUDE]
    assert len(includes) == 1
    first_run = next(c for c in commands if c.name == "run")
    assert includes[0].first < first_run.first
    makers = [c for c in commands if c.name in ("read_data", "read_restart", "create_atoms")]
    assert makers[-1].last < includes[0].first
    snaps = [c for c in commands if c.name == "if" and "start.lammpstrj" in " ".join(c.args)]
    assert len(snaps) == (1 if plan.snapshot_after else 0)
    if snaps:
        assert makers[-1].last < snaps[0].first
        mini = next(c for c in commands if c.name == "minimize")
        assert snaps[0].first < mini.first < includes[0].first


def test_the_original_lines_survive_unchanged():
    text = (INPUTS / "minimize.in").read_text()
    copy = _plan("minimize.in").apply(text)
    added = [line for line in copy.splitlines() if line not in text.splitlines()]
    kept = [line for line in copy.splitlines() if line in text.splitlines()]
    assert kept == text.splitlines()
    assert any("include " + INCLUDE == line for line in added)


def test_comments_and_print_blocks_are_not_commands():
    commands = read_commands((INPUTS / "print_block.in").read_text())
    names = [c.name for c in commands]
    assert names.count("run") == 1 and "clear" not in names
    pc = next(c for c in commands if c.name == "pair_coeff")
    assert pc.args[-3:] == ["C", "H", "O"] and (pc.first, pc.last) == (9, 10)


def test_the_hand_edit_names_where_the_include_goes():
    plan = _plan("jump_loop.in")
    assert INCLUDE in plan.refusal.hand_edit
    assert "after line 2 (read_data)" in plan.refusal.hand_edit
    assert "before line 7 (label)" in plan.refusal.hand_edit


def test_stop_on_alarm_sets_the_variable_before_the_include():
    plan = _plan("simple.in", stop_on_alarm=True)
    lines = plan.apply((INPUTS / "simple.in").read_text()).splitlines()
    i = lines.index("include " + INCLUDE)
    assert lines[i - 1] == "variable ffast_stop_on_alarm index 1"


def test_already_included_keeps_one_include_and_takes_stop_on_alarm():
    text = (INPUTS / "already_included.in").read_text()
    plan = _plan("already_included.in", stop_on_alarm=True)
    lines = plan.apply(text).splitlines()
    assert sum(INCLUDE in line for line in lines) == 1
    assert lines[lines.index("include      " + INCLUDE) - 1] == "variable ffast_stop_on_alarm index 1"


def test_new_data_file_replaces_the_read_data_file_and_keeps_its_keywords():
    text = "units metal\nread_data aspirin.data extra/atom/types 1\nfix nve all nve\nrun 10\n"
    plan = plan_input(text, include_path=INCLUDE, elements=ELEMENTS, data_file="/w/run.ffast.data")
    lines = plan.apply(text).splitlines()
    assert lines[1] == "read_data /w/run.ffast.data extra/atom/types 1"
    assert lines[3] == "include " + INCLUDE


@pytest.mark.parametrize("name, words", [
    ("read_data_variable.in", "variable"),
    ("restart.in", "read_restart"),
])
def test_new_data_file_refusals(name, words):
    plan = _plan(name, data_file="/w/run.ffast.data")
    assert plan.refusal is not None and words in plan.refusal.reason
    assert "Write a new data file" in plan.refusal.hand_edit


def test_new_data_file_needs_atom_style_atomic():
    text = "units real\natom_style full\nread_data a.data\nrun 10\n"
    plan = plan_input(text, include_path=INCLUDE, elements=ELEMENTS, data_file="/w/x.data")
    assert plan.refusal is not None and "atom_style full" in plan.refusal.reason


def test_reset_timestep_after_the_dump_refused_only_when_the_build_refuses_it():
    text = "units metal\nread_data a.data\nfix nve all nve\nrun 10\nreset_timestep 0\nrun 10\n"
    ok = plan_input(text, include_path=INCLUDE, elements=ELEMENTS)
    assert ok.refusal is None
    bad = plan_input(text, include_path=INCLUDE, elements=ELEMENTS, reset_after_dump_ok=False)
    assert bad.refusal is not None and bad.refusal.line == 5


@pytest.mark.parametrize("name", ["minimize.in", "simple.in"])
def test_reset_timestep_before_the_first_run_is_fine_on_any_build(name):
    """MeluXina 2 Aug 2023: "Cannot reset timestep with active dump" comes only
    once the dump has written, at the first run. minimize; reset_timestep 0;
    run is the common form and must pass (found by the step-5 proof)."""
    plan = _plan(name, reset_after_dump_ok=False)
    assert plan.refusal is None, plan.refusal and plan.refusal.text()
    text = "units metal\nread_data a.data\nreset_timestep 0\nrun 10\n"
    assert plan_input(text, include_path=INCLUDE, elements=ELEMENTS, reset_after_dump_ok=False).refusal is None


def test_no_run_is_refused():
    plan = plan_input("units metal\nread_data a.data\n", include_path=INCLUDE, elements=ELEMENTS)
    assert plan.refusal is not None and "no `run`" in plan.refusal.reason


def test_checks_report_pair_coeff_restart_and_pair_styles():
    plan = _plan("simple.in")
    texts = [c.text for c in plan.checks]
    assert any("matches the reference" in t for t in texts)
    assert not any("restart" in t for t in texts)          # simple.in writes restarts
    assert plan.pair_styles == ["mace"]
    plan = _plan("several_runs.in")
    assert any(c.status == "warn" and "restart" in c.text for c in plan.checks)
    assert any("3 `run` lines" in c.text for c in plan.checks)


def test_hybrid_pair_styles_are_listed():
    text = ("units metal\nread_data a.data\npair_style hybrid/overlay mace no_domain_decomposition "
            "zbl 3.0 4.0\nrun 10\n")
    plan = plan_input(text, include_path=INCLUDE, elements=ELEMENTS)
    # Sub-style keywords cannot be told from sub-style names here; the module
    # check matches the candidates against the build's list (hybrid flag).
    assert plan.hybrid and {"mace", "zbl"} <= set(plan.pair_styles)
    assert "3.0" not in plan.pair_styles
