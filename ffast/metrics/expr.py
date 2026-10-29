"""Expression-Metric compiler — element-wise algebra over refs, no Python (ADR 0042).

An **Expression Metric** is the middle rung of FFAST's custom-metric ladder,
between a **Dataset Field** passthrough (ADR 0023, no math) and a hand-written
Python ``@metric`` (the complex tail). A declarative ``[[metrics.expr]]`` entry
binds named **Expression Variables** to **Metric Inputs** and evaluates an
**element-wise** arithmetic ``expr`` string over them, registering the result as
an ordinary Metric:

    [[metrics.expr]]
    id    = "mylab.energy_per_atom"
    label = "Energy error per atom"
    unit  = "energy"
    expr  = "abs(pred - ref) / n_atoms"

      [metrics.expr.vars]
      pred = "prediction.info.pred_E"   # raw ref, Dataset Field, OR a metric id
      ref  = "reference.info.REF_energy"

``compile_expr_metric`` sits beside ``compile_field_metric`` (fields.py) and
``compile_transform`` (transforms.py) and runs in the same pre-freeze compile
pass on server, client, and headless thread (idempotent).

Design (ADR 0042 Decisions):

* **Reductions say what they combine** (ADR 0064, superseding "element-wise
  only"): ``mean/sum/min/max/rms/std(x, by=structure|element|all, where=...)``;
  ``by=`` alone decides the result's shape, and the vector parts are kept.
  ``by=(structure, element)`` gives a table: a row per structure, a column
  per element of the dataset.
  Grouping uses the same per-structure sums as the built-ins (segments.py).
  KDE and smoothing stay in the Transform layer.
* **Same-shape only.** Every non-scalar variable in one expr must resolve to the
  same **Metric Shape**; mixing shapes is rejected at config-load. Scalars (and
  numeric literals) broadcast (Decisions 2, 3).
* **``n_atoms`` reserved variable.** A per-structure atom-count array, auto-wired
  as the input ref ``"n_atoms"`` (resolved by ``InputResolver``); a user ``vars``
  entry named ``n_atoms`` is a Configuration Failure (Decision 4).
* **Variables may bind a metric id.** Metric-id inputs become **Metric Graph**
  dependencies, resolved by the Metric Execution Context (ADR 0035) — cycles are
  caught by the existing freeze validation (Decision 5).
* **Restricted-AST eval.** ``ast.parse(mode="eval")`` + a whitelist walk; no
  ``eval()``, no third-party lib (Decision 6, *Considered Options*).
* **Non-finite output raises a Metric Failure** — uniform with hand-authored
  metrics, honouring the Key Constraint (Decision 7).
* **Cache identity via ``implementation_source``** = expr + sorted var→ref map +
  whitelist version, so an expr edit under the same id busts the compute cache
  while the id stays referenceable verbatim (Decision 8).

The expr string and var→ref map ride the metric schema as data (not a closure):
a module-level ``_ExprFn`` (precedent: ``_TransformFn``, transforms.py) carries
them so the registry stays picklable for the metric worker pool.
"""
from __future__ import annotations

import ast

import numpy as np

from ffast.metrics import dims, units
from ffast.metrics.inputs import RAW_INPUT_BY_REF, parse_field_ref
from ffast.metrics.models import WithWarnings
from ffast.metrics.registry import default_registry
from ffast.metrics.segments import framesOrOne, segmentExtremes, segmentSums

# Bump when the whitelist (functions / operators / constants) changes, so every
# Expression Metric's cache identity invalidates (rides implementation_source).
_WHITELIST_VERSION = "5"

# Element-wise (shape-preserving) numpy functions. minimum/maximum/clip/where
# are element-wise, not the reducing min/max (Decision 6; those are _REDUCERS).
_FUNCS = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "exp": np.exp,
    "log": np.log,
    "log10": np.log10,
    "clip": np.clip,
    "where": np.where,
    "minimum": np.minimum,
    "maximum": np.maximum,
    "sign": np.sign,
}
_CONSTS = {"pi": np.pi}

# Part-axis functions (ADR 0057 rule 17). They read or collapse the trailing
# *part axes* of an input (a vector's xyz, a stress's 3x3 / Voigt), so they are
# handled by the shape-aware walk below, not by the plain ``_FUNCS`` table.
# ``norm``, ``mean_parts``, ``sum_parts`` and ``max_parts`` collapse every part
# axis (ADR 0064; ``max_parts`` ADR 0065, for the Max statistic).
_PART_FUNCS = {"norm", "mean_parts", "sum_parts", "max_parts", "x", "y", "z"}
_COLLAPSING = {"norm", "mean_parts", "sum_parts", "max_parts"}
_PART_REDUCERS = {"mean_parts": np.mean, "sum_parts": np.sum, "max_parts": np.max}
_XYZ_INDEX = {"x": 0, "y": 1, "z": 2}

# Reductions (ADR 0064): each combines the values of a group into one and
# needs ``by=`` to say which groups. ``where=`` picks the rows that count.
_REDUCERS = {"mean", "sum", "min", "max", "rms", "std"}
# ``by=atom`` (ADR 0065) groups the same atom position across structures, so
# it needs structures with the same atoms in the same order.
BY_WORDS = ("structure", "element", "atom", "all")
# ``by=(structure, element)`` (ADR 0064 amendment 2026-10-24): one group per
# structure and element, a table with a row per structure and a column per
# element. Only this order; the key is how by= is written.
PAIR = "(structure, element)"
_REDUCER_KEYWORDS = ("by", "where")
# The two-value functions a reducing min/max is easily mistaken for.
_PAIRWISE = {"min": "minimum", "max": "maximum"}
# ``element("H", ...)``: 1 for each atom of those elements, 0 for the others.
# The symbols are the only text a formula may hold.
_ELEMENT = "element"

_CMPOPS = {
    ast.Gt: lambda a, b: a > b,
    ast.Lt: lambda a, b: a < b,
    ast.GtE: lambda a, b: a >= b,
    ast.LtE: lambda a, b: a <= b,
    ast.Eq: lambda a, b: a == b,
}

