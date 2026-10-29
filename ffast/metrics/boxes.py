"""Keep boxes: a metric's grouping as four checkboxes (ADR 0065).

A **base metric** is an unreduced value such as Force Difference (per atom ×
xyz). A **setting** says which of its axes stay apart — the keep boxes atom,
structure, element and x/y/z, the same meaning as ``by=`` — how unkept x/y/z
become one number (**Combine**: per component or length), and which
**Statistic** combines what is not kept (MAE, RMSE, Max, Mean (signed)).

Every setting is a formula over the base, written with the variable ``D``
(``formula``), so there is one engine: the ADR 0064 reductions. A setting's
metric has a derived id, base + setting (``ffast.force_difference[structure;
component;rmse]``), so the same setting is always the same metric and a tab
file can name it; ``compile_box_metric`` registers one on demand.

``classify`` reads a formula back into its base and setting by matching its
parsed tree against the formulas the boxes write, ``D`` standing for any
part of the tree that is a metric. A formula that fits only partly is
**custom**: its boxes come from its shape alone.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from itertools import product

from ffast.metrics import dims

KEEP_ORDER = ("atom", "structure", "element", "xyz")
COMBINES = ("component", "length")
STATISTICS = ("mae", "rmse", "max", "mean")

STATISTIC_LABELS = {"mae": "MAE", "rmse": "RMSE", "max": "Max", "mean": "Mean (signed)"}
COMBINE_LABELS = {"component": "per component", "length": "length"}
# Each setting's grouping in words, by the row boxes kept (xyz aside).
_KEEP_WORDS = {
    frozenset({"atom", "structure"}): "per atom",
    frozenset({"atom"}): "per atom, over frames",
    frozenset({"structure"}): "per frame",
    frozenset({"structure", "element"}): "per frame and element",
    frozenset({"element"}): "per element",
    frozenset(): "dataset",
}
# The id's word for "nothing kept".
_NOTHING = "dataset"

# What each kind of row can keep, and the by= word of each choice; None = no
# reduction across rows. A metric's rows are its shape's leading axis.
_PAIR = "(structure, element)"
_BY = {
    "atom": {
        frozenset({"atom", "structure"}): None,
        frozenset({"atom"}): "atom",
        frozenset({"structure", "element"}): _PAIR,
        frozenset({"structure"}): "structure",
        frozenset({"element"}): "element",
        frozenset(): "all",
    },
    "frame": {frozenset({"structure"}): None, frozenset(): "all"},
    "element": {frozenset({"element"}): None, frozenset(): "all"},
    "table": {frozenset({"structure", "element"}): None},
    "none": {frozenset(): None},
}
# What a metric that is already reduced says about the finer boxes.
_ALREADY = {
    "frame": "this metric is already averaged per frame",
    "element": "this metric is already averaged per element",
    "table": "this metric is already averaged per frame and element",
    "none": "this metric is already one value for the dataset",
}


@dataclass(frozen=True)
class Setting:
    """``keep``: the ticked boxes; ``combine``: None when x/y/z is kept or
    the metric has no parts; ``statistic``: None when nothing is averaged."""

    keep: frozenset
    combine: str | None = None
    statistic: str | None = None

    def to_dict(self) -> dict:
        return {"keep": [k for k in KEEP_ORDER if k in self.keep],
                "combine": self.combine, "statistic": self.statistic}

    @classmethod
    def from_dict(cls, d: dict) -> "Setting":
        return cls(frozenset(d.get("keep") or ()), d.get("combine"), d.get("statistic"))


@dataclass(frozen=True)
class Reading:
    """What ``classify`` found. ``base`` and ``setting`` are None for a
    custom formula; ``keep`` is the boxes its shape gives (always set)."""

    base: str | None
    setting: Setting | None
    custom: bool
    keep: frozenset


# --------------------------------------------------------------------------- #
# Shapes
# --------------------------------------------------------------------------- #
def _shape(shape) -> tuple:
    return shape if isinstance(shape, tuple) else (shape,)


def rows_of(shape) -> str:
    """The kind of row a shape has: atom, frame, element, table or none."""
    from ffast.metrics.expr import is_table
    shape = _shape(shape)
    if is_table(shape):
        return "table"
    return {dims.N_atoms: "atom", dims.N_frames: "frame",
            dims.N_elements: "element"}.get(shape[0], "none")


def has_parts(shape) -> bool:
    from ffast.metrics.expr import _part_axes
    return bool(_part_axes(_shape(shape)))


def identity(shape) -> Setting:
    """The setting that keeps everything: the metric itself."""
    keep = next(k for k, by in _BY[rows_of(shape)].items() if by is None)
    return Setting(keep | ({"xyz"} if has_parts(shape) else set()))


def keep_of_shape(shape) -> frozenset:
    return identity(shape).keep


# --------------------------------------------------------------------------- #
# Setting → formula
# --------------------------------------------------------------------------- #
def setting_problem(setting: Setting, shape) -> str | None:
    """Why the boxes grey this setting for a metric of ``shape``, or None."""
    rows = rows_of(shape)
    parts = has_parts(shape)
    row_keep = setting.keep - {"xyz"}
    if not set(setting.keep) <= set(KEEP_ORDER):
        return f"unknown box {sorted(set(setting.keep) - set(KEEP_ORDER))[0]}"
    if "xyz" in setting.keep and not parts:
        return "this metric has no x/y/z to keep"
    if row_keep not in _BY[rows]:
        if rows == "atom" and "atom" in row_keep and "element" in row_keep:
            return "element is greyed when atom is ticked"
        return _ALREADY.get(rows, "these boxes do not fit this metric")
    by = _BY[rows][row_keep]
    combining = parts and "xyz" not in setting.keep
    if combining and setting.combine not in COMBINES:
        return "Combine must say per component or length"
    if not combining and setting.combine is not None:
        return "Combine applies only when x/y/z is not kept"
    averaged = by is not None or (combining and setting.combine == "component")
    if not averaged and setting.statistic is not None:
        return "nothing is averaged, so there is no Statistic"
    if averaged and setting.statistic not in STATISTICS:
        return "Statistic must be MAE, RMSE, Max or Mean (signed)"
    if setting.combine == "length" and setting.statistic == "mean":
        return "a length has no sign; Mean (signed) needs per component or x/y/z"
    return None


def _formula(by, mode, statistic, d="D") -> str:
    """The formula for one grouping (by= word or None), one way of treating
    the parts (``plain``: none or kept, ``length``, ``component``) and one
    statistic."""
    if mode == "length":
        row = f"norm({d})"
    elif mode == "component":
        row = {"mae": f"mean_parts(abs({d}))", "rmse": f"mean_parts({d} ** 2)",
               "max": f"max_parts(abs({d}))", "mean": f"mean_parts({d})", None: d}[statistic]
    else:
        row = {"mae": f"abs({d})", "max": f"abs({d})"}.get(statistic, d)
    if by is None:
        return f"sqrt({row})" if mode == "component" and statistic == "rmse" else row
    if mode == "component" and statistic == "rmse":
        return f"sqrt(mean({row}, by={by}))"
    reducer = {"mae": "mean", "rmse": "rms", "max": "max", "mean": "mean"}[statistic]
    return f"{reducer}({row}, by={by})"


def _mode(setting: Setting, shape) -> str:
    if has_parts(shape) and "xyz" not in setting.keep:
        return setting.combine
    return "plain"


def formula(setting: Setting, shape, d: str = "D") -> str:
    """The formula a setting writes over a metric of ``shape`` named ``d``."""
    problem = setting_problem(setting, shape)
    if problem:
        raise ValueError(problem)
    by = _BY[rows_of(shape)][setting.keep - {"xyz"}]
    return _formula(by, _mode(setting, shape), setting.statistic, d)


def settings_for(shape) -> list[Setting]:
    """Every setting the boxes allow for a metric of ``shape``."""
    out = []
    keeps = [frozenset(k for k, on in zip(KEEP_ORDER, flags) if on)
             for flags in product((False, True), repeat=len(KEEP_ORDER))]
    for keep, combine, statistic in product(keeps, (None, *COMBINES), (None, *STATISTICS)):
        s = Setting(keep, combine, statistic)
        if setting_problem(s, shape) is None:
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Ids and names
# --------------------------------------------------------------------------- #
_BOX_ID = re.compile(r"^(?P<base>[^\[\]]+)\[(?P<setting>[a-z+;]+)\]$")


def box_id(base: str, setting: Setting) -> str:
    """``base[keep;combine;statistic]``, leaving out what is None:
    ``ffast.force_difference[structure;component;rmse]``."""
    keep = "+".join(k for k in KEEP_ORDER if k in setting.keep) or _NOTHING
    fields = [keep] + [f for f in (setting.combine, setting.statistic) if f]
    return f"{base}[{';'.join(fields)}]"


def parse_box_id(metric_id: str) -> tuple[str, Setting] | None:
    """``(base, setting)`` of a box id written as ``box_id`` writes it, or
    None for any other id."""
    m = _BOX_ID.match(metric_id)
    if not m:
        return None
    fields = m.group("setting").split(";")
    keep = frozenset() if fields[0] == _NOTHING else frozenset(fields[0].split("+"))
    rest = fields[1:]
    combine = rest.pop(0) if rest and rest[0] in COMBINES else None
    statistic = rest.pop(0) if rest and rest[0] in STATISTICS else None
    setting = Setting(keep, combine, statistic)
    if rest or box_id(m.group("base"), setting) != metric_id:
        return None
    return m.group("base"), setting


def box_label(base_label: str, setting: Setting) -> str:
    """"Force Difference · MAE · per frame · per component"."""
    parts = [base_label]
    if setting.statistic:
        parts.append(STATISTIC_LABELS[setting.statistic])
    parts.append(_KEEP_WORDS.get(setting.keep - {"xyz"}, "custom"))
    if "xyz" in setting.keep:
        parts.append("per axis")
    elif setting.combine:
        parts.append(COMBINE_LABELS[setting.combine])
    return " · ".join(parts)


def compile_box_metric(metric_id: str, *, registry=None) -> str:
    """Register the formula metric of a box id over its base. Idempotent;
    raises ValueError naming why for an id the boxes would grey."""
    from ffast.metrics.expr import compile_expr_metric
    from ffast.metrics.registry import default_registry
    registry = registry if registry is not None else default_registry
    if registry.has(metric_id):
        return metric_id
    parsed = parse_box_id(metric_id)
    if parsed is None:
        raise ValueError(f"{metric_id} is not a box id such as "
                         f"ffast.force_difference[structure;component;mae]")
    base, setting = parsed
    if not registry.has(base):
        raise ValueError(f"{metric_id}: {base} is not a metric")
    schema, _fn = registry.get(base)
    problem = setting_problem(setting, schema.shape)
    if problem:
        raise ValueError(f"{metric_id}: {problem}")
    return compile_expr_metric(
        metric_id, formula(setting, schema.shape), {"D": base},
        label=box_label(schema.label or base, setting), unit=schema.unit,
        registry=registry)


def ensure_metric(metric_id: str, registry=None) -> bool:
    """Whether ``metric_id`` is registered, compiling it first when it is a
    box id. Never raises."""
    from ffast.metrics.registry import default_registry
    registry = registry if registry is not None else default_registry
    if registry.has(metric_id):
        return True
    if parse_box_id(metric_id) is None or not hasattr(registry, "metric"):
        return False
    try:
        compile_box_metric(metric_id, registry=registry)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------- #
# What the Metrics pane is told (section 4)
# --------------------------------------------------------------------------- #
# A setting the boxes refuse on a dataset whose structures differ in atoms.
_DIFFERENT_ATOMS = "structures have different atoms"


def read_metric(metric_id: str, registry) -> tuple[str, Setting, bool]:
    """``(base, setting, custom)`` the boxes open a metric with: a box id is
    its base and setting; a formula or a built-in with a formula twin that
    the boxes could have written is that base and setting; anything else is
    its own base with everything kept — custom when it is a formula that
    reduces in a way the boxes do not write."""
    from ffast.metrics.expr import _ExprFn
    parsed = parse_box_id(metric_id)
    if parsed is not None:
        return parsed[0], parsed[1], False
    schema, fn = registry.get(metric_id)
    found = _formula_of(metric_id, registry)
    if found is not None:
        reading = classify(found[0], found[1], registry)
        if not reading.custom and reading.base != metric_id:
            return reading.base, reading.setting, False
    custom = isinstance(fn, _ExprFn) and _reduces(fn.expr)
    return metric_id, identity(schema.shape), custom


def _reduces(expr: str) -> bool:
    """Whether a formula combines values: a by= reduction or a part collapse."""
    from ffast.metrics.expr import _COLLAPSING, _REDUCERS, parse_and_validate
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in _REDUCERS | _COLLAPSING
               for n in ast.walk(parse_and_validate(expr)))


def box_state(registry, *, metric_id: str | None = None, base: str | None = None,
              setting: dict | None = None, uniform: bool | None = None,
              saved=lambda _id: False) -> dict:
    """Everything the boxes show for a metric (``metric_id``) or for a box
    change (``base`` + ``setting``): the setting, the settings allowed, why
    a box is greyed, and the formula, id, name, unit and shape it gives.
    ``uniform`` False greys atom ✓ structure ✗ (by=atom). A box id
    ``exists`` only once ``saved`` says someone saved it: tabs and colouring
    register settings nobody named."""
    from ffast.metrics.dims import shape_to_str
    from ffast.metrics.expr import analyze_expr
    custom = False
    if metric_id is not None:
        registry.ensure(metric_id)
        base, current, custom = read_metric(metric_id, registry)
    else:
        current = Setting.from_dict(setting or {})
    schema, _fn = registry.get(base)
    shape = schema.shape
    allowed = settings_for(shape)
    hints = {}
    for box in KEEP_ORDER:
        if not any(box in s.keep for s in allowed):
            hints[box] = ("this metric has no x/y/z" if box == "xyz"
                          else _ALREADY.get(rows_of(shape), "these boxes do not fit this metric"))
    if uniform is False:
        allowed = [s for s in allowed if s.keep - {"xyz"} != {"atom"}]
        hints["atom_only"] = _DIFFERENT_ATOMS
    problem = setting_problem(current, shape)
    if problem is None and uniform is False and current.keep - {"xyz"} == {"atom"}:
        problem = _DIFFERENT_ATOMS
    state = {
        "base": base, "base_label": schema.label or base,
        "setting": current.to_dict(), "custom": custom,
        "allowed": [s.to_dict() for s in allowed], "hints": hints,
        "problem": problem, "id": None, "expr": None, "vars": None,
        "label": None, "unit": schema.unit, "shape": None, "exists": False,
    }
    if problem is not None:
        return state
    same = current == identity(shape)
    state["id"] = base if same else box_id(base, current)
    state["expr"] = formula(current, shape)
    state["vars"] = {"D": base}
    state["label"] = (schema.label or base) if same else box_label(schema.label or base, current)
    state["shape"] = shape_to_str(analyze_expr(state["expr"], state["vars"], registry=registry)[1])
    state["exists"] = (registry.has(state["id"]) if state["id"] == base
                       else saved(state["id"]))
    return state


def box_formula_problem(metric_id: str, expr: str, vars_: dict, registry) -> str | None:
    """Why a metric saved under a box id may not have this formula, or None:
    a box id's formula is its setting's (ADR 0065)."""
    parsed = parse_box_id(metric_id)
    if parsed is None:
        return None
    base, setting = parsed
    if not registry.has(base):
        return f"{base} is not a metric"
    problem = setting_problem(setting, registry.get(base)[0].shape)
    if problem:
        return f"{metric_id}: {problem}"
    if expr != formula(setting, registry.get(base)[0].shape) or dict(vars_) != {"D": base}:
        return (f"{metric_id} is a keep-box setting; its formula is set by the boxes. "
                f"Use Copy and edit to change the formula")
    return None


# --------------------------------------------------------------------------- #
# Formula → setting
# --------------------------------------------------------------------------- #
_BY_NAMES = {"structure", "element", "atom", "all", _PAIR, None}


def _templates():
    """``(parsed formula, by, mode, statistic)`` for every formula the boxes
    can write, whatever the base."""
    global _TEMPLATES
    if _TEMPLATES is None:
        seen, out = set(), []
        for by, mode, statistic in product(
                ("structure", "element", "atom", "all", _PAIR, None),
                ("plain", "length", "component"), (None, *STATISTICS)):
            if by is not None and statistic is None:
                continue
            if mode == "component" and statistic is None:
                continue
            text = _formula(by, mode, statistic)
            if text in seen:
                continue
            seen.add(text)
            out.append((ast.parse(text, mode="eval").body, by, mode, statistic))
        # The most specific form first: `mean(norm(D), ...)` before
        # `mean(D, ...)`, and the bare `D` last, so D is the smallest metric.
        out.sort(key=lambda t: -sum(1 for _ in ast.walk(t[0])))
        _TEMPLATES = out
    return _TEMPLATES


_TEMPLATES = None


def _match(template, node, found: dict) -> bool:
    """Whether ``node`` has the form of ``template``, ``D`` matching any
    subtree (the same one each time); the match lands in ``found["D"]``."""
    if isinstance(template, ast.Name) and template.id == "D":
        if "D" in found:
            return ast.dump(found["D"]) == ast.dump(node)
        found["D"] = node
        return True
    if type(template) is not type(node):
        return False
    for name, t_value in ast.iter_fields(template):
        if name == "ctx":
            continue
        n_value = getattr(node, name, None)
        if isinstance(t_value, list):
            if not isinstance(n_value, list) or len(t_value) != len(n_value):
                return False
            if not all(_match(t, n, found) if isinstance(t, ast.AST) else t == n
                       for t, n in zip(t_value, n_value)):
                return False
        elif isinstance(t_value, ast.AST):
            if not isinstance(n_value, ast.AST) or not _match(t_value, n_value, found):
                return False
        elif t_value != n_value:
            return False
    return True


def _normal(node, vars_: dict) -> str:
    """A subtree with each variable replaced by what it is bound to, so two
    formulas over the same inputs compare equal whatever their names."""
    class _Bind(ast.NodeTransformer):
        def visit_Name(self, n):
            return ast.Name(id=vars_.get(n.id, n.id), ctx=ast.Load())
    import copy
    return ast.dump(_Bind().visit(copy.deepcopy(node)), annotate_fields=False)


def _formula_of(metric_id: str, registry):
    """``(expr, vars)`` of a metric: its formula, or its built-in twin."""
    from ffast.metrics.builtin.equivalents import equivalent_of
    from ffast.metrics.expr import _ExprFn
    fn = registry.get(metric_id)[1]
    if isinstance(fn, _ExprFn):
        return fn.expr, fn.user_vars
    return equivalent_of(metric_id)


def _base_of(d, vars_: dict, registry) -> str | None:
    """The metric a matched ``D`` is: a variable bound to a metric, or a
    subtree that is some metric's own formula."""
    if isinstance(d, ast.Name) and registry.has(vars_.get(d.id, "")):
        return vars_[d.id]
    want = _normal(d, vars_)
    for mid in registry.list_metrics():
        if parse_box_id(mid) is not None:
            continue
        found = _formula_of(mid, registry)
        if found is None:
            continue
        expr, its_vars = found
        try:
            if _normal(ast.parse(expr, mode="eval").body, its_vars) == want:
                return mid
        except SyntaxError:
            continue
    return None


