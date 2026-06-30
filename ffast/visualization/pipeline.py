"""Execute registered pipeline stages in dependency order.

The executor turns the Stage Catalog into actual pipeline execution: it asks the
registry to topologically order the stages needed for a set of targets, resolves
each stage's declared inputs from an external namespace context plus prior stage
outputs, injects resolved parameters, and collects every output under its
canonical ``stage.<id>.<output>`` address.

External-namespace inputs (``frame.``, ``view.``, ``dataset.``, ``metric.``,
``reference.``, ``prediction.``) are supplied by the caller in ``context``;
``stage.<id>.<output>`` inputs are produced by upstream stages during the run.
"""
from __future__ import annotations

from typing import Any

from ffast.visualization.stages.registry import StageRegistry


class StageExecutionError(RuntimeError):
    """Raised when a stage cannot be executed (missing input or bad output arity)."""


def _resolve_params(schema, overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Stage parameter defaults overlaid with caller overrides (known params only)."""
    params = {name: p.default for name, p in schema.parameters.items()}
    for key, value in (overrides or {}).items():
        if key in schema.parameters:
            params[key] = value
    return params


def execute(
    registry: StageRegistry,
    targets: list[str],
    context: dict[str, Any],
    parameters: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the stages required for ``targets`` and return all available addresses.

    ``context`` maps external-namespace addresses (e.g. ``"frame.positions"``) to
    values. ``parameters`` maps ``stage_id -> {param: value}`` overrides. The
    return value is ``context`` extended with every produced
    ``stage.<id>.<output>`` value.
    """
    parameters = parameters or {}
    results: dict[str, Any] = dict(context)

    for sid in registry.resolve_order(targets):
        schema, fn = registry.get(sid)

        kwargs: dict[str, Any] = {}
        for arg_name, ref in schema.inputs.items():
            if ref in results:
                kwargs[arg_name] = results[ref]
            # Absent inputs fall back to the stage function's own default.

        kwargs.update(_resolve_params(schema, parameters.get(sid)))

        try:
            out = fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surface the offending stage id
            raise StageExecutionError(f"stage '{sid}' failed: {exc}") from exc

        out_keys = list(schema.outputs.keys())
        if len(out_keys) <= 1:
            key = out_keys[0] if out_keys else "result"
            results[f"stage.{sid}.{key}"] = out
        else:
            if not isinstance(out, (list, tuple)) or len(out) != len(out_keys):
                raise StageExecutionError(
                    f"stage '{sid}' declares {len(out_keys)} outputs but returned "
                    f"{type(out).__name__}"
                )
            for key, value in zip(out_keys, out):
                results[f"stage.{sid}.{key}"] = value

    return results