_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
}
_UNARYOPS = {ast.USub: lambda a: -a}  # unary minus only (Decision 6)

# The reserved Expression Variable: a per-structure atom-count array the server
# provides. Its input ref is the same literal string (resolved by InputResolver).
RESERVED_VARS = {"n_atoms"}
_RESERVED_SHAPE = {"n_atoms": (dims.N_frames,)}

# Inputs a formula gets without naming them (ADR 0064 section 6): the dataset's
# offsets when it groups by structure, the atomic numbers when it groups by
# element or uses element(...). The leading underscore keeps them apart from
# the user's variables.
_OFFSETS = "_offsets"
_ELEMENTS = "_elements"
HIDDEN_INPUTS = {_OFFSETS: "offsets", _ELEMENTS: "reference.elements"}
# Where the evaluator collects the bad values that where= left out.
_IGNORED = "__ignored"
_MAX_FRAMES_SHOWN = 5


def ignored_warning(count: int, frames) -> str:
    """The sentence for bad values that where= left out (ADR 0064 amendment):
    they do not fail the metric, but are not passed over in silence."""
    one = count == 1
    text = (f"{count} bad value{'' if one else 's'} in rows left out by where= "
            f"{'was' if one else 'were'} ignored")
    frames = list(frames)
    if not frames:
        return text
    shown = ", ".join(str(f) for f in frames[:_MAX_FRAMES_SHOWN])
    more = ", …" if len(frames) > _MAX_FRAMES_SHOWN else ""
    return f"{text} (frame{'' if len(frames) == 1 else 's'} {shown}{more})"

# Raw (non-metric, non-field) input refs → their Metric Shape, for the same-shape
# check. Restricted to the continuous float quantities that element-wise algebra
# is meaningful over: stress/selection are not sourced yet (InputResolver returns
# None), and ``reference.elements`` (integer atomic numbers) is a categorical
# label, not a continuous quantity — all are intentionally absent and rejected as
# expr variables in v1.
_RAW_REF_SHAPE = {ref: item.shape for ref, item in RAW_INPUT_BY_REF.items()}


# Part dims → (size of each part axis). A shape ends in at most one part dim;
# the array carries one trailing axis per entry here.
_PART_AXES = {
    dims.xyz: (3,),
    dims.voigt: (6,),
    dims.tensor_3x3: (3, 3),
}


def _part_axes(shape) -> tuple:
    """Sizes of the trailing part axes of ``shape`` (``()`` if it has none)."""
    last = _as_tuple(shape)[-1]
    return _PART_AXES.get(last, ())


# --------------------------------------------------------------------------- #
# AST validation
# --------------------------------------------------------------------------- #
# Safety limits (ADR 0064 step 1). Each operation makes a full-size temporary
# array, and the shape check and the evaluator walk the tree recursively, so a
# formula's size is capped before anything else looks at it.
MAX_FORMULA_CHARS = 2000
MAX_FORMULA_DEPTH = 50

# AST nodes that mark an operator or a load, not a part of the formula.
_MARKERS = (ast.operator, ast.unaryop, ast.cmpop, ast.expr_context, ast.boolop)


def _depth(node) -> int:
    """Parts on the formula's deepest path: ``a`` is 1, ``abs(a)`` 2, and
    ``a + a + a`` 3 (Python reads it as ``(a + a) + a``). Without recursion,
    so a formula far past the limit is measured, not crashed on."""
    deepest = 0
    stack = [(node, 1)]
    while stack:
        n, d = stack.pop()
        deepest = max(deepest, d)
        for child in ast.iter_child_nodes(n):
            if not isinstance(child, _MARKERS):
                stack.append((child, d + 1))
    return deepest


def element_numbers() -> dict[str, int]:
    """Element symbol → atomic number (``"H"`` → 1), real elements only."""
    global _ELEMENT_NUMBERS
    if _ELEMENT_NUMBERS is None:
        from ase.data import atomic_numbers
        _ELEMENT_NUMBERS = {sym: z for sym, z in atomic_numbers.items() if z > 0}
    return _ELEMENT_NUMBERS


_ELEMENT_NUMBERS: dict[str, int] | None = None


def _keyword(node: ast.Call, name: str):
    """The value node of keyword ``name=`` in a call, or None."""
    return next((kw.value for kw in node.keywords if kw.arg == name), None)


def _check_reduction(node: ast.Call, expr: str) -> None:
    """``mean(x, by=..., where=...)`` and its siblings: one value, a ``by=``
    word, and no keyword but ``by`` and ``where``."""
    name = node.func.id
    if len(node.args) != 1:
        if name in _PAIRWISE and len(node.args) == 2:
            raise ValueError(
                f"expr {expr!r}: {name}(...) combines many values into one per "
                f"group; to take the {'smaller' if name == 'min' else 'larger'} of "
                f"two values at each place, use {_PAIRWISE[name]}(a, b)"
            )
        raise ValueError(f"expr {expr!r}: {name}(...) takes one value, then by= and where=")
    for kw in node.keywords:
        if kw.arg not in _REDUCER_KEYWORDS:
            raise ValueError(
                f"expr {expr!r}: {kw.arg or '**'}= is not a keyword of "
                f"{name}(...); it takes by= and where="
            )
    by = _keyword(node, "by")
    if by is None:
        raise ValueError(
            f"expr {expr!r}: {name}(...) needs by=structure, by=element, by=atom or by=all"
        )
    if isinstance(by, ast.Tuple) and [getattr(e, "id", None) for e in by.elts] == ["element", "structure"]:
        raise ValueError(f"expr {expr!r}: write by={PAIR}, structure first")
    if _by_key(by) is None:
        raise ValueError(
            f"expr {expr!r}: by= takes structure, element, atom, all or {PAIR}"
        )


def _by_key(node) -> str | None:
    """The by= word of a by= value node: ``structure``, ``element``, ``all``
    or ``PAIR``; None for anything else."""
    if isinstance(node, ast.Name) and node.id in BY_WORDS:
        return node.id
    if isinstance(node, ast.Tuple) and [getattr(e, "id", None) for e in node.elts] == ["structure", "element"]:
        return PAIR
    return None


