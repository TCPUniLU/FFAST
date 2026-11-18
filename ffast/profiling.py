"""Opt-in local profiling. No hooks or sampling run until Profile is entered.

Run the representative workload with ``python -m ffast.profiling --help``.
Spans are wall times, including nested work; concurrent spans must not be
added to infer elapsed time. Sampled stacks describe residency, not CPU time.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import importlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
import platform
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psutil

_stack = contextvars.ContextVar("ffast_profile_stack", default=())

# Scoped wrappers leave the normal application and its scientific algorithms
# untouched. Resolve attributes before patching, and restore even on failure.
_HOOKS = {
    "ffast.core.loading_coordinator": {
        "LoadingCoordinator": ["loadDataset", "_loadWithReader", "_readArrays",
                               "_loadPredictionsFromKeys", "prepareFrameIndex"],
    },
    "ffast.readers.text": {None: ["get_table", "read_level2", "_read_fast_arrays",
                                  "_read_spans"]},
    "ffast.readers.indexed": {"IndexedStore": ["__init__", "_load_manifest",
        "_save_manifest", "_part", "_cached_slice", "_save", "_observe"]},
    "ffast.readers.dataset": {"ReaderDataset": ["__init__", "getFingerprint"]},
    "ffast.metrics.server_executor": {"ServerExecutor": ["_step"]},
    "ffast.metrics.resources": {"MemoryGate": ["reserve"]},
    "ffast.metrics.execution": {None: ["build_execution_plan"]},
    "ffast.metrics.streaming": {None: ["execute"]},
    "ffast.metrics.force_scan": {None: ["scan", "histogram", "_read_pair",
                                      "_summary_batch", "_histogram_batch"]},
    "ffast.io.result_array": {None: ["store_result"]},
    "ffast.io.budget": {"Budget": ["cached"]},
    "ffast.metrics.cache": {"MetricCache": ["get", "put"]},
    "ffast.metrics.result_store": {"MetricResultStore": ["_compute"]},
}


class _TimedSourceLock:
    """Profile acquisition only; preserve the original RLock and its lifetime."""

    def __init__(self, profile, lock):
        self.profile, self.lock = profile, lock

    def __enter__(self):
        held = False
        try:
            with self.profile.span("readers.indexed.source_lock_wait"):
                self.lock.acquire()
                held = True
        except BaseException:
            if held:
                self.lock.release()
            raise
        return self

    def __exit__(self, *exc):
        self.lock.release()


class Profile:
    """One process-wide profiling session, including its worker threads.

    JSONL is written incrementally so interrupted long runs retain evidence.
    Only metadata is captured: no array contents, locals or environment dump.
    ``output`` must be a new directory, preventing accidental report overwrite.
    """

    def __init__(self, output, *, interval=0.25, metadata=None):
        if interval <= 0:
            raise ValueError("sampling interval must be positive")
        self.output = Path(output)
        self.interval = interval
        self.metadata = dict(metadata or {})
        self.phase = "setup"
        self.lock = threading.RLock()
        self.finished = threading.Event()
        self.hooks = []
        self.aggregates = {}
        self.stacks = Counter()
        self.samples = []
        self.serial = 0
        self.last_stack_write = -2.0

    def _write(self, record):
        with self.lock:
            self.stream.write(json.dumps(record, allow_nan=False) + "\n")

    @contextmanager
    def span(self, name, **fields):
        started = time.perf_counter()
        previous = _stack.get()
        with self.lock:
            self.serial += 1
            ident = self.serial
        frame = {"id": ident}
        token = _stack.set((*previous, frame))
        phase = self.phase
        failed = False
        try:
            yield fields
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = time.perf_counter() - started
            _stack.reset(token)
            row = {"type": "span", "id": ident,
                   "parent": previous[-1]["id"] if previous else None,
                   "phase": phase, "name": name,
                   "start_s": started - self.started, "seconds": elapsed,
                   "thread": threading.current_thread().name,
                   "failed": failed, **fields}
            self._write(row)
            key = (phase, name, str(fields.get("metric", "")))
            with self.lock:
                agg = self.aggregates.setdefault(key, {
                    "phase": phase, "name": name, "metric": key[2],
                    "calls": 0, "seconds": 0.0, "max_s": 0.0,
                    "failures": 0, "hits": 0, "misses": 0,
                    "logical_bytes": 0})
                agg["calls"] += 1
                agg["seconds"] += elapsed
                agg["max_s"] = max(agg["max_s"], elapsed)
                agg["failures"] += failed
                if "hit" in fields:
                    agg["hits" if fields["hit"] else "misses"] += 1
                agg["logical_bytes"] += fields.get("logical_bytes", 0)

    def _wrap(self, original, name):
        @functools.wraps(original)
        def measured(*args, **kwargs):
            fields = {}
            if name.endswith("ServerExecutor._step"):
                fields["metric"] = args[1]
                from ffast.io.batched import BatchedArray
                fields["streamed"] = any(
                    isinstance(v, BatchedArray) for v in args[4].values())
            if name.endswith("_read_fast_arrays"):
                # Selected text bytes requested, not physical device reads.
                fields["logical_bytes"] = int((args[5] - args[4]).sum())
            with self.span(name, **fields) as detail:
                result = original(*args, **kwargs)
                if name == "metrics.force_scan.scan":
                    detail.update({"batches": result.batches, "atoms": result.atoms,
                                   "difference_evaluations": result.batches,
                                   "workers": result.workers})
                if name.endswith(("._load_manifest", ".cached", "._cached_slice",
                                  "MetricCache.get")):
                    detail["hit"] = (bool(result) if name.endswith("_load_manifest")
                                     else result is not None)
                if name.endswith("._cached_slice") and result is not None:
                    detail["logical_bytes"] = int(result.nbytes)
                if name.endswith("._read_spans"):
                    detail["logical_bytes"] = len(result)
                return result
        return measured

    def _install(self):
        for module_name, owners in _HOOKS.items():
            module = importlib.import_module(module_name)
            for owner_name, names in owners.items():
                owner = getattr(module, owner_name) if owner_name else module
                for name in names:
                    # getFingerprint may be inherited: deleting the temporary
                    # attribute restores inheritance instead of copying it.
                    own = name in vars(owner)
                    original = getattr(owner, name)
                    label = module_name.removeprefix("ffast.")
                    label += "." + (owner_name + "." if owner_name else "") + name
                    self.hooks.append((owner, name, original, own))
                    setattr(owner, name, self._wrap(original, label))
        indexed = importlib.import_module("ffast.readers.indexed")
        original = indexed._parse_lock
        self.hooks.append((indexed, "_parse_lock", original, True))
        indexed._parse_lock = _TimedSourceLock(self, original)

    def _sample(self):
        process = psutil.Process()
        processes = [process]
        try:
            processes += process.children(recursive=True)
        except (psutil.Error, OSError):
            pass
        rss = cpu = threads = 0
        for proc in processes:
            try:
                rss += proc.memory_info().rss
                times = proc.cpu_times()
                cpu += times.user + times.system
                threads += proc.num_threads()
            except (psutil.Error, OSError):
                pass
        memory = psutil.virtual_memory()
        try:
            swap = psutil.swap_memory()
        except (psutil.Error, OSError):
            swap = None
        sample = {"type": "sample", "phase": self.phase,
                  "at_s": time.perf_counter() - self.started,
                  "process_tree_rss_bytes": rss,
                  "live_process_tree_cpu_s": cpu, "threads": threads,
                  "system_available_bytes": memory.available,
                  "system_swap_used_bytes": swap.used if swap else None,
                  "system_swap_in_bytes": swap.sin if swap else None,
                  "system_swap_out_bytes": swap.sout if swap else None}
        self.samples.append(sample)
        self._write(sample)
        # Sampling Python stacks does not profile native Rust/NumPy CPU work.
        # Keep only repository frames, never filenames from users' home trees.
        observed = []
        for ident, frame in sys._current_frames().items():
            if ident == threading.get_ident():
                continue
            chain = []
            while frame is not None:
                filename = frame.f_code.co_filename.replace("\\", "/")
                if "/ffast/" in filename and not filename.endswith("/profiling.py"):
                    chain.append(filename.split("/ffast/", 1)[1] + ":" +
                                 str(frame.f_lineno) + ":" + frame.f_code.co_name)
                frame = frame.f_back
            if chain:
                self.stacks[(self.phase, " ← ".join(chain[:5]))] += 1
                observed.append({"thread_id": ident, "stack": chain[:5]})
        if sample["at_s"] - self.last_stack_write >= 2:
            self._write({"type": "stack_sample", "phase": self.phase,
                         "at_s": sample["at_s"], "threads": observed})
            self.last_stack_write = sample["at_s"]

    def _monitor(self):
        while not self.finished.wait(self.interval):
            try:
                self._sample()
            except (psutil.Error, OSError) as exc:
                self._write({"type": "sampling_error", "error": str(exc)})

    def __enter__(self):
        self.output.mkdir(parents=True, exist_ok=False)
        self.stream = (self.output / "events.jsonl").open("w", buffering=1)
        self.started = time.perf_counter()
        self.cpu_started = time.process_time()
        self.metadata.update({"python": sys.version, "platform": platform.platform(),
            "pid": os.getpid(), "started_utc": datetime.now(timezone.utc).isoformat(),
            "cpu_count": os.cpu_count(), "ram_bytes": psutil.virtual_memory().total,
            "sample_interval_s": self.interval,
            "notes": ["Span times are inclusive and overlap across threads.",
                      "Logical bytes are requested payload, not device I/O.",
                      "Stack samples measure residency, not CPU attribution.",
                      "Tree RSS can double-count shared pages; system swap is host-wide."]})
        packages = {}
        for name in ("numpy", "scipy", "ase", "psutil", "ffast-xyz"):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = None
        self.metadata["packages"] = packages
        try:
            native = importlib.import_module("ffast_xyz._ffast_xyz")
            with open(native.__file__, "rb") as binary:
                digest = hashlib.file_digest(binary, "sha256").hexdigest()
            self.metadata["native_parser"] = {"path": native.__file__, "sha256": digest}
        except (ImportError, OSError):
            self.metadata["native_parser"] = None
        self._write({"type": "metadata", **self.metadata})
        try:
            self._install()
            self._sample()
            self.monitor = threading.Thread(target=self._monitor,
                name="ffast-profiler", daemon=True)
            self.monitor.start()
        except BaseException:
            self._restore()
            self.stream.close()
            raise
        return self

    def _restore(self):
        for owner, name, original, own in reversed(self.hooks):
            if own:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
        self.hooks.clear()

    def __exit__(self, kind, exc, tb):
        self.finished.set()
        self.monitor.join(timeout=5)
        try:
            self._sample()
        finally:
            self._restore()
        elapsed = time.perf_counter() - self.started
        cpu = time.process_time() - self.cpu_started
        summary = {"metadata": self.metadata, "completed": kind is None,
            "error": None if exc is None else str(exc),
            "elapsed_s": elapsed, "process_cpu_s": cpu,
            "average_process_cores": cpu / max(elapsed, 1e-9),
            "peak_process_tree_rss_bytes": max(s["process_tree_rss_bytes"] for s in self.samples),
            "min_system_available_bytes": min(s["system_available_bytes"] for s in self.samples),
            "phases": sorted(self.aggregates.values(), key=lambda a: -a["seconds"]),
            "stack_residency": [{"phase": phase, "stack": stack, "samples": n}
                                for (phase, stack), n in self.stacks.most_common(100)]}
        (self.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        self.stream.close()


def main():
    # Keep the reusable recorder independent of the benchmark/bootstrap code.
    from ffast.profiling_workload import main as workload
    workload()


if __name__ == "__main__":
    main()
