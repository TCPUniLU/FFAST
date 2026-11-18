# SO3LR laptop profile, 2026-11-15

**Current 80% RAM policy:** Basic Errors improved from 27.5 to 23.2 seconds in
the same-cache comparison. A new-cache full run loaded in 34.1 seconds and
calculated Basic Errors in 32.1 seconds; its cache retained fewer binary
batches. Both sixty-second targets also passed in a fresh server reopening
the dataset. See the latest follow-up below; the initial
measurements and diagnosis that motivated these changes remain recorded here.

The slow workflow reproduces. The first open took 100.6 seconds and the first
Basic Errors calculation took 291.8 seconds. Repeating the same tab requests
in that session took 0.52 seconds. The metric workload repeatedly parses the
source rather than spending most of its time on the final scalar arithmetic.
Reopening the dataset took 1.79 seconds, but recalculating Basic Errors in
that fresh server took 513.65 seconds: its binary batch cache was missed.

## Scope and reproduction

The initial deliverable was profiling and a measured bottleneck report, with
performance changes proposed separately. The subsequent request set a
sixty-second target for loading and metrics; its follow-up results appear
below. Every frame is retained, laptop
memory limits stay enabled, and system caches/settings are unchanged.

```sh
.venv/bin/python -m ffast.profiling \
  /Volumes/ANT_Shield/Data/PhD/Omega_paper/datasets/so3lr/pred/so3lr_train_SO3LR_PRED_REORDERED.extxyz \
  --output temp/profiles/so3lr-laptop-2026-11-15 --runs 2 --timeout 1800
```

The output path must be new when repeating this command. See the
[profiling guide](../performance-profiling.md) for other prediction field names,
the recorder, and artifact interpretation.

- Source: 16,238,977,614 bytes, 3,856,645 frames. Reference `energy`/`forces`;
  embedded prediction `energy_so3lr`/`forces_so3lr`.
- Machine: macOS 26.6.2, arm64, 11 reported CPU cores, 18 GiB RAM.
- Environment: Python 3.11.14, NumPy 1.26.4, SciPy 1.17.0, ASE 3.27.0,
  psutil 7.2.2, usable `ffast-xyz` 0.1.0 with contract 1.
- Both runs use memory-saving mode automatically, with about 3.2 GiB of
  shared RAM budget. No resource environment overrides were set.
- Run 1 has empty isolated FFAST caches. Run 2 starts a fresh server process
  and reuses those caches. Each run also repeats the tab requests in-process.
- The workload issues the actual 14 drawn Basic Errors requests concurrently
  through `ServerSession` and `MetricResultStore`, including timeline thinning
  and response packing. It excludes browser drawing, transport, load-dialog
  probing and automatic 3D colouring. The energy-shift control uses its default.

## Measurements

| Operation | First server / empty FFAST cache | Fresh server / reused FFAST cache |
|---|---:|---:|
| Load full dataset and embedded prediction | 100.60 s | 1.79 s |
| First Basic Errors calculation | 291.84 s | 513.65 s |
| Repeat Basic Errors in same session | 0.52 s | 0.51 s |

Run 1: peak sampled process-tree RSS 2.02 GiB; minimum system available RAM
3.54 GiB. Loading averaged 0.97 CPU cores; Basic Errors averaged 3.63 CPU
cores. A core count is process CPU-seconds divided by wall seconds and includes
native threads; it is not a percentage of the entire machine.

Run 2: peak sampled process-tree RSS 1.81 GiB; minimum system available RAM
3.64 GiB. Its metric calculation averaged 3.51 CPU cores. All fourteen
requests succeeded in both phases of both runs. Both datasets had fingerprint
`58c34bf5ed6206670844a63689c64e87`.

Loading breaks down as follows. These boundaries are sequential except where
the nested inclusion is stated explicitly:

| Run 1 loading work | Wall time |
|---|---:|
| Find frame boundaries | 36.86 s |
| Indexed storage initialization | 63.27 s |
| ↳ Parse selected text into batches | 38.07 s |
| ↳ Observe schema / hash columns | 15.60 s |
| ↳ Write parsed batches, included in batch-read time | 2.77 s |
| Extract embedded prediction after reading | 0.024 s |

Native frame-index timing reports 35.86 s in read/newline processing and
0.93 s walking the frames. Read/newline processing combines I/O and CPU work;
it is not an isolated disk-bandwidth measurement.

## Findings