def _check_element(node: ast.Call, expr: str) -> None:
    """``element("H", "C")``: one or more element symbols in quotes."""
    if node.keywords:
        raise ValueError(f"expr {expr!r}: keyword arguments are not allowed in 'element(...)'")
    if not node.args or not all(
            isinstance(a, ast.Constant) and isinstance(a.value, str) for a in node.args):
        raise ValueError(
            f'expr {expr!r}: element(...) takes element symbols in quotes, such '
            f'as element("H") or element("C", "O")'
        )
    known = element_numbers()
    for a in node.args:
        if a.value not in known:
            raise ValueError(f'expr {expr!r}: "{a.value}" is not an element symbol')


def _symbol_nodes(tree) -> set[int]:
    """ids of the text nodes that are symbols inside ``element(...)``."""
    return {
        id(a) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == _ELEMENT
        for a in n.args
    }


def _by_word_nodes(tree) -> set[int]:
    """ids of the ``structure`` / ``element`` / ``all`` words after ``by=``,
    those of the pair included."""
    return {
        id(word) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _REDUCERS
        for kw in n.keywords if kw.arg == "by"
        for word in (kw.value.elts if isinstance(kw.value, ast.Tuple) else [kw.value])
    }


_ALLOWED_FUNCS = set(_FUNCS) | _PART_FUNCS | _REDUCERS | {_ELEMENT}


def parse_and_validate(expr: str) -> ast.Expression:
    """Parse ``expr`` in eval mode and reject anything outside the whitelist.

    Permits only ``Expression``, ``BinOp`` (+ - * / **), ``UnaryOp`` (unary -),
    a single comparison (> < >= <= ==), numeric ``Constant``, ``Name`` (bound
    vars / ``n_atoms`` / ``pi``), a ``Subscript`` by whole-number literals (a
    position over the part axes, ``F[2]``, ``S[0,1]``), and ``Call`` to a
    whitelisted function. Attribute access, comprehensions, boolean operators,
    lambdas, etc. raise ``ValueError`` with a precise message (errors at
    config-load, ADR 0042 / 0057).
    """
    if len(expr) > MAX_FORMULA_CHARS:
        raise ValueError(
            f"the formula is {len(expr):,} characters long; the limit is "
            f"{MAX_FORMULA_CHARS:,}"
        )
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"expr {expr!r} is not a valid expression: {e.msg}") from e
    depth = _depth(tree.body)
    if depth > MAX_FORMULA_DEPTH:
        raise ValueError(
            f"the formula is nested {depth} levels deep; the limit is "
            f"{MAX_FORMULA_DEPTH} (each bracket, function and operator adds a level)"
        )

    symbols = _symbol_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expression):
            continue
        if isinstance(node, ast.keyword):
            continue  # checked with its call below
        # Operator marker nodes (ast.Add / ast.USub / ...) are children of a
        # BinOp/UnaryOp already validated below; skip them here.
        if isinstance(node, (ast.operator, ast.unaryop, ast.cmpop)):
            continue
        if isinstance(node, ast.BoolOp):
            raise ValueError(
                f"expr {expr!r}: boolean operator (and/or) is not allowed "
                f"(permitted: + - * / **)"
            )
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                raise ValueError(
                    f"expr {expr!r}: chained comparison is not allowed "
                    f"(write one comparison per where(...))"
                )
            if type(node.ops[0]) not in _CMPOPS:
                raise ValueError(
                    f"expr {expr!r}: comparison '{type(node.ops[0]).__name__}' is "
                    f"not allowed (permitted: > < >= <= ==)"
                )
            continue
        if isinstance(node, ast.Subscript):
            _positions(node, expr)  # raises on anything but whole-number literals
            continue
        if isinstance(node, ast.Tuple):
            continue  # a Subscript slice or the by= pair; checked with those
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _BINOPS:
                raise ValueError(
                    f"expr {expr!r}: operator '{type(node.op).__name__}' is not "
                    f"allowed (permitted: + - * / **)"
                )
            continue
        if isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARYOPS:
                raise ValueError(
                    f"expr {expr!r}: unary operator '{type(node.op).__name__}' is "
                    f"not allowed (permitted: unary -)"
                )
            continue
        if isinstance(node, ast.Constant):
            if id(node) in symbols:
                continue  # checked with element(...) below
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(
                    f"expr {expr!r}: only numeric literals are allowed, not "
                    f"{node.value!r}"
                )
            continue
        if isinstance(node, ast.Name):
            continue
        if isinstance(node, ast.Load):
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError(f"expr {expr!r}: only direct function calls are allowed")
            if node.func.id not in _ALLOWED_FUNCS:
                raise ValueError(
                    f"expr {expr!r}: function '{node.func.id}' is not allowed "
                    f"(permitted: {', '.join(sorted(_ALLOWED_FUNCS))})"
                )
            if node.func.id in _REDUCERS:
                _check_reduction(node, expr)
                continue
            if node.func.id == _ELEMENT:
                _check_element(node, expr)
                continue
            if node.keywords:
                raise ValueError(
                    f"expr {expr!r}: keyword arguments are not allowed in "
                    f"'{node.func.id}(...)'"
                )
            continue
        raise ValueError(
            f"expr {expr!r}: {type(node).__name__} is not allowed "
            f"(no attribute access, comprehensions, or lambdas)"
        )
    return tree


def _positions(node: ast.Subscript, expr: str) -> tuple[int, ...]:
    """The whole-number literals of a position ``F[2]`` / ``S[0,1]``."""
    sl = node.slice
    items = sl.elts if isinstance(sl, ast.Tuple) else [sl]
    out = []
    for it in items:
        if (not isinstance(it, ast.Constant) or isinstance(it.value, bool)
                or not isinstance(it.value, int) or it.value < 0):
            raise ValueError(
                f"expr {expr!r}: a position must be whole-number literals "
                f"such as F[2] or S[0,1] (no negatives, slices or names)"
            )
        out.append(it.value)
    return tuple(out)