def _setting_from(by, mode, statistic, shape) -> Setting | None:
    """The setting that writes this (by, mode, statistic) over ``shape``."""
    keep = next((k for k, b in _BY.get(rows_of(shape), {}).items() if b == by), None)
    if keep is None:
        return None
    parts = has_parts(shape)
    if parts and mode == "plain":
        keep = keep | {"xyz"}
    elif not parts and mode != "plain":
        return None
    setting = Setting(keep, None if mode == "plain" else mode, statistic)
    return setting if setting_problem(setting, shape) is None else None


def classify(expr: str, vars_: dict, registry) -> Reading:
    """Read a formula as a base metric and a setting (ADR 0065 section 3).
    A formula that fits the boxes only partly is custom: its keep boxes come
    from its shape and its base and setting are None."""
    from ffast.metrics.expr import analyze_expr, parse_and_validate
    tree = parse_and_validate(expr).body
    for template, by, mode, statistic in _templates():
        found: dict = {}
        if not _match(template, tree, found):
            continue
        base = _base_of(found["D"], vars_, registry)
        if base is None:
            continue
        setting = _setting_from(by, mode, statistic, registry.get(base)[0].shape)
        if setting is not None:
            return Reading(base, setting, False, setting.keep)
    _inputs, shape, _parts = analyze_expr(expr, vars_, registry=registry)
    return Reading(None, None, True, keep_of_shape(shape))