### 1. Metric computation reparses much more text than the input size

In the first Basic Errors calculation, `_read_fast_arrays` was called
243,554 times and was asked to parse 238,694,724,405 selected text bytes:
238.7 GB, or 14.70 complete-file equivalents. These are **logical requested
bytes**, not measured physical disk reads. OS caching can satisfy some reads.

The native read/parse total is 1,088.2 seconds across concurrent calls;
inclusive Python wrapper time is 1,210.4 seconds. Neither is elapsed wall
time and neither should be added to the 291.8-second workload duration.
Sampled stacks repeatedly reside in `XyzReader.parse_into` through
`IndexedStore._part`, consistent with the recorded parse workload.

| First metric calculation in each server | Run 1 | Run 2 |
|---|---:|---:|
| Parser wrapper calls | 243,554 | 406,414 |
| Logical selected source text | 238.69 GB | 434.53 GB |
| Complete-file equivalents | 14.70 | 26.76 |
| Binary slice cache hits / attempts | 70,834 / 207,070 | 0 / 207,106 |

The parsed columns total about 7.55 GiB. Each energy column occupies just
29.42 MiB, yet reading an uncached scalar column still reparses the atom
columns of its source frames. Retaining those small reused columns is a
different resource choice from retaining every parsed atom array.

The relevant path is `IndexedStore.column.read` → `_read_ranges` → `_part` →
`_read_fast_arrays`. A missing binary slice causes a complete source-frame
parse, including atom columns, even when a metric requests only energies.
Energy reductions, density construction, parity bounds/histograms and
outliers make multiple passes over these virtual arrays. Shared dependencies
avoid duplicate result computations, but a shared lazy array can still read
and parse its source again for every consumer or pass.

### 2. A small RAM-budget change makes retained parsed batches unreachable

Run 1 recorded 70,834 successful binary slice reads from 207,070 attempts.
Run 2 recorded zero successful binary slice reads from 207,106 attempts,
despite retained parsed files and a successful manifest load.

Code inspection and a reconstruction using the saved frame index confirm
the cache filename mismatch. `IndexedStore._ranges`
derives batch boundaries from the current RAM budget; `_cache_path` includes
those exact frame boundaries. A slightly different automatic budget can
therefore look for different filenames while the manifest identity still
matches. The two budgets were 3,300.6 and 3,270.5 MiB, a change of only 0.91%.

| Reconstructed cache partition | Run 1 budget | Run 2 budget |
|---|---:|---:|
| Batch count | 151 | 152 |
| First frame range, end exclusive | 0–18,678 | 0–18,584 |
| Retained on-disk batches with matching filenames | 66 | 0 |

The reconstruction calls the existing `_ranges` implementation with the two
recorded budgets, then checks its exact filenames against the retained cache.
The resulting ranges and column sizes are saved in `layout_check.json`.
This explains the loss of binary cache reuse. It does not isolate every
contribution to the elapsed-time difference between the two observational runs.

### 3. Raising worker counts alone does not address the measured work

The fast parser is present and eleven parser threads are available. In run 1,
the twenty metric memory-gate reservations totalled only 0.010 seconds of
wall time, so waiting at that gate did not explain minutes of latency.

The measured 3.63 average CPU cores during metrics leaves capacity unused,
but does not establish that adding threads is the right fix. There are many
small parser calls, redundant scans and cache misses. The native parser
also constructs a Rayon thread pool on each `parse_into` call; that cost is
currently combined with native read/parse time and has not been isolated.
Run 2 also spent only 0.009 seconds across the twenty memory-gate reservations.

### 4. Some eager metrics can be refused even for tiny inputs

A separate small-fixture validation exposed 4,096 MiB working-memory hints
on force parity density/outliers. The eager executor reserves that hint
against the shared budget, which was smaller than 4 GiB on this laptop.
The profiler preserves these refusals as dashboard failures and continues
the comparison. This is separate from the large-file run: all fourteen
requests in run 1 succeeded through the streaming path.
The second full-file run also completed all requests successfully.

## Original proposed next work

1. Make parsed-cache partitions reusable across changes to the available RAM
   budget. Verify a fresh process actually gets binary slice hits.
2. Retain frequently reused small columns or intermediates within the shared
   budget, especially energies. Compare parser call counts and logical source
   bytes, as well as elapsed time.
3. Share compatible statistics/histogram/outlier scans where practical,
   preserving current numeric definitions and full-dataset coverage.