def _value_names(tree: ast.Expression) -> set[str]:
    """Names referenced as *values* (excludes the name of a called function
    and the word after ``by=``)."""
    func_nodes = {
        id(n.func) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    } | _by_word_nodes(tree)
    return {
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and id(n) not in func_nodes
    }


# --------------------------------------------------------------------------- #
# Shape resolution + same-shape check
# --------------------------------------------------------------------------- #
def _as_tuple(shape):
    return shape if isinstance(shape, tuple) else (shape,)


def resolve_ref_shape(ref: str, registry) -> tuple:
    """The Metric Shape an input ref has (see ``_resolve_ref_shape``)."""
    return _resolve_ref_shape(ref, registry)


def _is_scalar_shape(shape) -> bool:
    return _as_tuple(shape) == (dims.scalar,)


def _resolve_ref_shape(ref: str, registry):
    """Resolve a Metric Input ref → its Metric Shape tuple (for the same-shape
    check). A registered metric contributes its declared shape; a Dataset Field
    contributes per-atom/per-frame; a raw ref its fixed shape; ``n_atoms`` is
    per-structure. An unresolvable ref raises (bad ref → config-load error)."""
    if registry.has(ref) or (hasattr(registry, "ensure") and registry.ensure(ref)):
        return _as_tuple(registry.get(ref)[0].shape)
    if ref in _RESERVED_SHAPE:
        return _RESERVED_SHAPE[ref]
    parsed = parse_field_ref(ref)
    if parsed is not None:
        _side, kind, _key = parsed
        return (dims.N_atoms,) if kind == "atoms" else (dims.N_frames,)
    if ref in _RAW_REF_SHAPE:
        return _RAW_REF_SHAPE[ref]
    from ffast.metrics.retired import retired_message
    if retired_message(ref):
        raise ValueError(retired_message(ref))
    raise ValueError(
        f"cannot determine a Metric Shape for ref '{ref}' — expr variables must "
        f"bind a registered metric id, a Dataset Field "
        f"({{reference,prediction}}.{{info,atoms}}.<key>), or one of "
        f"{sorted(_RAW_REF_SHAPE)}"
    )


def _combine(shapes: list, expr: str):
    """Element-wise combination: scalars broadcast, every other shape must
    match, except that a value without parts applies to each part of one with
    the same rows: per atom with per atom × xyz (ADR 0065 section 8)."""
    non_scalar = [tuple(sh) for sh in shapes if not _is_scalar_shape(sh)]
    widest = max(non_scalar, key=len, default=None)
    if widest is not None and _part_axes(widest):
        rows = widest[:-1]
        non_scalar = [widest if sh == rows else sh for sh in non_scalar]
    distinct = set(non_scalar)
    if len(distinct) > 1:
        detail = " vs ".join(dims.shape_to_str(sh) for sh in sorted(distinct, key=str))
        raise ValueError(
            f"expr {expr!r}: operands have different Metric Shapes ({detail}); an "
            f"Expression Metric is element-wise, so all non-scalar operands must "
            f"share one shape"
        )
    return non_scalar[0] if non_scalar else (dims.scalar,)


_LEAD_WORDS = {
    dims.N_atoms: "per atom",
    dims.N_frames: "per structure",
    dims.N_elements: "per element",
    dims.scalar: "one number",
}


# The result of by=(structure, element): a row per structure, a column per element.
TABLE = (dims.N_frames, dims.N_elements)


def is_table(shape) -> bool:
    """Whether ``shape`` is a structures × elements table (a vector part or not)."""
    return _as_tuple(shape)[:2] == TABLE


def shape_words(shape) -> str:
    """A shape in plain words: ``per atom``, ``per structure, a vector``,
    ``per structure and element``, ``one number``."""
    shape = _as_tuple(shape)
    if shape[0] in _PART_AXES:
        return "one vector" if shape[0] == dims.xyz else "one tensor"
    if is_table(shape):
        words, shape = "per structure and element", shape[1:]
    else:
        words = _LEAD_WORDS.get(shape[0], dims.shape_to_str(shape))
    if len(shape) > 1:
        words += ", a vector" if shape[-1] == dims.xyz else ", a tensor"
    return words


# The leading axes a reduction can combine over (rows), by= word → result row.
_ROWS = (dims.N_atoms, dims.N_frames, dims.N_elements)
_BY_RESULT = {"structure": (dims.N_frames,), "element": (dims.N_elements,),
              "atom": (dims.N_atoms,), PAIR: TABLE}


def _reduction_shape(node: ast.Call, go, expr: str) -> tuple:
    """Shape of ``mean(x, by=..., where=...)`` (ADR 0064 section 1): ``by=``
    alone decides it; the parts of a vector are kept."""
    value = node.args[0]
    shape = tuple(go(value))
    lead, parts = shape[0], shape[1:]
    by = _by_key(_keyword(node, "by"))
    if is_table(shape):
        raise ValueError(
            f"expr {expr!r}: {ast.unparse(value)} is per structure and element, "
            f"which can't be reduced again yet"
        )
    if by in _BY_RESULT:
        if lead != dims.N_atoms:
            raise ValueError(
                f"expr {expr!r}: by={by} needs a per-atom value; "
                f"{ast.unparse(value)} is {shape_words(shape)}"
            )
        out = (*_BY_RESULT[by], *parts)
    else:
        if lead not in _ROWS:
            raise ValueError(
                f"expr {expr!r}: by=all needs a per-atom, per-structure or "
                f"per-element value; {ast.unparse(value)} is {shape_words(shape)}"
            )
        out = tuple(parts) or (dims.scalar,)
    where = _keyword(node, "where")
    if where is not None:
        where_shape = tuple(go(where))
        if where_shape != (lead,):
            raise ValueError(
                f"expr {expr!r}: where= must have the input's shape: "
                f"{shape_words((lead,))} here, {ast.unparse(where)} is "
                f"{shape_words(where_shape)}"
            )
    return out


