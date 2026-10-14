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

* **Element-wise only.** The function whitelist is shape-preserving; no reducers
  — all reduction stays in the Transform layer.
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
from ffast.metrics.inputs import parse_field_ref
from ffast.metrics.registry import default_registry

# Bump when the whitelist (functions / operators / constants) changes, so every
# Expression Metric's cache identity invalidates (rides implementation_source).
_WHITELIST_VERSION = "2"

# Element-wise (shape-preserving) numpy functions only — no reducers. minimum/
# maximum/clip/where are element-wise, not the reducing min/max (Decision 6).
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
_PART_FUNCS = {"norm", "x", "y", "z"}
_XYZ_INDEX = {"x": 0, "y": 1, "z": 2}

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

# Raw (non-metric, non-field) input refs → their Metric Shape, for the same-shape
# check. Restricted to the continuous float quantities that element-wise algebra
# is meaningful over: stress/selection are not sourced yet (InputResolver returns
# None), and ``reference.elements`` (integer atomic numbers) is a categorical
# label, not a continuous quantity — all are intentionally absent and rejected as
# expr variables in v1.
_RAW_REF_SHAPE = {
    "reference.energies":  (dims.N_frames,),
    "prediction.energies": (dims.N_frames,),
    "reference.forces":    (dims.N_atoms, dims.xyz),
    "prediction.forces":   (dims.N_atoms, dims.xyz),
    "reference.positions": (dims.N_atoms, dims.xyz),
    "reference.masses":    (dims.N_atoms,),
}


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
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"expr {expr!r} is not a valid expression: {e.msg}") from e

    for node in ast.walk(tree):
        if isinstance(node, ast.Expression):
            continue
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
            continue  # only reachable as a Subscript slice; checked above
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
            if node.func.id not in _FUNCS and node.func.id not in _PART_FUNCS:
                raise ValueError(
                    f"expr {expr!r}: function '{node.func.id}' is not allowed "
                    f"(permitted: {', '.join(sorted(set(_FUNCS) | _PART_FUNCS))})"
                )
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
    """Names referenced as *values* (excludes the name of a called function)."""
    func_nodes = {
        id(n.func) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    return {
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and id(n) not in func_nodes
    }


# --------------------------------------------------------------------------- #
# Shape resolution + same-shape check
# --------------------------------------------------------------------------- #
def _as_tuple(shape):
    return shape if isinstance(shape, tuple) else (shape,)


def _is_scalar_shape(shape) -> bool:
    return _as_tuple(shape) == (dims.scalar,)


def _resolve_ref_shape(ref: str, registry):
    """Resolve a Metric Input ref → its Metric Shape tuple (for the same-shape
    check). A registered metric contributes its declared shape; a Dataset Field
    contributes per-atom/per-frame; a raw ref its fixed shape; ``n_atoms`` is
    per-structure. An unresolvable ref raises (bad ref → config-load error)."""
    if registry.has(ref):
        return _as_tuple(registry.get(ref)[0].shape)
    if ref in _RESERVED_SHAPE:
        return _RESERVED_SHAPE[ref]
    parsed = parse_field_ref(ref)
    if parsed is not None:
        _side, kind, _key = parsed
        return (dims.N_atoms,) if kind == "atoms" else (dims.N_frames,)
    if ref in _RAW_REF_SHAPE:
        return _RAW_REF_SHAPE[ref]
    raise ValueError(
        f"cannot determine a Metric Shape for ref '{ref}' — expr variables must "
        f"bind a registered metric id, a Dataset Field "
        f"({{reference,prediction}}.{{info,atoms}}.<key>), or one of "
        f"{sorted(_RAW_REF_SHAPE)}"
    )


def _combine(shapes: list, expr: str):
    """Element-wise combination: scalars broadcast, every other shape must match."""
    non_scalar = [tuple(sh) for sh in shapes if not _is_scalar_shape(sh)]
    distinct = set(non_scalar)
    if len(distinct) > 1:
        detail = " vs ".join(dims.shape_to_str(sh) for sh in sorted(distinct, key=str))
        raise ValueError(
            f"expr {expr!r}: operands have different Metric Shapes ({detail}); an "
            f"Expression Metric is element-wise, so all non-scalar operands must "
            f"share one shape"
        )
    return non_scalar[0] if non_scalar else (dims.scalar,)


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
            shapes = [go(a) for a in node.args]
            if name in _PART_FUNCS:
                if len(shapes) != 1:
                    raise ValueError(f"expr {expr!r}: {name}(...) takes one argument")
                sh = shapes[0]
                if name == "norm" and not _part_axes(sh):
                    raise ValueError(
                        f"expr {expr!r}: norm needs an input with part axes "
                        f"(vector, 3x3, Voigt); this one is {dims.shape_to_str(sh)}"
                    )
                if name != "norm" and sh[-1] != dims.xyz:
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
    """

    def __init__(self, expr: str, var_refs: dict[str, str],
                 var_parts: dict[str, int] | None = None) -> None:
        self.expr = expr
        # sorted for a stable implementation_source regardless of dict order
        self.var_refs = dict(sorted(var_refs.items()))
        # name → number of trailing part axes (vector 1, Voigt 1, 3x3 2); the
        # compiler fills it from the input shapes. Absent = no part axes.
        self.var_parts = dict(var_parts or {})

    def _tree(self) -> ast.Expression:
        tree = _AST_CACHE.get(self.expr)
        if tree is None:
            tree = parse_and_validate(self.expr)
            _AST_CACHE[self.expr] = tree
        return tree

    def _eval(self, node, ns):
        """Returns ``(value, k)``: the array and its count of trailing part axes."""
        if isinstance(node, ast.Expression):
            return self._eval(node.body, ns)
        if isinstance(node, ast.Constant):
            return node.value, 0
        if isinstance(node, ast.Name):
            if node.id in _CONSTS:
                return _CONSTS[node.id], 0
            return ns[node.id], self.var_parts.get(node.id, 0)
        if isinstance(node, ast.BinOp):
            (a, ka), (b, kb) = self._eval(node.left, ns), self._eval(node.right, ns)
            return _BINOPS[type(node.op)](a, b), max(ka, kb)
        if isinstance(node, ast.UnaryOp):
            a, k = self._eval(node.operand, ns)
            return _UNARYOPS[type(node.op)](a), k
        if isinstance(node, ast.Compare):
            (a, ka), (b, kb) = self._eval(node.left, ns), self._eval(node.comparators[0], ns)
            return _CMPOPS[type(node.ops[0])](a, b), max(ka, kb)
        if isinstance(node, ast.Subscript):
            a, k = self._eval(node.value, ns)
            pos = _positions(node, self.expr)
            return np.asarray(a)[(..., *pos)], 0
        if isinstance(node, ast.Call):
            name = node.func.id
            evald = [self._eval(a, ns) for a in node.args]
            if name == "norm":
                a, k = evald[0]
                arr = np.asarray(a, dtype=float)
                return np.sqrt(np.sum(arr * arr, axis=tuple(range(-k, 0)))), 0
            if name in _XYZ_INDEX:
                return np.asarray(evald[0][0])[..., _XYZ_INDEX[name]], 0
            return _FUNCS[name](*[v for v, _ in evald]), max((k for _, k in evald), default=0)
        raise ValueError(f"expr {self.expr!r}: cannot evaluate {type(node).__name__}")

    def __call__(self, /, **kwargs):
        # ``self`` is positional-only so an Expression Variable legitimately named
        # ``self`` (bound via ``fn(**resolved_inputs)``) cannot collide with it.
        out, _k = self._eval(self._tree(), kwargs)
        arr = np.asarray(out)
        if arr.dtype == bool:
            arr = arr.astype(float)  # a bare comparison yields 1.0 / 0.0
        if not np.all(np.isfinite(arr)):
            # Decision 7: a Metric never returns silent inf/nan. Raising here
            # surfaces as a Metric Failure naming this metric (per the executor).
            raise ValueError(
                f"expr {self.expr!r} produced non-finite values "
                f"(inf/nan) — check for division by zero or log of a "
                f"non-positive value in the data"
            )
        return arr

    def implementation_source(self) -> str:
        binding = ",".join(f"{k}={v}" for k, v in self.var_refs.items())
        return f"_ExprFn:{self.expr}|{binding}|v{_WHITELIST_VERSION}"

    def __repr__(self) -> str:
        return f"_ExprFn({self.expr!r})"


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
    return inputs, output_shape, var_parts


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
    decorator(_ExprFn(expr, inputs, var_parts))
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
