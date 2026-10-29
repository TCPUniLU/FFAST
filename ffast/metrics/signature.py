"""Signature-driven metric schema inference (DX layer over ``@metric``).

Lets a metric author declare *less*: inputs, compute parameters, output
shape, label and description are inferred from the function signature,
type annotations and docstring. Anything passed explicitly to ``@metric``
still wins, so existing fully-declared metrics are byte-identical.

Convention (matches the hand-written builtins):

- **Positional-or-keyword params before ``*`` → inputs.** The symbolic ref
  comes from the annotation: a bare ref string (``"reference.forces"``) or
  ``Ref[...]`` / ``Ref(..., optional=True)``. A positional param with a
  default (e.g. ``offsets=None``) is treated as an *optional* input.
- **Keyword-only params after ``*`` → compute parameters.** Type/default
  are read from the signature; ``Literal[...]`` → choice, ``bool``/``int``/
  ``float``/``str`` → the matching parameter kind. ``Annotated[t, P(...)]``
  carries min/max/role/label/scope/description.
- **Return annotation → shape.** A ``dims.Dim`` or a tuple of ``Dim``.
- **Docstring → label (first line) + description (rest).**
- **Id** ← ``METRIC_NAMESPACE`` module global (or ``__module__``) + func name,
  unless ``id=`` is given explicitly.

No third-party deps (works on Python 3.9; jaxtyping would need 3.10+).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional, get_args, get_origin

from ffast.metrics.dims import Dim

try:  # jaxtyping array types (Float[np.ndarray, "N_atoms xyz"]) drive shape inference
    from jaxtyping import AbstractArray as _JaxArray
except Exception:  # pragma: no cover - jaxtyping is a hard dep, guard for safety
    _JaxArray = None

_EMPTY = inspect.Parameter.empty
_DIM_TABLE: Optional[dict] = None


def _dim_table() -> dict:
    """Map dim *name* -> Dim, harvested from ``ffast.metrics.dims`` once."""
    global _DIM_TABLE
    if _DIM_TABLE is None:
        from ffast.metrics import dims as _d
        _DIM_TABLE = {v.name: v for v in vars(_d).values() if isinstance(v, Dim)}
    return _DIM_TABLE


class Ref:
    """Annotate a metric input with its symbolic ref.

        def force_difference(
            reference: Ref["reference.forces"],
            predicted: Ref["prediction.forces"],
        ) -> (dims.N_atoms, dims.xyz): ...

    ``Ref(ref, optional=True)`` marks an optional input. A bare string
    annotation (``reference: "reference.forces"``) works too.
    """

    __slots__ = ("ref", "optional")

    def __init__(self, ref: str, *, optional: bool = False) -> None:
        self.ref = ref
        self.optional = optional

    def __class_getitem__(cls, item) -> "Ref":
        if isinstance(item, tuple):
            return cls(item[0])
        return cls(item)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Ref({self.ref!r}, optional={self.optional})"


@dataclass(frozen=True)
class P:
    """Extra compute-parameter metadata, used inside ``Annotated``.

        scale: Annotated[float, P(min=0.1, max=10.0, label="Error Scale")] = 1.0
    """
    role: Literal["compute", "present"] = "compute"
    min: Optional[float] = None
    max: Optional[float] = None
    label: str = ""
    description: str = ""
    scope: str = "view"
    choices: Optional[list] = None


def _resolve_annotations(func) -> dict:
    """Return name -> live annotation object, eval-ing stringized ones."""
    raw = getattr(func, "__annotations__", None) or {}
    g = getattr(func, "__globals__", {})
    out = {}
    for name, ann in raw.items():
        if isinstance(ann, str):
            try:
                ann = eval(ann, g)  # noqa: S307 - controlled, author's own module globals
            except Exception:
                pass
        out[name] = ann
    return out


def _extract(ann):
    """Return ``(base_type, P|None, Ref|None)`` from a possibly-``Annotated``
    annotation. Supports ``Ref[...]`` alone, ``Annotated[t, P(...)]`` and
    ``Annotated[Float[np.ndarray, "..."], Ref["..."]]`` (jaxtyping + ref)."""
    if get_origin(ann) is Annotated:
        args = get_args(ann)
        p = next((m for m in args[1:] if isinstance(m, P)), None)
        ref = next((m for m in args[1:] if isinstance(m, Ref)), None)
        return args[0], p, ref
    if isinstance(ann, Ref):
        return ann, None, ann
    return ann, None, None


def _ref_from_annotation(ann):
    """Return (ref|None, optional) for an input parameter annotation."""
    base, _, ref = _extract(ann)
    if ref is not None:
        return ref.ref, ref.optional
    if isinstance(base, str):
        return base, False
    return None, False


def _build_param(name: str, param: inspect.Parameter, ann) -> dict:
    base, extra, _ = _extract(ann)
    default = None if param.default is _EMPTY else param.default
    role = extra.role if extra else "compute"
    spec: dict[str, Any] = {"role": role}
    if extra:
        if extra.label:
            spec["label"] = extra.label
        if extra.description:
            spec["description"] = extra.description
        if extra.scope:
            spec["scope"] = extra.scope

    # choice: Literal[...] base, or an explicit choices list on P
    choices = None
    if get_origin(base) is Literal:
        choices = [str(c) for c in get_args(base)]
    elif extra and extra.choices is not None:
        choices = [str(c) for c in extra.choices]
    if choices is not None:
        spec.update(type="choice", choices=choices,
                    default=str(default if default is not None else choices[0]))
        return spec

    # bool before int (bool is a subclass of int)
    if base is bool or isinstance(default, bool):
        spec.update(type="bool", default=bool(default))
        return spec
    if base is int or (isinstance(default, int) and not isinstance(default, bool)):
        spec.update(type="int", default=int(default if default is not None else 0))
        if extra and extra.min is not None:
            spec["min"] = int(extra.min)
        if extra and extra.max is not None:
            spec["max"] = int(extra.max)
        return spec
    if base is float or isinstance(default, float):
        spec.update(type="float", default=float(default if default is not None else 0.0))
        if extra and extra.min is not None:
            spec["min"] = float(extra.min)
        if extra and extra.max is not None:
            spec["max"] = float(extra.max)
        return spec

    spec.update(type="string", default=str(default if default is not None else ""))
    return spec


def _shape_from_return(ann):
    """Derive the semantic shape tuple from a return annotation.

    Supports jaxtyping arrays (``Float[np.ndarray, "N_atoms xyz"]`` — axis names
    must be ``dims`` names), plain ``float``/``int`` → ``(dims.scalar,)``, and the
    legacy ``Dim`` / ``tuple[Dim, ...]`` forms.
    """
    if ann is None or ann is _EMPTY:
        return None
    base, _, _ = _extract(ann)

    if base is float or base is int:
        from ffast.metrics.dims import scalar
        return (scalar,)

    if _JaxArray is not None and isinstance(base, type) and issubclass(base, _JaxArray):
        from ffast.metrics.dims import scalar
        names = [getattr(d, "name", None) for d in base.dims]
        if not names:
            return (scalar,)
        table = _dim_table()
        bad = [n for n in names if n not in table]
        if bad:
            raise ValueError(
                f"Return shape axis(es) {bad} are not known dims. Use names from "
                f"ffast.metrics.dims ({', '.join(sorted(table))}) or pass shape=."
            )
        return tuple(table[n] for n in names)

    if isinstance(base, Dim):
        return (base,)
    if isinstance(base, tuple):
        return base
    return base


def _doc_parts(func):
    doc = inspect.getdoc(func) or ""
    if not doc:
        return "", ""
    head, _, rest = doc.partition("\n\n")
    return " ".join(head.split()), rest.strip()


def _infer_id(func, namespace: Optional[str]) -> str:
    ns = namespace
    if ns is None:
        ns = getattr(func, "__globals__", {}).get("METRIC_NAMESPACE")
    if ns is None:
        ns = getattr(func, "__module__", "") or ""
    name = func.__name__
    return f"{ns}.{name}" if ns else name


def infer_schema(
    func,
    *,
    id: Optional[str] = None,
    namespace: Optional[str] = None,
    inputs: Optional[dict] = None,
    shape=None,
    unit: Optional[str] = None,
    label: Optional[str] = None,
    description: Optional[str] = None,
    parameters: Optional[dict] = None,
    optional_inputs: Optional[list] = None,
    tests: Optional[list] = None,
    hints: Optional[dict] = None,
) -> dict:
    """Build the ``MetricSchema`` payload from ``func`` + explicit overrides.

    Every explicit (non-None) field wins over inference, so a fully-declared
    ``@metric(...)`` produces exactly what it produced before.
    """
    sig = inspect.signature(func)
    anns = _resolve_annotations(func)

    inf_inputs: dict[str, str] = {}
    inf_optional: list[str] = []
    inf_params: dict[str, dict] = {}

    infer_inputs = inputs is None
    infer_params = parameters is None

    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if param.kind is inspect.Parameter.KEYWORD_ONLY:
            if infer_params:
                inf_params[pname] = _build_param(pname, param, anns.get(pname))
            continue
        # positional-or-keyword → input
        if not infer_inputs:
            continue
        ref, optional = _ref_from_annotation(anns.get(pname))
        if param.default is not _EMPTY:
            optional = True
        if ref is not None:
            inf_inputs[pname] = ref
        if optional:
            inf_optional.append(pname)
        elif ref is None:
            raise ValueError(
                f"Metric '{func.__name__}': input parameter '{pname}' has no ref "
                f"annotation and no default. Annotate it, e.g. "
                f"'{pname}: Ref[\"reference.forces\"]', or pass inputs= explicitly."
            )

    doc_label, doc_desc = _doc_parts(func)

    inf_shape = _shape_from_return(anns.get("return"))
    resolved_shape = shape if shape is not None else inf_shape
    if resolved_shape is None:
        raise ValueError(
            f"Metric '{func.__name__}': cannot infer shape. Add a return "
            f"annotation (e.g. '-> (dims.N_atoms, dims.xyz)') or pass shape=."
        )

    return {
        "id": id if id is not None else _infer_id(func, namespace),
        "label": label if label is not None else doc_label,
        "description": description if description is not None else doc_desc,
        "inputs": inputs if inputs is not None else inf_inputs,
        "optional_inputs": optional_inputs if optional_inputs is not None else inf_optional,
        "shape": resolved_shape,
        "unit": unit if unit is not None else "dimensionless",
        "parameters": parameters if parameters is not None else inf_params,
        "tests": tests or [],
        "hints": hints or {},
    }