def infer_shape(tree: ast.Expression, var_shapes: dict, expr: str = "") -> tuple:
    """Output Metric Shape of a validated expr, given each variable's shape.

    Walks the tree: scalars broadcast, other operands must share one shape,
    ``norm`` / ``x`` / ``y`` / ``z`` / positions consume the trailing part axes.
    Raises ``ValueError`` for a shape mismatch, a part function on an input with
    no such part, or an out-of-range position.
    """
    def go(node):
        if isinstance(node, ast.Expression):
            return go(node.body)
        if isinstance(node, ast.Constant):
            return (dims.scalar,)
        if isinstance(node, ast.Name):
            return (dims.scalar,) if node.id in _CONSTS else tuple(var_shapes[node.id])
        if isinstance(node, ast.UnaryOp):
            return go(node.operand)
        if isinstance(node, ast.BinOp):
            return _combine([go(node.left), go(node.right)], expr)
        if isinstance(node, ast.Compare):
            return _combine([go(node.left), go(node.comparators[0])], expr)
        if isinstance(node, ast.Subscript):
            inner = go(node.value)
            sizes = _part_axes(inner)
            pos = _positions(node, expr)
            if not sizes:
                raise ValueError(
                    f"expr {expr!r}: a position needs an input with part axes "
                    f"(vector, 3x3, Voigt); this one is {dims.shape_to_str(inner)}"
                )
            if len(pos) != len(sizes):
                raise ValueError(
                    f"expr {expr!r}: this input needs {len(sizes)} position "
                    f"number(s), got {len(pos)}"
                )
            if any(p >= n for p, n in zip(pos, sizes)):
                raise ValueError(
                    f"expr {expr!r}: position {list(pos)} is out of range for "
                    f"axes of size {list(sizes)}"
                )
            return inner[:-1] or (dims.scalar,)
        if isinstance(node, ast.Call):
            name = node.func.id
            if name in _REDUCERS:
                return _reduction_shape(node, go, expr)
            if name == _ELEMENT:
                return (dims.N_atoms,)
            shapes = [go(a) for a in node.args]
            if name in _PART_FUNCS:
                if len(shapes) != 1:
                    raise ValueError(f"expr {expr!r}: {name}(...) takes one argument")
                sh = shapes[0]
                if name in _COLLAPSING and not _part_axes(sh):
                    raise ValueError(
                        f"expr {expr!r}: {name} needs an input with part axes "
                        f"(vector, 3x3, Voigt); this one is {dims.shape_to_str(sh)}"
                    )
                if name not in _COLLAPSING and sh[-1] != dims.xyz:
                    raise ValueError(
                        f"expr {expr!r}: {name}(...) needs a vector input "
                        f"(shape ending in xyz); this one is {dims.shape_to_str(sh)}"
                    )
                return sh[:-1] or (dims.scalar,)
            return _combine(shapes, expr)
        raise ValueError(f"expr {expr!r}: cannot infer a shape for {type(node).__name__}")

    return go(tree)


# --------------------------------------------------------------------------- #
# Picklable evaluator
# --------------------------------------------------------------------------- #
_AST_CACHE: dict[str, ast.Expression] = {}


