"""Repeatable full-file → embedded prediction → Basic Errors server profile."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


def basic_errors_requests():
    """Requests drawn by analysis.js, derived from the bundled tab config.

    Parity x/y describe selection, not drawing. Fetching them as extra roots
    would materialise whole force arrays that the browser never requests.
    """
    from ffast.config.tabs import load_builtin_tabs, compile_tabs_metrics, tab_layout

    tab = next(t for t in load_builtin_tabs() if t.name == "Basic Errors")
    compile_tabs_metrics([tab])
    requests = []
    for panel in tab_layout(tab)["panels"]:
        metrics = panel["metrics"]
        if panel["kind"] == "timeline":
            requests.append(("REQUEST_PLOT_VIEW", {
                "kind": "line", "metrics": metrics, "width": 900, "height": 300}))
        else:
            for role, refs in metrics.items():
                if panel["kind"] == "parity" and role not in ("density", "outliers"):
                    continue
                for metric in refs if isinstance(refs, list) else [refs]:
                    requests.append(("REQUEST_METRIC", {"metric_id": metric}))
    return requests


@contextmanager
def stage(profile, name):
    profile.phase = name
    started, cpu = time.perf_counter(), time.process_time()
    print(f"[{name}] started", flush=True)
    with profile.span("workload." + name) as fields:
        try:
            yield fields
        finally:
            elapsed = time.perf_counter() - started
            fields["process_cpu_s"] = time.process_time() - cpu
            fields["average_process_cores"] = fields["process_cpu_s"] / max(elapsed, 1e-9)
            try:
                import ffast_xyz
                fields["native_seconds"] = ffast_xyz.timings()
            except (ImportError, AttributeError):
                fields["native_seconds"] = None
            print(f"[{name}] {elapsed:.3f}s, {fields['average_process_cores']:.2f} CPU cores", flush=True)


async def run_workload(args, profile):
    from ffast.core.environment import HeadlessEnvironment
    from ffast.core.plugin_discovery import loadModules
    from ffast.config.tabs import compile_project_metrics
    from ffast.metrics.result_store import MetricResultStore
    from ffast.protocol.rpc import unpack
    from ffast.session.server_session import ServerSession
    from ffast.io.budget import budget
    from ffast.readers.text import fast_parser_usable, fast_threads

    with stage(profile, "bootstrap"):
        env = HeadlessEnvironment()
        loadModules(env)
        compiled = compile_project_metrics(None)
        if compiled.errors:
            raise RuntimeError(str(compiled.errors))
        env.results = MetricResultStore(env)
        env.data.metricExecutor.start()
        requests = basic_errors_requests()
        queue = asyncio.Queue()
        session = ServerSession(env, queue)
        loop = asyncio.create_task(env.headlessEventLoop())
    profile.metadata.update({"fast_parser_usable": fast_parser_usable(),
        "parser_threads": fast_threads(), "ram_budget_bytes": budget().limit,
        "parsed_cache_budget_bytes": budget().parsed_limit,
        "result_cache_budget_bytes": budget().result_limit,
        "request_count": len(requests)})
    profile._write({"type": "configuration", **profile.metadata})

    replies = []
    async def drain():
        while True:
            data = await queue.get()
            event, pos, kw = unpack(data)
            if event in ("METRIC_RESULT", "PLOT_VIEW", "LOAD_RESULT"):
                # Inspect metadata only; never decode or retain result arrays.
                errors = []
                if kw.get("error"):
                    errors.append(str(kw["error"]))
                if event in ("METRIC_RESULT", "PLOT_VIEW") and not kw.get("ok"):
                    errors.append(kw.get("kind") or "metric failed")
                if event == "PLOT_VIEW":
                    for role, val in kw.get("results", {}).items():
                        if val.get("error"):
                            errors.append(f"{role}: {val['error']}")
                record = {"type": "reply", "phase": profile.phase,
                    "at_s": time.perf_counter() - profile.started,
                    "event": event, "request_id": pos[0] if pos else None,
                    "bytes": len(data), "errors": errors}
                replies.append(record)
                profile._write(record)
            queue.task_done()

    draining = asyncio.create_task(drain())
    try:
        with stage(profile, "load") as info:
            await session.dispatch("LOAD_DATASET", (), {
                "path": str(args.dataset), "dataset_type": "ase (auto)",
                "selected_energy_key": args.energy_key,
                "selected_force_key": args.force_key,
                "prediction_keys": [[args.pred_energy_key, args.pred_force_key, "profile prediction"]],
                "slice_num": 0, "memory_mode": args.memory_mode})
            await asyncio.to_thread(env.waitForTasks)
            dataset = env.getDatasetFromPath(str(args.dataset))
            if dataset is None:
                raise RuntimeError("Dataset failed to load; see run.log and events.jsonl")
            models = list(env.models.all())
            if len(models) != 1:
                raise RuntimeError(f"Expected one embedded prediction, found {len(models)}")
            model = models[0]
            info.update({"frames": dataset.getN(),
                "memory_mode": "saving" if getattr(dataset, "storage", None) is not None else "eager",
                "requested_memory_mode": args.memory_mode,
                "fingerprint": dataset.fingerprint})
            storage = getattr(dataset, "storage", None)
            if storage is not None:
                import numpy as np
                from ffast.readers.text import per_atom

                info["batch_layout"] = {
                    "identity": storage.identity,
                    "ranges_sha256": hashlib.sha256(
                        np.asarray(storage.ranges, dtype=np.int64).tobytes()).hexdigest(),
                    "count": len(storage.ranges),
                    "first_ranges": storage.ranges[:3],
                    "columns_bytes": {
                        key: int((storage.offsets[-1] if per_atom(key) else dataset.getN())
                                 * np.prod(column["tail"], dtype=np.int64)
                                 * column["dtype"].itemsize)
                        for key, column in storage.info.items()}}
        async def ask(index, event, payload):
            started = time.perf_counter()
            await session.dispatch(event, (), {
                **payload, "request_id": f"profile-{index}", "params": {},
                "dataset_fp": dataset.fingerprint, "model_fp": model.fingerprint})
            profile._write({"type": "request_done", "phase": profile.phase,
                "request_id": f"profile-{index}", "event": event,
                "metric": payload.get("metric_id", payload.get("metrics")),
                "seconds": time.perf_counter() - started})

        for name in ("basic_errors", "basic_errors_cached"):
            with stage(profile, name) as info:
                before = len(replies)
                await asyncio.gather(*(ask(i, event, payload)
                    for i, (event, payload) in enumerate(requests)))
                await queue.join()
                received = [r for r in replies[before:]
                            if r["event"] in ("METRIC_RESULT", "PLOT_VIEW")]
                info["reply_count"] = len(received)
                info["reply_bytes"] = sum(r["bytes"] for r in received)
                info["errors"] = [e for r in received for e in r["errors"]]
                info["dashboard_ok"] = not info["errors"] and len(received) == len(requests)
                if len(received) != len(requests):
                    raise RuntimeError(f"Incomplete Basic Errors: {info}")
    finally:
        session.close()
        env.headlessQuit()
        await loop
        await queue.join()
        draining.cancel()
        await asyncio.gather(draining, return_exceptions=True)
        env.results._pool.shutdown(wait=True)
        env.data.metricExecutor.shutdown()


def write_report(output):
    rows = []
    for path in sorted(output.glob("run-*/summary.json")):
        summary = json.loads(path.read_text())
        # The raw trace can be much larger than RAM on a long scan. Only
        # boundary/reply/request records are needed for the Markdown report.
        events = []
        with (path.parent / "events.jsonl").open() as stream:
            for line in stream:
                if any(marker in line for marker in (
                        '"name": "workload.', '"type": "reply"',
                        '"type": "request_done"')):
                    events.append(json.loads(line))
        rows.append((path.parent.name, summary, events))
    lines = ["# Laptop profile", "", "Full dataset → embedded predictions → Basic Errors.", "",
        "Fresh server process per run; run 1 starts with an empty isolated FFAST cache,",
        "later runs reuse that cache. OS/device cache state is uncontrolled.", "",
        "Includes server loading, computation, thinning and response packing. Excludes",
        "browser drawing, WebSocket transport, load-dialog probing and 3D colouring.", "",
        "| Run | Phase | Wall seconds | Average CPU cores |",
        "|---|---|---:|---:|"]
    for name, summary, events in rows:
        for e in events:
            if e.get("name", "").startswith("workload."):
                lines.append(f"| {name} | {e['phase']} | {e['seconds']:.3f} | {e['average_process_cores']:.2f} |")
    for name, summary, events in rows:
        lines.extend(["", f"## {name}", "",
            f"Completed: {summary['completed']}. Peak process tree RSS: "
            f"{summary['peak_process_tree_rss_bytes'] / 2**30:.2f} GiB. "
            f"Minimum system available RAM: {summary['min_system_available_bytes'] / 2**30:.2f} GiB.", "",
            "| Phase | Operation | Metric | Calls | Inclusive seconds | Cache hit/miss | Requested GiB |",
            "|---|---|---|---:|---:|---:|---:|"])
        for a in summary["phases"][:35]:
            lines.append(f"| {a['phase']} | {a['name']} | {a['metric']} | {a['calls']} | "
                f"{a['seconds']:.3f} | {a['hits']}/{a['misses']} | {a['logical_bytes']/2**30:.3f} |")
        if summary["error"]:
            lines.extend(["", "Run error: " + summary["error"]])
        errors = sorted({error for e in events for error in e.get("errors", [])})
        if errors:
            lines.extend(["", "Dashboard failures (a completed profile can contain failed metrics):", ""])
            lines.extend("- " + error for error in errors)
        native = [(e["phase"], key, seconds) for e in events
                  for key, seconds in (e.get("native_seconds") or {}).items()]
        if native:
            lines.extend(["", "Native parser phase totals (concurrent calls can overlap):", "",
                "| Workload phase | Native phase | Seconds |", "|---|---|---:|"])
            lines.extend(f"| {phase} | {key} | {seconds:.3f} |" for phase, key, seconds in native)
        requests = sorted((e for e in events if e["type"] == "request_done"),
                          key=lambda e: -e["seconds"])
        if requests:
            lines.extend(["", "Slowest requests, including waits and response packing:", "",
                "| Phase | Metric(s) | Seconds |", "|---|---|---:|"])
            lines.extend(f"| {e['phase']} | {e['metric']} | {e['seconds']:.3f} |" for e in requests[:8])
    lines.extend(["", "Inclusive spans nest and overlap: do not sum them as elapsed time.",
        "Requested bytes are logical payload, not measured physical disk traffic.",
        "Sampled Python stack residency does not identify native CPU hotspots.", ""])
    for path in sorted(output.glob("run-*-status.json")):
        status = json.loads(path.read_text())
        if status["state"] != "completed":
            lines.extend(["", f"Incomplete run: {path.stem}: {status['state']}. "
                "Inspect its log and incremental events; it may have no summary.", ""])
    (output / "report.md").write_text("\n".join(lines))


def check_targets(output, seconds):
    """Evaluate the load and first-dashboard target using recorded evidence."""
    checks = []
    for folder in sorted(p for p in Path(output).glob("run-*") if p.is_dir()):
        path = folder / "summary.json"
        summary = json.loads(path.read_text()) if path.is_file() else {}
        phases = {}
        if not (folder / "events.jsonl").is_file():
            checks.append({"run": folder.name, "phase": "incomplete",
                           "seconds": None, "passed": False})
            continue
        with (folder / "events.jsonl").open() as stream:
            for line in stream:
                if '"name": "workload.' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event["phase"] not in ("load", "basic_errors"):
                    continue
                phases[event["phase"]] = event
        for name in ("load", "basic_errors"):
            event = phases.get(name)
            checks.append({"run": folder.name, "phase": name,
                "seconds": event["seconds"] if event else None,
                "passed": bool(summary.get("completed") and event
                               and event["seconds"] < seconds and not event.get("errors"))})
    result = {"target_seconds": seconds, "checks": checks,
              "passed": bool(checks) and all(c["passed"] for c in checks)}
    (Path(output) / "targets.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New directory for reports and isolated caches")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=1800, help="Maximum seconds per server process")
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--target-seconds", type=float,
                        help="Fail when loading or the first dashboard takes at least this long")
    parser.add_argument("--energy-key", default="energy")
    parser.add_argument("--force-key", default="forces")
    parser.add_argument("--pred-energy-key", default="energy_so3lr")
    parser.add_argument("--pred-force-key", default="forces_so3lr")
    parser.add_argument("--memory-mode", choices=("auto", "saving"), default="auto")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.dataset = args.dataset.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    if not args.dataset.is_file():
        parser.error("dataset does not exist")
    if args.runs < 1 or args.timeout <= 0 or args.interval <= 0:
        parser.error("runs, timeout and interval must be positive")
    if args.target_seconds is not None and args.target_seconds <= 0:
        parser.error("target-seconds must be positive")
    if args.worker:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        from ffast.profiling import Profile
        st = args.dataset.stat()
        with Profile(args.output, interval=args.interval, metadata={
            "dataset": str(args.dataset), "source_bytes": st.st_size,
            "source_mtime_ns": st.st_mtime_ns,
            "cache_directories": {k: os.environ.get(k) for k in (
                "FFAST_FRAME_INDEX_DIR", "FFAST_FRAME_ARRAYS_DIR",
                "FFAST_RESULT_CACHE_DIR", "FFAST_METRIC_CACHE_DIR")},
            "resource_overrides": {k: os.environ[k] for k in (
                "FFAST_MEMORY_BYTES", "FFAST_PARSED_CACHE_BYTES", "FFAST_RESULT_CACHE_BYTES",
                "FFAST_READ_WORKERS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS") if k in os.environ},
        }) as profile:
            asyncio.run(run_workload(args, profile))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment["FFAST_XYZ_TIMING"] = "1"
    for name, folder in (("FFAST_FRAME_INDEX_DIR", "index"),
                         ("FFAST_FRAME_ARRAYS_DIR", "parsed"),
                         ("FFAST_RESULT_CACHE_DIR", "results"),
                         ("FFAST_METRIC_CACHE_DIR", "metrics")):
        path = args.output / "cache" / folder
        path.mkdir(parents=True)
        environment[name] = str(path)
    command = [sys.executable, "-m", "ffast.profiling", str(args.dataset),
        "--worker", "--interval", str(args.interval), "--energy-key", args.energy_key,
        "--force-key", args.force_key, "--pred-energy-key", args.pred_energy_key,
        "--pred-force-key", args.pred_force_key, "--memory-mode", args.memory_mode]
    try:
        for i in range(1, args.runs + 1):
            print(f"Run {i}/{args.runs}: {args.output / f'run-{i}'}", flush=True)
            log = args.output / f"run-{i}.log"
            with log.open("w") as stream:
                child = subprocess.Popen([*command, "--output", str(args.output / f"run-{i}")],
                    env=environment, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                state = "started"
                try:
                    status = child.wait(timeout=args.timeout)
                    state = "completed" if status == 0 else "failed"
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    state = "timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                    raise
                finally:
                    (args.output / f"run-{i}-status.json").write_text(json.dumps({
                        "state": state, "exit_code": child.returncode,
                        "timeout_s": args.timeout}, indent=2) + "\n")
            if status:
                raise RuntimeError(f"Run {i} failed ({status}); see {log}")
    finally:
        write_report(args.output)
    print(f"Report: {args.output / 'report.md'}", flush=True)
    if args.target_seconds is not None:
        target = check_targets(args.output, args.target_seconds)
        if not target["passed"]:
            raise SystemExit(f"Performance target failed; see {args.output / 'targets.json'}")
        print(f"Performance target passed (<{args.target_seconds:g}s per stage).", flush=True)


if __name__ == "__main__":
    main()