4. Measure parser pool construction separately and compare larger bounded
   batches or pool reuse. Do this after removing avoidable re-parsing.
5. Review the eager force-parity memory hints against actual working-set needs.

Each change should preserve scientific values and fingerprints, keep resource
limits enabled, and repeat this workload before claiming a speedup. No
performance algorithms or resource policies were changed by the initial
profiling work. The subsequent optimization is documented below and in ADR 0103.

## Artifacts and limits

Raw artifacts remain under `temp/profiles/so3lr-laptop-2026-11-15/`, which is
ignored by Git: per-run `events.jsonl`, `summary.json` and ordinary logs.
The generated `report.md` contains operation totals and native
phases. The repository report preserves the findings without committing the
large traces or cache files.
The original artifacts and cache occupied about 4.1 GiB. Generated caches
were removed between optimization experiments to restore disk headroom;
their inventories, raw measurements and `layout_check.json` were retained.

Validation: four profiler tests pass, covering real eager/streamed dashboard
requests across fresh processes, captured memory refusals, restoration after
failure, and timeout reporting. Related loading/executor/resource tests:
55 passed, one skipped. The large-file workflow completed both server runs
and both repeated-request phases without metric errors.

These are observational runs on a working laptop, not an isolated benchmark
distribution. The first frame-index scan briefly overlapped 12.6 seconds of
verification tests; later metrics ran after those tests completed. OS/device
cache state is uncontrolled. Profiling overhead has not been separately
quantified. macOS sandboxing made swap counters unavailable, so they are null
rather than evidence of no swap. No physical device I/O counters were available
in this recording. Browser-visible latency may include additional costs.

## Follow-up: verified sixty-second targets

The user requested less than sixty seconds each for the first load and for
the first Basic Errors calculation, and asked to use more CPU and RAM.
The implementation now overlaps bounded Rust indexing and parsing, hashes
independent columns on up to four threads, retains admitted scalar columns,
and shares large source windows between metric consumers. Persistent cache
partitions survive a changed RAM budget. Metric scans preserve their existing
disk cache rather than evicting the next part of their input; small schema
records and scalar columns receive cache admission priority.

The parser remains the installed Rust package. Its code and the scientific
metric definitions were not changed. At this stage, total laptop memory policy
remained 60% of available memory, with existing admission checks and headroom;
the later 80% trial is recorded below.
Useful retention and concurrent processing make better use of that allowance.
See [ADR 0103](../adr/0103-large-xyz-files-are-indexed-beside-parsing.md).

The definitive comparison used a new empty FFAST cache, the same original
file on ANT_Shield, default laptop limits and this command:

```sh
.venv/bin/python -m ffast.profiling \
  /Volumes/ANT_Shield/Data/PhD/Omega_paper/datasets/so3lr/pred/so3lr_train_SO3LR_PRED_REORDERED.extxyz \
  --output temp/profiles/so3lr-laptop-verified-2026-11-15 \
  --runs 2 --timeout 900 --target-seconds 60
```

| Operation | Original first server | Verified first server | Verified fresh-server reopen |
|---|---:|---:|---:|
| Full load with embedded prediction | 100.60 s | 32.10 s | 1.87 s |
| First Basic Errors calculation | 291.84 s | 47.78 s | 46.92 s |
| Repeat tab requests in same session | 0.52 s | 0.49 s | 0.87 s |

The command exited successfully and `targets.json` records all four load/first
metric checks passing. Every one of the fourteen drawn requests succeeded in
both dashboard phases of both runs. All 3,856,645 frames remain selected.
The dataset fingerprint is still `58c34bf5ed6206670844a63689c64e87`; the embedded
prediction fingerprint is still `5d48403a8e5d58c2bc121e45a066f3b9`.

Loading averaged 3.19 CPU cores, compared with 0.97 in the initial profile.
Peak sampled process-tree RSS was 2.45 GiB on the first run and 1.63 GiB on
reopen; the respective shared RAM budgets were 3.09 and 3.07 GiB. Minimum
system available memory was 3.47 and 3.81 GiB. No total RAM-limit increase or
system-setting change was needed.

The first dashboard requested 25.36 GB of source text, down from 238.69 GB.
Its binary cache served 102,252 of 104,532 slice reads. On reopen it again
served 102,252 of 104,532 reads, rather than the original zero hits.
The valid manifest was reused; no full indexing or metadata scan occurred.