class _ExprFn:
    """Picklable evaluator for one Expression Metric.

    Carries the ``expr`` string and the var→ref map as data (no closure) so a
    compiled expr metric — and the whole registry — pickles to the metric worker
    pool (precedent: ``_TransformFn``, transforms.py). The executor calls
    ``fn(**resolved_inputs)`` keyed by each Expression Variable's local name; the
    AST is compiled once (cached by string) and evaluated over those arrays.

    "No value" (ADR 0064 section 8) travels beside each value as its own mask
    over the rows, so a bad value (inf, NaN from 0/0) is never mistaken for
    one. It comes from a reduction's empty group, or from an input that is a
    formula metric (``gap_inputs``), whose NaN can mean nothing else.
    """

    def __init__(self, expr: str, var_refs: dict[str, str],
                 var_parts: dict[str, int] | None = None,
                 gap_inputs=()) -> None:
        self.expr = expr
        # sorted for a stable implementation_source regardless of dict order
        self.var_refs = dict(sorted(var_refs.items()))
        # name → number of trailing part axes (vector 1, Voigt 1, 3x3 2); the
        # compiler fills it from the input shapes. Absent = no part axes.
        self.var_parts = dict(var_parts or {})
        # inputs whose NaN is "no value": those bound to a formula metric
        self.gap_inputs = frozenset(gap_inputs)

    @property
    def user_vars(self) -> dict[str, str]:
        """The variables the user named: without ``n_atoms`` and the hidden
        offsets / elements inputs."""
        return {n: r for n, r in self.var_refs.items()
                if n not in RESERVED_VARS and n not in HIDDEN_INPUTS}

    def _tree(self) -> ast.Expression:
        tree = _AST_CACHE.get(self.expr)
        if tree is None:
            tree = parse_and_validate(self.expr)
            _AST_CACHE[self.expr] = tree
        return tree

    def _eval(self, node, ns):
        """Returns ``(value, k, gap)``: the array, its count of trailing part
        axes, and the rows that have no value (a bool array over the value's
        shape without its part axes), or None when every row has one."""
        if isinstance(node, ast.Expression):
            return self._eval(node.body, ns)
        if isinstance(node, ast.Constant):
            return node.value, 0, None
        if isinstance(node, ast.Name):
            if node.id in _CONSTS:
                return _CONSTS[node.id], 0, None
            return self._input(node.id, ns)
        if isinstance(node, ast.BinOp):
            (a, ka, ga), (b, kb, gb) = self._eval(node.left, ns), self._eval(node.right, ns)
            a, b = _align([(a, ka), (b, kb)])
            return _BINOPS[type(node.op)](a, b), max(ka, kb), _either(ga, gb)
        if isinstance(node, ast.UnaryOp):
            a, k, g = self._eval(node.operand, ns)
            return _UNARYOPS[type(node.op)](a), k, g
        if isinstance(node, ast.Compare):
            (a, ka, ga), (b, kb, gb) = self._eval(node.left, ns), self._eval(node.comparators[0], ns)
            a, b = _align([(a, ka), (b, kb)])
            return _CMPOPS[type(node.ops[0])](a, b), max(ka, kb), _either(ga, gb)
        if isinstance(node, ast.Subscript):
            a, k, g = self._eval(node.value, ns)
            pos = _positions(node, self.expr)
            return np.asarray(a)[(..., *pos)], 0, g
        if isinstance(node, ast.Call):
            name = node.func.id
            if name in _REDUCERS:
                return self._reduce(node, ns)
            if name == _ELEMENT:
                wanted = [element_numbers()[a.value] for a in node.args]
                return np.isin(np.asarray(ns[_ELEMENTS]), wanted).astype(float), 0, None
            evald = [self._eval(a, ns) for a in node.args]
            if name in _COLLAPSING:
                a, k, g = evald[0]
                arr = np.asarray(a, dtype=float)
                axes = tuple(range(-k, 0))
                if name == "norm":
                    return np.sqrt(np.sum(arr * arr, axis=axes)), 0, g
                return _PART_REDUCERS[name](arr, axis=axes), 0, g
            if name in _XYZ_INDEX:
                a, _k, g = evald[0]
                return np.asarray(a)[..., _XYZ_INDEX[name]], 0, g
            gap = None
            for _v, _k, g in evald:
                gap = _either(gap, g)
            return (_FUNCS[name](*_align([(v, k) for v, k, _ in evald])),
                    max((k for _, k, _ in evald), default=0), gap)
        raise ValueError(f"expr {self.expr!r}: cannot evaluate {type(node).__name__}")

    def _input(self, name, ns):
        value, k = ns[name], self.var_parts.get(name, 0)
        if name not in self.gap_inputs:
            return value, k, None
        arr = np.asarray(value, dtype=float)
        gap = np.isnan(arr)
        if k:
            gap = gap.any(axis=tuple(range(-k, 0)))
        return arr, k, (gap if gap.any() else None)

    def _reduce(self, node, ns):
        """``mean(x, by=..., where=...)`` and its siblings (ADR 0064): group
        the rows, leave out those ``where=`` drops and those with no value,
        and combine each group with the per-structure sums of segments.py. A
        group with nothing left has no value; a bad value in a counted row
        makes its group bad."""
        name = node.func.id
        value, k, gap = self._eval(node.args[0], ns)
        v = np.asarray(value, dtype=float)
        rows = v.shape[0]
        counted = np.ones(rows, dtype=bool) if gap is None else ~np.broadcast_to(gap, (rows,))
        where = _keyword(node, "where")
        if where is not None:
            w, _wk, wgap = self._eval(where, ns)
            w = np.broadcast_to(np.asarray(w, dtype=float), (rows,))
            # A bad value in where= is neither 1 nor 0: the row counts and is
            # made bad, so its group fails rather than being quietly decided.
            w_bad = ~np.isfinite(w)
            left_out = counted & (w == 0)
            self._note_ignored(v, k, left_out, ns)
            counted = counted & ((w != 0) | w_bad)
            if wgap is not None:
                counted = counted & ~np.broadcast_to(wgap, (rows,))
            if w_bad.any():
                v = np.where(w_bad.reshape((rows,) + (1,) * k), np.nan, v)

        by = _by_key(_keyword(node, "by"))
        order = grid = None
        if by == "structure":
            offsets = framesOrOne(ns.get(_OFFSETS), v)
        elif by == "atom":
            # Group i: atom i of every structure.
            frames = framesOrOne(ns.get(_OFFSETS), v)
            _check_same_atoms(frames, ns.get(_ELEMENTS))
            size = int(frames[1] - frames[0]) if frames.size > 1 else 0
            index = np.arange(rows) - np.repeat(frames[:-1], np.diff(frames))
            order = np.argsort(index, kind="stable")
            offsets = np.concatenate([[0], np.cumsum(np.bincount(index, minlength=size))])
        elif by in ("element", PAIR):
            # Columns: every element of the dataset, in order of atomic number.
            z = np.asarray(ns[_ELEMENTS])
            columns = np.unique(z)
            index = np.searchsorted(columns, z)
            size = columns.size
            if by == PAIR:
                # One group per (structure, element): structure i's column j
                # is group i * columns + j.
                frames = framesOrOne(ns.get(_OFFSETS), v)
                grid = (frames.size - 1, columns.size)
                index = np.repeat(np.arange(grid[0]), np.diff(frames)) * columns.size + index
                size = grid[0] * grid[1]
            order = np.argsort(index, kind="stable")
            offsets = np.concatenate([[0], np.cumsum(np.bincount(index, minlength=size))])
        else:
            offsets = np.array([0, rows])
        if order is not None:
            v, counted = v[order], counted[order]

        keep = counted.reshape((rows,) + (1,) * k)
        n = segmentSums(counted.astype(float), offsets)
        empty = n == 0
        per_group = n.reshape(n.shape + (1,) * k)
        with np.errstate(invalid="ignore", divide="ignore"):
            if name in ("min", "max"):
                fill = np.inf if name == "min" else -np.inf
                ufunc = np.minimum if name == "min" else np.maximum
                out = segmentExtremes(np.where(keep, v, fill), offsets, ufunc)
                # The fill would hide an infinity among the counted rows: a
                # group holding any bad value is bad.
                bad = segmentSums(np.where(keep, ~np.isfinite(v), False).astype(float), offsets)
                out = np.where(bad > 0, np.nan, out)
            else:
                sums = segmentSums(np.where(keep, v, 0.0), offsets)
                if name == "sum":
                    out = sums
                elif name == "mean":
                    out = sums / per_group
                elif name == "rms":
                    out = np.sqrt(segmentSums(np.where(keep, v * v, 0.0), offsets) / per_group)
                else:  # std: about each group's mean, divided by N (np.std)
                    mean = sums / per_group
                    group_of_row = np.repeat(np.arange(n.size), np.diff(offsets))
                    dev = np.where(keep, v - mean[group_of_row], 0.0)
                    out = np.sqrt(segmentSums(dev * dev, offsets) / per_group)
        if by == "all":
            out, empty = out[0], empty[0]
        elif grid is not None:
            out, empty = out.reshape(grid + out.shape[1:]), empty.reshape(grid)
        return out, k, (empty if np.any(empty) else None)

    def _note_ignored(self, v, k, left_out, ns) -> None:
        """Record the bad values in rows where= leaves out, with their frames
        when the rows can be placed (per atom or per structure)."""
        bad = ~np.isfinite(v)
        if k:
            bad = bad.any(axis=tuple(range(-k, 0)))
        rows = np.flatnonzero(left_out & bad)
        if not rows.size:
            return
        offsets = ns.get(_OFFSETS)
        frames = []
        if offsets is not None:
            offsets = np.asarray(offsets)
            if v.shape[0] == offsets[-1]:            # per atom
                frames = np.searchsorted(offsets, rows, side="right") - 1
            elif v.shape[0] == offsets.size - 1:     # per structure
                frames = rows
        ns[_IGNORED].append((int(rows.size), [int(f) for f in frames]))

    def evaluate_split(self, /, **kwargs):
        """``(values, gap, ignored)``: the formula's values, NaN where there is
        no value; the rows with no value (or None); and the bad values where=
        left out, ``{"count", "frames"}`` (or None). Bad values elsewhere are
        left in; a metric run rejects them in ``__call__``, Try counts them."""
        ns = {**kwargs, _IGNORED: []}
        out, k, gap = self._eval(self._tree(), ns)
        ignored = None
        if ns[_IGNORED]:
            frames = sorted({f for _n, fs in ns[_IGNORED] for f in fs})
            ignored = {"count": sum(n for n, _fs in ns[_IGNORED]), "frames": frames}
        arr = np.asarray(out)
        arr = arr.astype(float) if arr.dtype == bool else arr
        if gap is not None:
            gap = np.broadcast_to(gap, arr.shape[:arr.ndim - k])
            arr = np.array(arr, dtype=float)
            arr[gap] = np.nan
        return arr, gap, ignored

    def evaluate(self, /, **kwargs):
        """The formula over these inputs: NaN where there is no value, bad
        values left in (a bare comparison gives 1.0 / 0.0)."""
        return self.evaluate_split(**kwargs)[0]

    def __call__(self, /, **kwargs):
        # ``self`` is positional-only so an Expression Variable legitimately named
        # ``self`` (bound via ``fn(**resolved_inputs)``) cannot collide with it.
        arr, gap, ignored = self.evaluate_split(**kwargs)
        bad = ~np.isfinite(arr)
        if gap is not None:
            bad &= ~gap.reshape(gap.shape + (1,) * (arr.ndim - gap.ndim))
        if np.any(bad):
            # Decision 7: a Metric never returns silent inf/nan. Raising here
            # surfaces as a Metric Failure naming this metric (per the executor).
            # "No value" (ADR 0064) is not bad: it is NaN in the result.
            raise ValueError(
                f"expr {self.expr!r} produced non-finite values "
                f"(inf/nan) — check for division by zero or log of a "
                f"non-positive value in the data"
            )
        if ignored:
            return WithWarnings(arr, [ignored_warning(ignored["count"], ignored["frames"])])
        return arr

    def implementation_source(self) -> str:
        binding = ",".join(f"{k}={v}" for k, v in self.var_refs.items())
        return f"_ExprFn:{self.expr}|{binding}|v{_WHITELIST_VERSION}"

    def __repr__(self) -> str:
        return f"_ExprFn({self.expr!r})"


