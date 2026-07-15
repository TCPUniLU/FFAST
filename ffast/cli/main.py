from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from ffast.config.loader import discover_config, load_metric_modules, load_project_config
from ffast.metrics.builtin import force_metrics, energy_metrics, atomic_metrics, accel_metrics, structure_metrics  # noqa: F401 — register built-ins
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.models import MetricFailure
from ffast.metrics.registry import MetricRegistry, _default_registry
import ffast.visualization.stages.builtin  # noqa: F401 — register builtin stages
from ffast.visualization.stages.registry import _default_registry as _stage_registry


def cmd_dataset_keys(args: argparse.Namespace) -> None:
    """List the per-frame (atoms.info) and per-atom (atoms.arrays) keys in a file
    and whether each is usable as a Dataset Field (ADR 0023). Inspects the first
    frame; full-dataset validation happens at metric time (all-or-nothing)."""
    from ffast.io.xyz import read_ase_or_explain

    try:
        atoms = read_ase_or_explain(args.path, index=0)
    except Exception as exc:
        print(f"Error: could not read {args.path}: {exc}", file=sys.stderr)
        sys.exit(1)

    reserved = {"positions", "numbers", "momenta"}

    print(f"Dataset Fields in {args.path} (first frame):\n")
    print("Frame fields  — reference.info.<key> / prediction.info.<key>:")
    if not atoms.info:
        print("  (none)")
    for k in sorted(atoms.info):
        v = np.asarray(atoms.info[k])
        ok = v.ndim == 0 and np.issubdtype(v.dtype, np.number)
        mark = "✓" if ok else "✗ not a numeric scalar"
        print(f"  {k:<24} dtype={v.dtype}  {mark}")

    print("\nAtom fields   — reference.atoms.<key> / prediction.atoms.<key>:")
    atom_keys = [k for k in atoms.arrays if k not in reserved]
    if not atom_keys:
        print("  (none)")
    for k in sorted(atom_keys):
        v = np.asarray(atoms.arrays[k])
        ok = v.ndim == 1 and v.shape[0] == len(atoms) and np.issubdtype(v.dtype, np.number)
        if ok:
            mark = "✓"
        elif v.ndim != 1:
            mark = f"✗ not per-atom scalar (shape {v.shape})"
        else:
            mark = "✗ non-numeric"
        print(f"  {k:<24} shape={v.shape} dtype={v.dtype}  {mark}")


def _resolve_config(config_arg: str | None) -> Path:
    if config_arg:
        return Path(config_arg)
    discovered = discover_config(Path.cwd())
    if discovered is None:
        print("Error: no ffast.toml found. Pass --config or run from a project directory.", file=sys.stderr)
        sys.exit(1)
    return discovered