Validation included 1,309 passing reader/loading/executor/resource tests
(one skipped), 66 passing frame-index/cache/fingerprint/profiler checks, and
focused pipeline cancellation and disk-pressure checks. Some suites overlap;
these counts are not a unique-test total. Regression coverage includes changed
RAM budgets, source mutation, exact fingerprints/values, optional-parser
fallback, partial-cache geometry reads, no dropped frames on scanner handover,
bounded workspaces and cancellation without publishing partial metadata.

Artifacts are under `temp/profiles/so3lr-laptop-verified-2026-11-15/`, including
the generated report, full traces, summaries, strict target results and the
isolated cache. The laptop/cache-state and browser-exclusion limits described
above still apply. These measured results meet the requested target for this
dataset; they do not promise the same time for every drive or background load.

## Follow-up: shared force passes

Basic Errors' separate result requests now share force-error computation.
The first bounded pass reads reference and predicted forces once, subtracts
once per batch, and produces dataset/per-structure MAE and RMSE, worst
components with their true/predicted values, and parity bounds. The parity
histogram uses those bounds in a second pass without recalculating differences.
Timelines and distributions read the small saved per-structure series.
No complete force-difference array is retained.

Result IDs and definitions stay the same. Multiple panel tasks can still be
listed while they wait for shared computation. Custom formulas, other box
settings and sources without stable identities use the previous engine. See
[ADR 0104](../adr/0104-related-force-metrics-share-batched-passes.md) for matching,
storage, cancellation, invalidation and numerical-equivalence rules.

The counting regression test makes six force-result requests concurrently.
Before this change, each input was read ten times. Now each is read exactly
twice, or once if no parity histogram is requested. Tests compare against the
existing metrics at `rtol=1e-12` and downstream curves at `rtol=1e-11`, including
variable and empty structures, non-finite values/overflow and multiple batches.
They also exercise shared cancellation, retry, object/result invalidation,
custom definitions, spill and insufficient-storage fallback.

The relevant metric/expression, executor, result-store, resource, reader and
session regression suites passed 568 checks (one skipped). The final focused
force-sharing, executor and profiler run passed 27 checks, including an isolated
custom registry that must not load built-ins. These suite counts overlap.

Full-file command, with a new isolated cache and unchanged resource policy:

```sh
.venv/bin/python -m ffast.profiling \
  /Volumes/ANT_Shield/Data/PhD/Omega_paper/datasets/so3lr/pred/so3lr_train_SO3LR_PRED_REORDERED.extxyz \
  --output temp/profiles/so3lr-force-shared-2026-11-15 \
  --runs 2 --timeout 900 --target-seconds 60
```

| Operation | Before shared passes | Shared passes, first server | Shared passes, reopen |
|---|---:|---:|---:|
| Full load | 32.10 s | 40.16 s | 1.86 s |
| First Basic Errors calculation | 47.78 s | 37.52 s | 36.98 s |
| Repeat tab requests | 0.49 s | 0.53 s | 0.59 s |

The two profiles are observational laptop runs with uncontrolled OS/device
cache state, so differences in loading time are not attributed to the metric
change. All four strict load/first-metric target checks passed, all fourteen
outputs succeeded in each phase, and the dataset fingerprint remained
`58c34bf5ed6206670844a63689c64e87`.

Each fresh server recorded exactly one summary call over **230 batches and
80,227,586 atoms**, with **230 batch difference evaluations**, then exactly one
histogram call. Source-text parsing requests fell from 25.36 GB to 6.44 GB;
binary slice requests fell from 104,532 to 1,400. These remain logical payload
measurements, not physical device bytes. Whole-process CPU time during the
first dashboard fell from 169.46 CPU-seconds to 39.60 CPU-seconds. Its sampled
process-tree RSS peaked at 1.94 GiB, versus 2.45 GiB in the preceding profile.

The new phase counters appear in `events.jsonl` under `metrics.force_scan.scan`
and `metrics.force_scan.histogram`; ordinary logs also report completed batches.
Artifacts and `targets.json` remain under
`temp/profiles/so3lr-force-shared-2026-11-15/`. The preceding experiment's
generated cache was removed to restore disk headroom; its raw measurements
and cache inventory remain available.

## Follow-up: bounded parallel force batches