def _align(values):
    """Values ready for numpy: one without parts gets a trailing axis per part
    of the widest, so each atom's value applies to all its parts."""
    k = max(kv for _v, kv in values)
    return [np.asarray(v).reshape(np.shape(v) + (1,) * (k - kv))
            if kv < k and np.ndim(v) > 0 else v for v, kv in values]


def _check_same_atoms(frames, elements) -> None:
    """``by=atom`` groups atom i of every structure, so every structure must
    have the same atoms in the same order (ADR 0058 "uniform")."""
    sizes = np.diff(frames)
    same = sizes.size == 0 or bool(np.all(sizes == sizes[0]))
    if same and elements is not None and sizes.size > 1:
        z = np.asarray(elements).reshape(sizes.size, sizes[0])
        same = bool(np.all(z == z[0]))
    if not same:
        raise ValueError(
            "by=atom needs structures with the same atoms in the same order; "
            "the structures of this dataset differ")


def _either(a, b):
    """Rows with no value in either operand."""
    if a is None:
        return b
    if b is None:
        return a
    return np.logical_or(a, b)


# --------------------------------------------------------------------------- #
# Compiler
# --------------------------------------------------------------------------- #
def analyze_expr(expr, vars, *, registry=None, id="<expr>"):
    """Check an expr against its vars without registering anything.

    Returns ``(inputs, output_shape, var_parts)``: the name→ref map to register,
    the output Metric Shape, and each variable's count of trailing part axes.
    Raises ``ValueError`` for any error ``compile_expr_metric`` would raise on the
    formula itself. Touches no data (ADR 0057 rule 11).
    """
    registry = registry if registry is not None else default_registry
    if "n_atoms" in vars:
        raise ValueError(
            f"expr metric '{id}': 'n_atoms' is a reserved Expression Variable "
            f"(auto-provided per-structure atom count) — rename the vars entry"
        )
    tree = parse_and_validate(expr)
    used = _value_names(tree) - set(_CONSTS)
    hidden = sorted(used & set(HIDDEN_INPUTS))
    if hidden:
        raise ValueError(
            f"expr metric '{id}': {hidden[0]!r} is a name FFAST keeps for itself — "
            f"rename the variable"
        )
    unknown = used - set(vars) - RESERVED_VARS
    if unknown:
        raise ValueError(
            f"expr metric '{id}': unknown identifier(s) {sorted(unknown)} — every "
            f"name must be a declared var, the reserved 'n_atoms', or a constant "
            f"({', '.join(sorted(_CONSTS))})"
        )

    # Resolve each used variable's ref + Metric Shape (bad ref raises here).
    inputs: dict[str, str] = {}
    shapes: dict[str, tuple] = {}
    for name in sorted(used):
        ref = "n_atoms" if name in RESERVED_VARS else vars[name]
        inputs[name] = ref
        shapes[name] = _resolve_ref_shape(ref, registry)

    try:
        output_shape = infer_shape(tree, shapes, expr)
    except ValueError as e:
        raise ValueError(f"expr metric '{id}': {e}") from e
    var_parts = {n: len(_part_axes(sh)) for n, sh in shapes.items() if _part_axes(sh)}
    words = by_words(tree)
    # where= needs the offsets too, to name the frames of what it leaves out.
    if words & {"structure", "atom", PAIR} or _uses_where(tree):
        inputs[_OFFSETS] = HIDDEN_INPUTS[_OFFSETS]
    if words & {"element", "atom", PAIR} or _uses_element(tree):
        inputs[_ELEMENTS] = HIDDEN_INPUTS[_ELEMENTS]
    return inputs, output_shape, var_parts


