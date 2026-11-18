# Profiling laptop loading and Basic Errors

Use the Python environment that runs FFAST:

```sh
.venv/bin/python -m ffast.profiling /path/to/predictions.extxyz \
  --output temp/profiles/example --runs 2 --target-seconds 60
```

The output directory must be new. The defaults use reference `energy` and
`forces` and embedded prediction `energy_so3lr` and `forces_so3lr`; use
`--energy-key`, `--force-key`, `--pred-energy-key` and `--pred-force-key`
for another file. Every frame is selected. `--memory-mode auto` retains
FFAST's ordinary resource policy; `--memory-mode saving` is an explicit
experiment with bounded storage. Existing resource environment overrides
are retained and recorded. The built-in tab and metric defaults are used;
project configuration, saved layouts and user tabs are excluded.

The command starts a fresh server environment process for each run. The
first starts with empty FFAST caches beneath the output directory; the
second reuses them. It never deletes or changes the user's existing caches,
changes system settings, or flushes the OS cache. **First open means empty
FFAST caches, not a physically cold disk.** The original file is read in place.
Keep other heavy work idle for a controlled comparison and record unavoidable
background activity. The default limit is 1,800 seconds per process; change
it with `--timeout`. Timeout/interruption stops the benchmark's process group
and retains the JSONL evidence already written.

`--target-seconds 60` additionally writes `targets.json` and exits with failure
if either full loading or the first Basic Errors calculation in any run takes
sixty seconds or more, or reports dashboard errors. Omit the option to collect
a diagnostic profile regardless of performance. Cached tab requests are
reported separately and do not substitute for the first-calculation check.

Each run loads the dataset and its embedded predictions through `ServerSession`,
then makes the 14 requests that the default Basic Errors tab draws concurrently
through the same result store as the browser. It repeats those requests in the
same process to measure result reuse. Timelines include server thinning at
900 × 300 pixels; parity requests include density and outliers. Their x/y
selection references are not fetched as extra roots. The default energy-shift
control is left unchanged. Responses are packed and checked for failure.

This workload measures server loading, metric computation, thinning and
response packing. It excludes load-dialog probes, automatic 3D colouring,
WebSocket transport and browser rendering. For those, combine an ordinary
interactive reproduction with the existing browser timing log and `ffast
--trace` (ADR 0072). Do not present a server result as click-to-visible latency.

## Questions and evidence

The profile answers four questions:

1. Is loading spending time finding frames, parsing, hashing/learning the
   schema, extracting predictions, or writing parsed caches?
2. Are metrics waiting for memory, repeatedly reading/parsing source batches,
   reading binary cache slices, computing, or storing results?
3. Does the process use several CPU cores, and what RAM headroom remains?
4. Which work disappears on a repeated request or repeated open, and do all
   panels actually succeed?

Artifacts:

- `report.md`: comparison table and expensive operations, including dashboard
  failures. A completed profiling session does not imply every metric succeeded.
- `run-N/summary.json`: metadata, phase aggregates, resource extrema and
  sampled Python stack residency.
- `run-N/events.jsonl`: incremental nested spans, resource samples, per-request
  completion times and reply sizes/errors. Workload spans contain native parser
  phase totals when the installed extension supports `ffast_xyz.timings()`.
  Memory-saving load records include the parsed column sizes and a batch-layout
  digest, so a changed cache partition can be distinguished from an empty cache.
  Periodic stack snapshots survive timeout even without a final summary.
  Shared force passes have separate `metrics.force_scan.scan` and
  `metrics.force_scan.histogram` spans; the summary records atom count,
  completed batches, batch difference evaluations and admitted worker count.
  `_read_pair`, `_summary_batch` and `_histogram_batch` spans distinguish input
  reads from reductions. Batch spans overlap across workers; their summed wall
  times are not phase duration or CPU time.
  `readers.indexed.source_lock_wait` records acquisition of the shared source
  parser lock, separately from parsing itself. This wrapper preserves the
  original reentrant lock and is removed when profiling ends.
- `run-N.log`: ordinary FFAST logs, including fallback and refusal reasons.
- `cache/`: isolated frame indexes, parsed batches and temporary metric/results
  storage. These consume disk space under the existing cache budgets. Delete
  this directory after analysis if the repeated-open cache is no longer needed.
- `targets.json`: optional pass/fail evidence for the requested stage duration.

The recorder installs scoped timing wrappers only during an explicit profile.
Normal FFAST imports and operation do not enable profiling. Hooks restore on
failure. The profiler samples every 250 ms by default (`--interval`), includes
descendant processes in RSS samples and records CPU-seconds for the main process
at each workload boundary. It records metadata, never array values or locals.
Metadata records the loaded native parser binary's path and SHA-256, since
local profiling builds can share a package version with the installed reader.

With a native reader built from the current source and `FFAST_XYZ_TIMING=1`,
`read_runs.allocate`, `read_runs.prepare` and `read_runs.pread` split buffer
allocation, read preparation and OS byte reads. `parse_chunk.read` includes
those operations; `parse_chunk.decode` covers header/atom decoding into arrays.
`find_chunk.pread` and `find_chunk.newlines` split indexing reads from newline
search. These are elapsed times summed across native worker chunks, not CPU
time or phase duration. Nested totals must not be added together. The OS read
path includes waiting and copying cached bytes, so it is not a measurement of
physical device service time alone. Existing installed readers without these
counters still produce the coarser phase totals.

Interpret the measurements carefully:

- Average CPU cores = process CPU-seconds / wall seconds. One fully occupied
  core is 1.0, even on an eleven-core laptop. Native thread CPU is included;
  child-process CPU is available only in sampled live-tree counters.
- Span wall times include nested work and overlap across threads. They cannot
  be summed to obtain elapsed time. Memory-gate spans measure admission wait;
  streamed step spans also include source reads, arithmetic and result storage.
- Logical requested bytes count selected parser text and cached-array payloads,
  not physical device traffic. They can reveal repeated work but do not measure
  USB throughput. Native parser read/parse time also combines those two costs.
- Sampled Python stacks show where threads reside, including waits and calls
  into Rust/NumPy. They are not CPU attribution or proof of GIL contention.
- RSS summed across descendants can double-count shared pages. System memory
  and swap describe the whole host. Unavailable swap counters are `null`, never
  inferred to be zero. macOS sandboxing can prevent reading those counters.
- Profiling has overhead. Use large phase differences to locate bottlenecks;
  compare any later optimization under identical settings and validate values
  before claiming a speedup.

The reusable `ffast.profiling.Profile` context can also surround another local
workload. Its wrappers are process-wide: use one profiling session at a time.
It is a local diagnostic tool, not a production tracing API.

The initial scope was agreed on 2026-11-15: profiling and a measured report
first. The follow-up requested less than sixty seconds each for loading and
metrics. The resulting reader/cache changes are recorded in
[ADR 0103](adr/0103-large-xyz-files-are-indexed-beside-parsing.md).

For measured results and the confirmed batch-cache mismatch, see the
[SO3LR laptop report](reports/2026-11-15-so3lr-laptop-profile.md).