The shared summary and histogram now overlap reads and computation across up
to four in-flight batches on the existing server chunk pool. CPU availability
and RAM headroom can lower that number. Each lane reserves its working memory
before dispatch; a completed batch retains only its small reduction outputs.
The producing request merges partials in source order and writes per-structure
results. No complete force pair or difference is queued for the whole dataset.
Queued work is cancelled and running work joined before its reservation is
released on failure/cancellation. Single-CPU and small-budget jobs run the same
kernel synchronously. The shared one-subtraction-per-batch rule still applies.

Full-file command:

```sh
.venv/bin/python -m ffast.profiling \
  /Volumes/ANT_Shield/Data/PhD/Omega_paper/datasets/so3lr/pred/so3lr_train_SO3LR_PRED_REORDERED.extxyz \
  --output temp/profiles/so3lr-force-parallel-2026-11-15 \
  --runs 2 --timeout 900 --target-seconds 60
```

| Operation | First server | Fresh-server reopen |
|---|---:|---:|
| Full load | 31.69 s | 1.87 s |
| First Basic Errors calculation | 30.02 s | 26.58 s |
| Average CPU cores during Basic Errors | 1.86 | 2.10 |
| Peak process-tree RSS, whole run | 2.32 GiB | 1.83 GiB |

All four strict target checks passed. All fourteen outputs succeeded in both
dashboard phases of both runs. Each server recorded one summary over 230
batches and 80,227,586 atoms, exactly 230 batch subtractions, and one histogram
pass. The dataset fingerprint remained `58c34bf5ed6206670844a63689c64e87`.

This run had less free disk space than the preceding shared-pass experiment,
so fewer binary force batches were retained: 964 of 1,400 slice requests hit
disk cache, versus 1,124 before. Source parsing increased to 10.20 GB from
6.44 GB. Comparing 37.52 and 30.02 seconds alone therefore mixes scheduling
and cache conditions. Loading was unchanged by this follow-up; its time
variation is not assigned to force-batch parallelism.

A second comparison reused exactly the new experiment's saved parsed cache,
in fresh server processes. The small harness `compare-workers.py` is retained
beside its traces. It only changes the private force-lane selector during an
explicit profile; it keeps CPU and memory admission checks enabled.

| Requested lane cap | Admitted lanes | Basic Errors | Average CPU cores | Peak process-tree RSS |
|---|---:|---:|---:|---:|
| 1 | 1 | 52.54 s | 1.03 | 1.37 GiB |
| 4 | 4 | 27.22 s | 2.07 | 1.78 GiB |
| 8 | 6 | 29.64 s | 1.93 | 1.64 GiB |

Every comparison produced fourteen successful outputs. Each read exactly the
same logical source payload (10,200,346,210 bytes), made 5,276 native parser
calls and 1,400 binary slice requests, and recorded 964 binary hits. The
automatic RAM caps varied from 2.50 to 2.77 GiB with available host memory;
the saved source partition and observed input work were identical. OS/device
caches and background load remained uncontrolled, so these are observational
measurements rather than a statistical scaling study. Six lanes did not beat
four in this comparison; normal operation consequently retains a four-lane
cap. CPU use remains below total laptop capacity: input fetching/parsing,
including serial preparation and I/O waits, dominates the parallel scan.
The measurements do not isolate physical-drive bandwidth from parser overhead.

Replay a comparison while that generated cache is retained (choose a new
output directory):

```sh
.venv/bin/python temp/profiles/so3lr-force-parallel-2026-11-15/compare-workers.py \
  4 temp/profiles/so3lr-force-parallel-2026-11-15/cache \
  temp/profiles/so3lr-force-parallel-2026-11-15/replay-four \
  /Volumes/ANT_Shield/Data/PhD/Omega_paper/datasets/so3lr/pred/so3lr_train_SO3LR_PRED_REORDERED.extxyz
```

Use `serial` for one lane or `8` for a higher cap subject to resource admission.
The `serial-control`, `four-control` and `eight-control` directories retain
their complete events and summaries. `_read_pair`, `_summary_batch` and
`_histogram_batch` spans show the batch pipeline; `scan` records its admitted
worker count. Nested/concurrent span times must not be summed as phase time.

Validation passed 327 relevant metric, expression, executor, result-store,
resource, reader and session checks (one skipped). Coverage includes numerical
equivalence with one/four/eight lanes, unchanged two-pass force read counts,
actual batch overlap, source-order result merging, cancellation and read
failure with in-flight workers drained before workspace release, and retry.
Overflow/divide-by-zero fixtures produce the expected reference-engine warnings.