def by_words(tree: ast.Expression) -> set[str]:
    """The ``by=`` words a parsed formula uses anywhere; the pair is ``PAIR``."""
    return {
        _by_key(kw.value) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _REDUCERS
        for kw in n.keywords if kw.arg == "by"
    } - {None}


def _uses_where(tree: ast.Expression) -> bool:
    return any(kw.arg == "where" for n in ast.walk(tree) if isinstance(n, ast.Call)
               for kw in n.keywords)


def _uses_element(tree: ast.Expression) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == _ELEMENT
               for n in ast.walk(tree))


# The by= words that mean "the whole dataset". Colouring reads a metric over
# the whole dataset; when only the frame on screen can be computed, they would
# mean "this frame" there (ADR 0064 section 5, amendment 2026-10-24). The pair
# is not one: each of its rows is one structure (amendment 2026-10-24).
# by=atom averages over every structure (ADR 0065).
_DATASET_WIDE = ("all", "element", "atom")


def formula_by_words(expr: str, refs=(), registry=None) -> set[str]:
    """The by= words a formula uses anywhere: itself, or in a formula metric
    it takes as an input, however deep."""
    words = set(by_words(parse_and_validate(expr)))
    seen: set[str] = set()
    todo = [r for r in refs if registry is not None and registry.has(r)]
    while todo:
        ref = todo.pop()
        if ref in seen:
            continue
        seen.add(ref)
        fn = registry.get(ref)[1]
        if isinstance(fn, _ExprFn):
            words |= by_words(fn._tree())
            todo.extend(r for r in fn.var_refs.values() if registry.has(r))
    return words


BY_ATOM_DIFFERENT = ("by=atom needs structures with the same atoms in the same order; "
                     "this dataset's structures differ")


def dataset_warnings(expr: str, refs=(), registry=None, *, uniform: bool | None = None) -> list[str]:
    """What Check warns of for the dataset open in the pane (ADR 0066 rule 5):
    sentences, not refusals, since the formula may be right for another
    dataset. ``uniform`` None (no dataset) warns of nothing."""
    if uniform is False and "atom" in formula_by_words(expr, refs, registry):
        return [BY_ATOM_DIFFERENT]
    return []


def colour_problem(expr: str, refs=(), registry=None) -> str | None:
    """Why this formula cannot colour atoms from the frame on screen alone,
    or None. A formula that uses by=all or by=element anywhere, itself or in a
    formula metric it takes as an input, would give one number in Try and
    another on screen."""
    words = formula_by_words(expr, refs, registry)
    for word in _DATASET_WIDE:
        if word in words:
            return (f"by={word} colours atoms only when the prediction covers every "
                    f"frame; this one covers the frame on screen")
    return None


def metric_colour_problem(metric_id: str, registry) -> str | None:
    """``colour_problem`` of a registered metric; None for a Python metric."""
    fn = registry.get(metric_id)[1]
    if not isinstance(fn, _ExprFn):
        return None
    return colour_problem(fn.expr, fn.var_refs.values(), registry)


def formula_inputs(inputs: dict[str, str], registry) -> set[str]:
    """The variables bound to a formula metric: their NaN is "no value",
    never a bad value (ADR 0064 section 8). A Python metric's NaN stays bad."""
    return {name for name, ref in inputs.items()
            if registry.has(ref) and isinstance(registry.get(ref)[1], _ExprFn)}


def compile_expr_metric(id, expr, vars, *, label="", unit="", registry=None) -> str:
    """Compile one ``[[metrics.expr]]`` entry → a registered Metric id. Idempotent.

    ``vars`` maps each Expression Variable to a Metric Input ref (raw ref,
    Dataset Field, or metric id). ``n_atoms`` is auto-provided when referenced;
    a ``vars`` key named ``n_atoms`` raises (Decision 4). The output Metric Shape
    comes from ``infer_shape``: the shared shape of the non-scalar operands
    (scalars broadcast), less any part axes ``norm`` / ``x`` / positions consume
    (Decisions 2, 3; ADR 0057 rule 17).
    """
    registry = registry if registry is not None else default_registry
    if "." not in id:
        raise ValueError(f"expr metric id '{id}' must be namespaced (contain a dot)")
    if "n_atoms" in vars:
        raise ValueError(
            f"expr metric '{id}': 'n_atoms' is a reserved Expression Variable "
            f"(auto-provided per-structure atom count) — rename the vars entry"
        )
    if registry.has(id):
        return id  # idempotent: same compile pass runs on server/client/headless

    inputs, output_shape, var_parts = analyze_expr(
        expr, vars, registry=registry, id=id)
    decorator = registry.metric(
        id=id,
        label=label or id,
        inputs=inputs,
        shape=output_shape,
        unit=unit or units.dimensionless,
    )
    decorator(_ExprFn(expr, inputs, var_parts, formula_inputs(inputs, registry)))
    return id


def compile_expr_metrics(configs, registry=None) -> list[str]:
    """Compile a list of ExprMetricConfig (or dict-likes) → registered ids."""
    out = []
    for c in configs:
        out.append(
            compile_expr_metric(
                c.id, c.expr, dict(c.vars),
                label=getattr(c, "label", ""), unit=getattr(c, "unit", ""),
                registry=registry,
            )
        )
    return out