def cmd_config_validate(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    try:
        load_project_config(config_path)
        print(f"OK: {config_path}")
    except ValidationError as e:
        print(f"Invalid config: {config_path}\n{e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: file not found: {config_path}", file=sys.stderr)
        sys.exit(1)


def cmd_metrics_list(args: argparse.Namespace) -> None:
    config_path = _resolve_config(getattr(args, "config", None))
    config = load_project_config(config_path)
    load_metric_modules(config, config_path)
    for metric_id in sorted(_default_registry.list_metrics()):
        print(metric_id)


def cmd_metrics_inspect(args: argparse.Namespace) -> None:
    config_path = _resolve_config(getattr(args, "config", None))
    config = load_project_config(config_path)
    load_metric_modules(config, config_path)
    try:
        schema, _ = _default_registry.get(args.metric_id)
    except KeyError:
        print(f"Error: metric '{args.metric_id}' not found.", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(schema.model_dump(), indent=2))


def cmd_metrics_test(args: argparse.Namespace) -> None:
    config_path = _resolve_config(getattr(args, "config", None))
    config = load_project_config(config_path)
    load_metric_modules(config, config_path)

    metric_ids = [args.metric_id] if getattr(args, "metric_id", None) else sorted(_default_registry.list_metrics())

    all_passed = True
    for metric_id in metric_ids:
        try:
            schema, _ = _default_registry.get(metric_id)
        except KeyError:
            print(f"Error: metric '{metric_id}' not found.", file=sys.stderr)
            sys.exit(1)

        if not schema.tests:
            print(f"  {metric_id}: no tests")
            continue

        for i, test in enumerate(schema.tests):
            label = f"{metric_id}[{i}]"
            raw_inputs = {k: np.asarray(v, dtype=float) for k, v in test.inputs.items()}
            expected = np.asarray(test.expected, dtype=float)

            # Run 1: correctness check (fresh registry + executor, no shared cache)
            registry1 = _fresh_registry_with_builtins()
            load_metric_modules(config, config_path)
            result1 = InProcessExecutor(registry1).run(metric_id, raw_inputs, test.parameters)

            if isinstance(result1, MetricFailure):
                print(f"  FAIL {label}: run 1 raised — {result1.traceback.splitlines()[-1]}")
                all_passed = False
                continue

            if result1.values.shape != expected.shape:
                print(f"  FAIL {label}: shape {result1.values.shape} != expected {expected.shape}")
                all_passed = False
                continue

            if not np.allclose(result1.values, expected, atol=test.atol, rtol=test.rtol):
                print(f"  FAIL {label}: values differ (atol={test.atol}, rtol={test.rtol})")
                print(f"    got:      {result1.values}")
                print(f"    expected: {expected}")
                all_passed = False
                continue

            # Run 2: determinism check
            registry2 = _fresh_registry_with_builtins()
            load_metric_modules(config, config_path)
            result2 = InProcessExecutor(registry2).run(metric_id, raw_inputs, test.parameters)

            if isinstance(result2, MetricFailure):
                print(f"  FAIL {label}: run 2 raised — {result2.traceback.splitlines()[-1]}")
                all_passed = False
                continue

            if result1.checksum != result2.checksum:
                print(f"  FAIL {label}: non-deterministic (checksums differ between runs)")
                all_passed = False
                continue

            print(f"  OK   {label}")

    if not all_passed:
        sys.exit(1)


def _coerce_param(schema, key: str, raw: str):
    """Coerce a CLI ``KEY=VALUE`` string to the metric parameter's declared type."""
    pschema = schema.parameters.get(key)
    if pschema is None:
        known = ", ".join(sorted(schema.parameters)) or "(none)"
        print(
            f"Error: unknown parameter '{key}' for metric '{schema.id}'. "
            f"Known parameters: {known}",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        if pschema.type == "float":
            return float(raw)
        if pschema.type == "int":
            return int(raw)
        if pschema.type == "bool":
            low = raw.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("0", "false", "no", "off"):
                return False
            raise ValueError(f"not a boolean: {raw!r}")
        if pschema.type == "choice":
            if raw not in pschema.choices:
                raise ValueError(f"{raw!r} not in choices {pschema.choices}")
            return raw
        return raw  # string
    except ValueError as exc:
        print(f"Error: invalid value for parameter '{key}': {exc}", file=sys.stderr)
        sys.exit(1)


def _parse_params(schema, raw_params) -> dict:
    params: dict = {}
    for item in raw_params or []:
        if "=" not in item:
            print(f"Error: --param must be KEY=VALUE, got {item!r}", file=sys.stderr)
            sys.exit(1)
        key, raw = item.split("=", 1)
        params[key] = _coerce_param(schema, key, raw)
    return params


def _summarize_values(values) -> str:
    """One-line numeric summary of a metric result's values."""
    arr = np.asarray(values)
    if arr.size == 1:
        return f"{float(arr.reshape(-1)[0]):.6g}"
    flat = arr.reshape(-1).astype(float)
    return (
        f"shape={tuple(arr.shape)}  "
        f"min={flat.min():.6g}  mean={flat.mean():.6g}  max={flat.max():.6g}"
    )


def cmd_metrics_run(args: argparse.Namespace) -> None:
    # Load config + custom metric modules so project-defined metrics are
    # available; builtins are already registered via the module-level import.
    # Unlike list/inspect/test, a missing config is not an error here — running
    # a built-in metric on a dataset should not require an ffast.toml.
    project_config = None
    if getattr(args, "config", None):
        config_path = Path(args.config)
        project_config = load_project_config(config_path)
        load_metric_modules(project_config, config_path)
    else:
        discovered = discover_config(Path.cwd())
        if discovered is not None:
            project_config = load_project_config(discovered)
            load_metric_modules(project_config, discovered)

    # Register declarative Dataset Field passthrough metrics (ADR 0023) and
    # Analysis-Tab Transform Metrics so a project-defined metric id can be run,
    # mirroring the server's pre-freeze compile pass (modules/configTabs.loadData).
    if project_config is not None:
        from ffast.config.tabs import compile_project_metrics
        result = compile_project_metrics(project_config)
        for context, msg in result.errors:
            print(f"Warning: metric config error in {context}: {msg}", file=sys.stderr)

    try:
        schema, _ = _default_registry.get(args.metric_id)
    except KeyError:
        print(f"Error: metric '{args.metric_id}' not found.", file=sys.stderr)
        sys.exit(1)

    params = _parse_params(schema, args.param)

    if not Path(args.dataset).exists():
        print(f"Error: dataset not found: {args.dataset}", file=sys.stderr)
        sys.exit(1)
    if args.prediction and not Path(args.prediction).exists():
        print(f"Error: prediction file not found: {args.prediction}", file=sys.stderr)
        sys.exit(1)

    # metric_needs_prediction only reads the registry — cheap, no env required.
    from ffast.metrics.input_resolver import metric_needs_prediction

    if metric_needs_prediction(args.metric_id) and not args.prediction:
        print(
            f"Error: metric '{args.metric_id}' requires model predictions. "
            f"Pass --prediction PATH (a pre-predicted file with energies/forces).",
            file=sys.stderr,
        )
        sys.exit(1)

    # The headless Environment pulls in the full data-loading + module stack and
    # starts a worker thread; only spin it up once cheap validation has passed.
    from client.environment import startHeadlessEnvironment

    env = startHeadlessEnvironment()
    try:
        env.taskLoadDataset(args.dataset, args.dataset_type)
        env.waitForTasks(verbose=args.verbose, dt=0.5)
        dataset = env.getDatasetFromPath(args.dataset)
        if dataset is None:
            print(
                f"Error: failed to load dataset {args.dataset} "
                f"(type {args.dataset_type!r}). See log above.",
                file=sys.stderr,
            )
            sys.exit(1)

        model = None
        if args.prediction:
            before = {m.fingerprint for m in env.models.all()}
            env.loadPrepredictedDataset(
                args.prediction,
                dataset.fingerprint,
                selected_energy_key=args.pred_energy_key,
                selected_force_key=args.pred_force_key,
            )
            env.waitForTasks(verbose=args.verbose, dt=0.5)
            new_models = [m for m in env.models.all() if m.fingerprint not in before]
            if not new_models:
                print(
                    f"Error: prediction file {args.prediction} produced no model. "
                    f"See log above.",
                    file=sys.stderr,
                )
                sys.exit(1)
            model = new_models[0]

        key = env.data.make_metric_cache_key(args.metric_id, params, model, dataset)
        env.data.taskGenerateMetric(args.metric_id, params, model, dataset, key)
        env.waitForTasks(verbose=args.verbose, dt=0.5)

        result = env.data.getCacheByKey(key, subChecks=False)
        if result is None:
            print(
                f"Error: metric '{args.metric_id}' did not compute "
                f"(missing inputs or runtime failure). See log above.",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.json:
            print(json.dumps({
                "metric_id": result.metric_id,
                "shape": result.shape,
                "unit": result.unit,
                "dtype": result.dtype,
                "compute_parameters": result.compute_parameters,
                "checksum": result.checksum,
                "values": np.asarray(result.values).tolist(),
            }, indent=2))
        else:
            print(f"{result.metric_id}  ({schema.label or schema.id})")
            print(f"  dataset:    {dataset.getName()}  (N={dataset.getN()})")
            if model is not None:
                print(f"  prediction: {model.getName()}")
            print(f"  shape:      {result.shape}")
            print(f"  unit:       {result.unit or '(dimensionless)'}")
            print(f"  dtype:      {result.dtype}")
            if result.compute_parameters:
                print(f"  parameters: {result.compute_parameters}")
            print(f"  value:      {_summarize_values(result.values)}")
    finally:
        env.headlessQuit()


def cmd_metrics_validate(args: argparse.Namespace) -> None:
    """Freeze the metric graph and report ref/shape/cycle errors (decision H2).

    Builtins are registered at import; custom modules from a config (explicit or
    discovered) are loaded first so the whole graph is validated together. This
    is the headless equivalent of the server's startup validation.
    """
    project_config = None
    if getattr(args, "config", None):
        config_path = Path(args.config)
        project_config = load_project_config(config_path)
        load_metric_modules(project_config, config_path)
    else:
        discovered = discover_config(Path.cwd())
        if discovered is not None:
            project_config = load_project_config(discovered)
            load_metric_modules(project_config, discovered)

    # Compile every declarative metric (Dataset Fields ADR 0023, Expression
    # Metrics ADR 0042, Analysis-Tab Transform Metrics ADR 0021 + bundled tabs)
    # so they are validated alongside the built-ins — the headless equivalent of
    # the server's pre-freeze compile pass. A config-load error is a
    # Configuration Failure: print it and exit non-zero before freezing.
    from ffast.config.tabs import compile_project_metrics
    result = compile_project_metrics(project_config)
    if result.errors:
        for context, msg in result.errors:
            print(f"  ERROR {context}: {msg}", file=sys.stderr)
        print(
            f"Metric configuration FAILED: {len(result.errors)} error(s) at "
            f"config-load.",
            file=sys.stderr,
        )
        sys.exit(1)
    if result.ids:
        print(f"Compiled {len(result.ids)} declarative metric(s).")

    errors = _default_registry.freeze()
    if errors:
        for mid, msg in errors:
            print(f"  ERROR [{mid}]: {msg}", file=sys.stderr)
        print(
            f"Metric validation FAILED: {len(errors)} error(s) across "
            f"{len(_default_registry.list_metrics())} metric(s).",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"OK: {len(_default_registry.list_metrics())} metrics, dependency graph valid.")


def _is_textual(x) -> bool:
    """True for a string, or a list/array whose elements are strings."""
    if isinstance(x, str):
        return True
    if isinstance(x, (list, tuple)):
        return len(x) > 0 and isinstance(x[0], str)
    return getattr(x, "dtype", None) is not None and x.dtype.kind in ("U", "S")


def _compare_stage_result(result, expected, atol: float, rtol: float):
    """Compare stage return value to expected. Returns (ok, error_msg)."""
    if expected is None:
        if result is None:
            return True, None
        return False, f"expected None, got {type(result).__name__}"
    if result is None:
        return False, "expected non-None result, got None"

    # Multi-output stages return a list/tuple of outputs (e.g. displacement_stats,
    # force_arrows, kabsch_alignment, atom_labels); compare element-by-element.
    # Single-output stages return an ndarray (or scalar), so a list/tuple result
    # paired with a list/tuple expected of equal length is always multi-output.
    # (Element-wise comparison stays correct even if a single nested-list array
    # slips through here — each row is compared as an array.)
    if isinstance(result, (list, tuple)) and isinstance(expected, (list, tuple)):
        if len(result) != len(expected):
            return False, f"expected {len(expected)} outputs, got {len(result)}"
        for i, (r_elem, e_elem) in enumerate(zip(result, expected)):
            ok, msg = _compare_stage_result(r_elem, e_elem, atol, rtol)
            if not ok:
                return False, f"element {i}: {msg}"
        return True, None

    # Textual outputs (e.g. label strings): compare with equality, not allclose.
    if _is_textual(result) or _is_textual(expected):
        r_list = [str(v) for v in np.asarray(result).ravel()]
        e_list = [str(v) for v in np.asarray(expected).ravel()]
        if r_list != e_list:
            return False, f"text differs: {r_list} != {e_list}"
        return True, None

    result_arr = np.asarray(result)
    expected_arr = np.asarray(expected)
    if result_arr.shape != expected_arr.shape:
        return False, f"shape {result_arr.shape} != expected {expected_arr.shape}"
    if not np.allclose(result_arr, expected_arr, atol=atol, rtol=rtol):
        return False, f"values differ (atol={atol}, rtol={rtol})"
    return True, None


def cmd_stages_list(args: argparse.Namespace) -> None:
    for stage_id in sorted(_stage_registry.list_stages()):
        print(stage_id)


def cmd_stages_inspect(args: argparse.Namespace) -> None:
    try:
        schema, _ = _stage_registry.get(args.stage_id)
    except KeyError:
        print(f"Error: stage '{args.stage_id}' not found.", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(schema.model_dump(), indent=2))


def cmd_stages_test(args: argparse.Namespace) -> None:
    stage_ids = [args.stage_id] if getattr(args, "stage_id", None) else sorted(_stage_registry.list_stages())

    all_passed = True
    for stage_id in stage_ids:
        try:
            schema, fn = _stage_registry.get(stage_id)
        except KeyError:
            print(f"Error: stage '{stage_id}' not found.", file=sys.stderr)
            sys.exit(1)

        if not schema.tests:
            print(f"  {stage_id}: no tests")
            continue

        for i, test in enumerate(schema.tests):
            label = f"{stage_id}[{i}]"
            try:
                inputs = {k: np.asarray(v) if isinstance(v, list) else v for k, v in test.inputs.items()}
                result = fn(**inputs, **test.parameters)
            except ImportError as e:
                print(f"  SKIP {label}: missing dependency — {e}")
                continue
            except Exception as e:
                print(f"  FAIL {label}: raised {type(e).__name__}: {e}")
                all_passed = False
                continue

            ok, msg = _compare_stage_result(result, test.expected, test.atol, test.rtol)
            if ok:
                print(f"  OK   {label}")
            else:
                print(f"  FAIL {label}: {msg}")
                all_passed = False

    if not all_passed:
        sys.exit(1)


def _fresh_registry_with_builtins() -> MetricRegistry:
    from ffast.metrics import registry as reg_module
    r = MetricRegistry()
    # Re-register all metrics currently in the default registry into a fresh one
    for metric_id in _default_registry.list_metrics():
        schema, fn = _default_registry.get(metric_id)
        r._metrics[metric_id] = (schema, fn)
    return r


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ffast", description="FFAST headless tools")
    sub = parser.add_subparsers(dest="command")

    # ffast config ...
    config_parser = sub.add_parser("config", help="Configuration commands")
    config_sub = config_parser.add_subparsers(dest="config_command")

    validate = config_sub.add_parser("validate", help="Validate a config file")
    validate.add_argument("config", help="Path to ffast.toml")
    validate.set_defaults(func=cmd_config_validate)

    # ffast metrics ...
    metrics_parser = sub.add_parser("metrics", help="Metric commands")
    metrics_sub = metrics_parser.add_subparsers(dest="metrics_command")

    list_cmd = metrics_sub.add_parser("list", help="List registered metrics")
    list_cmd.add_argument("--config", default=None, help="Path to ffast.toml")
    list_cmd.set_defaults(func=cmd_metrics_list)

    inspect_cmd = metrics_sub.add_parser("inspect", help="Inspect a metric")
    inspect_cmd.add_argument("metric_id", help="Metric ID to inspect")
    inspect_cmd.add_argument("--config", default=None, help="Path to ffast.toml")
    inspect_cmd.set_defaults(func=cmd_metrics_inspect)

    test_cmd = metrics_sub.add_parser("test", help="Run metric tests")
    test_cmd.add_argument("metric_id", nargs="?", default=None, help="Metric ID to test (omit for all)")
    test_cmd.add_argument("--config", default=None, help="Path to ffast.toml")
    test_cmd.set_defaults(func=cmd_metrics_test)

    validate_cmd = metrics_sub.add_parser(
        "validate", help="Validate the metric dependency graph (refs, shapes, cycles)"
    )
    validate_cmd.add_argument("--config", default=None, help="Path to ffast.toml")
    validate_cmd.set_defaults(func=cmd_metrics_validate)

    run_cmd = metrics_sub.add_parser("run", help="Run a metric against a dataset")
    run_cmd.add_argument("metric_id", help="Metric ID to run")
    run_cmd.add_argument("--dataset", required=True, help="Path to a dataset file")
    run_cmd.add_argument(
        "--dataset-type", default="ase (auto)",
        help="Dataset loader type (default: 'ase (auto)'; use 'sGDML' for .npz)",
    )
    run_cmd.add_argument(
        "--prediction", default=None,
        help="Pre-predicted file (energies/forces) for prediction-dependent metrics",
    )
    run_cmd.add_argument(
        "--pred-energy-key", default=None,
        help="Energy key in the prediction file (auto-detected if omitted)",
    )
    run_cmd.add_argument(
        "--pred-force-key", default=None,
        help="Force key in the prediction file (auto-detected if omitted)",
    )
    run_cmd.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="Compute/presentation parameter (repeatable)",
    )
    run_cmd.add_argument("--config", default=None, help="Path to ffast.toml")
    run_cmd.add_argument(
        "--json", action="store_true",
        help="Emit the full result as JSON (includes all values)",
    )
    run_cmd.add_argument(
        "--verbose", "-v", action="store_true", help="Show task progress",
    )
    run_cmd.set_defaults(func=cmd_metrics_run)

    # ffast stages ...
    stages_parser = sub.add_parser("stages", help="Stage commands")
    stages_sub = stages_parser.add_subparsers(dest="stages_command")

    stages_list_cmd = stages_sub.add_parser("list", help="List registered stages")
    stages_list_cmd.set_defaults(func=cmd_stages_list)

    stages_inspect_cmd = stages_sub.add_parser("inspect", help="Inspect a stage")
    stages_inspect_cmd.add_argument("stage_id", help="Stage ID to inspect")
    stages_inspect_cmd.set_defaults(func=cmd_stages_inspect)

    stages_test_cmd = stages_sub.add_parser("test", help="Run stage tests")
    stages_test_cmd.add_argument("stage_id", nargs="?", default=None, help="Stage ID to test (omit for all)")
    stages_test_cmd.set_defaults(func=cmd_stages_test)

    # ffast dataset ...
    dataset_parser = sub.add_parser("dataset", help="Dataset commands")
    dataset_sub = dataset_parser.add_subparsers(dest="dataset_command")

    keys_cmd = dataset_sub.add_parser(
        "keys",
        help="List Dataset Field keys (atoms.info / atoms.arrays) usable in metrics",
    )
    keys_cmd.add_argument("path", help="Path to an extxyz / ASE-readable file")
    keys_cmd.set_defaults(func=cmd_dataset_keys)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)
    args.func(args)