## Follow-up: 80% laptop RAM allowance

At the user's request, the automatic workstation share increased from 60% to
80% of memory available when the budget is created. Keep the 512 MiB minimum
headroom when available, allocation-time checks against live free memory,
cache eviction, explicit project/environment overrides and the separate 85%
cgroup/Slurm policy. The budget does not automatically grow later in the
session. This trial isolates the initial share; concurrency remains four.

Fresh servers reopened the complete dataset using the same saved parsed
cache at `temp/profiles/so3lr-force-parallel-2026-11-15/cache/`:

| Measurement | 60% before change | 80% after change |
|---|---:|---:|
| Admitted RAM budget | 2.89 GiB | 3.71 GiB |
| Basic Errors | 27.51 s | 23.19 s |
| Average CPU cores during Basic Errors | 1.97 | 2.25 |
| Peak process-tree RSS, whole run | 2.00 GiB | 2.05 GiB |
| Minimum host available RAM | 3.46 GiB | 3.38 GiB |
| Native parser calls during Basic Errors | 5,276 | 134 |
| Logical source text parsed | 10.20 GB | 10.29 GB |

Basic Errors was 15.7% faster in this comparison. The source batch-layout
digest, four-lane concurrency, 230 summary batches, 80,227,586 atoms and
dataset fingerprint stayed the same. All fourteen outputs succeeded.
The larger allowance admits canonical source windows that previously exceeded
the reader's one-third-of-budget workspace threshold. Bigger Rust parser calls
replace many small calls, while reference/prediction consumers share their
parsed window. The allowance is a cap; FFAST does not allocate RAM just to
reach 80%. The measured retained/process-memory increase was small.

An additional 80% fresh-server repeat on this cache took 23.30 seconds and
again returned fourteen successful outputs (`ram80-repeat/`).
The raw evidence is in `ram60-before/`, `ram80-after/` and `ram80-repeat/` under
that profile directory. These runs use automatic startup sizing, so host availability and admitted
budgets differ; OS/device caches remain uncontrolled. This is an observational
comparison, not a guaranteed speedup for every host/cache state. Host swap
counters were unavailable, so no swap-free claim is made.

The current default was also checked with a new empty FFAST cache:

```sh
.venv/bin/python -m ffast.profiling \
  /Volumes/ANT_Shield/Data/PhD/Omega_paper/datasets/so3lr/pred/so3lr_train_SO3LR_PRED_REORDERED.extxyz \
  --output temp/profiles/so3lr-ram80-2026-11-15 \
  --runs 2 --timeout 900 --target-seconds 60
```

| Operation | First server | Fresh-server reopen |
|---|---:|---:|
| Full load | 34.10 s | 1.84 s |
| First Basic Errors calculation | 32.11 s | 31.91 s |
| Repeat tab requests | 0.64 s | 0.54 s |
| Admitted RAM budget | 3.36 GiB | 4.06 GiB |
| Peak process-tree RSS, whole run | 2.47 GiB | 1.99 GiB |
| Minimum host available RAM | 3.16 GiB | 3.12 GiB |

All four strict target checks passed and fourteen outputs succeeded in every
dashboard phase. The source dataset fingerprint stayed
`58c34bf5ed6206670844a63689c64e87`. This later experiment had still less free
disk space: only 752 binary force-slice requests hit, and 15.15 GB of source
text was reparsed. Its 32-second dashboard must therefore not be compared
directly with the better-cached 23-second trial as a RAM-only difference.

The dominant cost remains input access. The first file-index read/newline
phase took 30.23 seconds within the 34.10-second load, overlapping parsing.
During Basic Errors, `_read_pair` occupied 118.54 of 126.67 summed batch-worker
seconds (93.6%); these include source/cache access and waits for shared parsing.
They are overlapping worker wall times, not CPU seconds or additive phase
durations. The summary pass took 16.42 elapsed seconds and the parity pass
15.65 seconds. Physical disk waiting and native parser work are not isolated
by these spans. Retaining more reusable binary force data is a stronger next
candidate than increasing the arithmetic worker count.

Validation passed 30 storage-policy/force-sharing/profiler checks and 54
resource/reader checks (one skipped), including the 80% workstation default,
unchanged 85% constrained-job policy, 512 MiB headroom and explicit overrides.
Artifacts and strict target results remain in
`temp/profiles/so3lr-ram80-2026-11-15/`.
