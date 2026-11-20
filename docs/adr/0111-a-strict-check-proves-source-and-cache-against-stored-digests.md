# A strict check proves source and cache against stored digests

Status: Accepted and implemented 2026-11-20. Builds on ADRs 0107, 0108 and 0110.
Spec v1.1 §4.3 (validation policies), step 7 of the agreed order.

FFAST only ever runs the fast check (spec §4.3 "Fast"):

- A parsed cache is trusted when the source file's size, modification time
  and inode match (`IndexedStore.check_source`). A file rewritten with the
  same size, its modification time set back (`rsync -t`, `touch -r`, a
  restore from backup), passes.
- A bundle (ADR 0110) is trusted when every batch file exists with its
  recorded byte size. A batch damaged in transfer, with the right size, is
  read as if it were good: a user plots wrong forces without being told.
- The chunk digests written by ADR 0107 (`<id>.chunks.npz`, 32 bytes per
  16,384 rows per column) exist exactly for this check, and nothing read
  them back against the data.

Those digests cover the *parsed values*, not the bytes of the source text.
So the way to prove a source unchanged is to parse it again and compare:
about 12 s for the 10 GB benchmark file.

Spec §4.3 asks for an optional strict policy that verifies stored
cryptographic hashes of all required source and cache content, accounts for
the extra reading, and reports what was actually verified, particularly when
the source moved or is missing. It also asks that any source fingerprint be
computed during ingestion without an avoidable extra full read.

Example of the wanted behaviour. On MeluXina a user loads `so3lr_10GB.xyz`,
exports `so3lr.ffast` and copies it to a laptop. One batch arrives damaged.
On the laptop they run `ffast-cli cache verify so3lr.ffast/dataset.ffast.json`:

```
so3lr.ffast  (bundle, schema 1)
  source   not found (looked in /scratch/u/so3lr_10GB.xyz and beside the bundle); not verified
  batches  411 of 412 verified; 81920-86016.npz: forces differ
1 mismatch. The bundle was not changed.
```

Back on the cluster, `ffast-cli cache verify so3lr_10GB.xyz` parses the
source again, checks every cached batch, and prints `all verified`.

## Decision

- **The strict check proves the source by parsing it again.** Loading records
  nothing new. `ffast-cli cache verify PATH` takes a source file (its
  finished parsed cache is found by path) or a bundle's `dataset.ffast.json`.
  For the source part it reads the whole file again with the scan's own
  reader and windows, writing nothing: no batch, no scan lock, no progress
  record, no frame list, and a RAM-cache name of its own. It feeds every
  column into the chunk hasher of ADR 0107, in the stored type, and compares
  each chunk digest with the sidecar `<id>.chunks.npz` (a bundle's
  `chunks.npz`). A differing chunk is reported as the frames that hold its
  rows, neighbours merged, at most ten listed. Memory stays bounded: the
  windows stream through the memory budget as in a load.
- **Columns the cache did not keep are covered.** A cache that dropped forces
  or positions under ADR 0106 still has their chunk digests, so the source
  part checks them. The batch part lists them as "not cached" and says so.
- **A source in another state is still compared.** The cache is found by the
  file's path when its size, modification time or inode no longer match,
  and the report says that it was read again and compared anyway. A source
  that cannot be parsed any more is a difference; a file only the Python
  scanner can read is "not checked", with the reason.
- **Batches.** Every batch is read in frame order and every column the cache
  kept goes into a chunk hasher in the stored type; each completed chunk is
  compared with the sidecar. A mismatch names the batch files that hold the
  chunk's rows and the column. A batch that cannot be read is a difference.
  A column the cache left out on purpose is "not cached". The next batch is
  read while the current one is hashed, and the columns of a batch are
  hashed side by side.
- **Exit status and report.** The report says, part by part, what was
  verified, what differed and what could not be checked. Exit status: 0 when
  nothing differed, 1 when something differed, 2 when nothing could be
  checked (no finished cache, unreadable description).
- **What a mismatch changes.**
  - A parsed-cache batch that differs is deleted. When its frames are next
    parsed from the source, the batch is written again at the level its
    siblings were kept at (the disk allowance and the space lock apply). If
    the source itself differs, the whole cache of that identity (manifest,
    sidecar, batches) and the saved frame lists are deleted, as spec §4.3
    asks for a changed source. Other copies of the same path and the pins
    (by path, ADR 0108) stay. A dataset open in another FFAST process (its
    `.open` lock) is not touched; the report says so.
  - A bundle is never changed. The report names the damaged batches; the
    user exports again or copies them again.
- **Fingerprints, loading and the compiled parser do not change.** Nothing
  new is recorded while loading, so a load costs what it did before this ADR
  and no new `ffast-xyz` release is needed. The fast policy stays the
  default and only policy on every load and open path.

## Alternatives

Measured on the ADR 0105 benchmark (M3 Pro, memory budget 3 GiB, baseline
`164641c7`; Validation has the tables).

- **A: the frame finder hashes the source bytes it reads** (a SHA-256 of
  8 MiB pieces of each window, `source|` arrays in the sidecar, a byte-range
  check in `verify`). It makes `verify` fast, about 7 s for the 10 GB file,
  and detects any byte change. Rejected: every first load got slower.
  Hashing inline cost 10 % to 22 % (about 2.4 s per 10 GB).
- **A, hashed on a thread of its own** while the window is parsed. First
  open still read 4 % to 10 % slower at 1 GB and 10 GB (about 0.6 to 0.9 s
  per 10 GB), and the retained window buffers raised the 1 GB peak memory by
  up to 10 %, outside the memory budget.
- **A2: that thread, with at most one window waiting and its buffer counted
  in the budget.** The 1 GB cases were clean, but 10 GB constant first open
  read 13 % slower (13.34 to 15.09 s) because the finder now waited for each
  hash. Building it also showed a fault: the job's lock was held while it
  waited, so two threads waiting for one digest hung.
- **Batches only, no source check.** Cheapest, but it never proves the source
  unchanged, which is half of what §4.3 asks.
- **Fixed byte blocks hashed from the start of the file**, instead of
  parsing. A block crosses two windows, so the finder would carry
  half-hashed state between calls. Not tried: the same hashing cost as A.
- **A strict setting on every open** (`FFAST_VALIDATION=strict`). It adds a
  full source read to every open of a large file and needs progress and
  messages in the browser. The same check function can be called from such a
  setting later.
- **A browser button.** Waits for a sketch, as other cache UI (ADR 0108).
- **Repair at once from the source**, rewriting damaged batches inside the
  check. More code, and FFAST would write into bundles, which ADR 0110 keeps
  read-only. Deleting a cache batch and writing it again when it is next
  parsed gives the repair without that.

## Consequences

What this decision keeps:

- A load costs what it did: no clear change in load time, reopen time or
  peak memory at 1 GB and 10 GB (Validation).
- No change to the compiled parser, its dependencies or its release.
- A user can prove, with one command, that a cache or a bundle holds what
  was parsed and that the source still parses to the same values, including
  the columns the cache did not keep.

What it gives up:

- `verify` of a source takes a full parse: about 12 s for the 10 GB file
  (a warm `cat` of it takes 2.5 s), about twice the 7 s that byte digests
  allowed. The check of a bundle alone takes about 3 s. A check of a source
  holds about 1.4 GB of memory at peak.
- An edit that changes no parsed value (whitespace, a comment that is not
  parsed, a key the reader ignores) is not detected. The check proves the
  values, not the bytes.
- A change to the parser that changes parsed values makes the check report
  the source as differing, though the file did not change. A cache of an
  older parser version has another cache identity (`cache_version`), so
  most such changes find no cache to compare with.
- FFAST keeps no content identity of the source file. A named difference is
  at the granularity of a chunk: 16,384 rows, so up to that many frames for a
  per-frame column and a few hundred for a per-atom column.
- Loads that do not run the one-pass scan (small whole-array loads, strided or
  sampled loads, the Python scanner, non-XYZ readers) have no chunk digests;
  `verify` reports "nothing could be checked" for them.
- Spec §4.3's "source fingerprint computed during ingestion" is not met. The
  strict check pays the read it needs when it is asked for.
- Spec §7.3 scenario 4 ("cache reopen, strict") is timed as `verify`
  (Validation).

## What the build settled

- **Commits.** The source-byte digests were built, measured and removed:
  `4f525632` to `08a654d2` added them (finder hashing, the digest in the
  progress record and sidecar, the background thread, the wait and the
  reservation) and `2c77a3d4` removed them with a new commit, not a history
  rewrite. The compiled parser, its Cargo files, `frame_index.py`,
  `scan_record.py` and the scan in `indexed.py` read as before this ADR,
  except for the healing and verify mode below. `RELEASE` and the crate
  version were never touched.
- **Rebuild command.** The compiled parser is unchanged, but the local copy
  was rebuilt during this work with
  `~/.venvs/ffast_env/bin/python -c "import sys; from ffast.readers.install_fast import install; sys.exit(install(from_source=True, python='<home>/.venvs/ffast_env/bin/python'))"`.
  It installs into the venv's site-packages (`ffast-cli` has no
  `install-fast-reader`). The benchmark harness runs each checkout with the
  compiled file in its own `rust/ffast-xyz/python/ffast_xyz/`, so the file
  from site-packages `ffast_xyz/_ffast_xyz.abi3.so` was copied there. A
  baseline worktree needs the matching old file copied in (a worktree has
  none of its own).
- **Verify mode of the scan.** `IndexedStore(..., verify=True)` reads the
  file as a scan does, but `use_cache` is off, the store has a name of its
  own (so the RAM cache of the real copy is neither used nor filled), the
  frame list and `text` table are not saved, and the chunk digests of the
  hashers are left in `chunk_digests` instead of being thrown away. One
  parse path serves loading and checking.
- **The cache of a changed file is found by path.** `IndexedStore.identity_of`
  is the one formula for a cache's name (the load uses it too). When the name
  for the file's present state has no manifest, `verify` looks for a finished
  manifest of the same real path and compares against the newest.
- **Chunk granularity.** A differing chunk is reported as the frames that hold
  its rows, so a change in one frame names every frame of its chunk, and for a
  per-frame column that is the whole dataset below 16,384 frames. The same
  holds for the batch part: it names every batch with rows in a differing
  chunk, and the repair deletes all of them. No per-batch digests are stored.
- **Only the stored type is compared.** The sidecar holds a second candidate
  digest (float64); `verify` checks the stored one, which any change reaches.
- **Healing.** `_part` writes a missing batch again after parsing it, when the
  window is one of the manifest's ranges. `_save` takes `omit`, the columns
  the first readable sibling batch leaves out, so a healed batch keeps its
  siblings' level. A partial read (under 1 MiB of one column in one batch)
  parses only the frames asked for and does not heal; only a read of the whole
  window does. A deleted batch stays deleted until such a read, and is parsed
  from the source meanwhile.
- **Threading in `verify`.** The next batch is read while the current one is
  hashed, and the columns of a batch are hashed on the fast-parser thread
  count (`hashlib` releases the GIL). Two batches are in memory at a time. The
  first build hashed serially: a bundle alone took 7.2 s for the 10 GB file,
  now 2.8 to 3.0 s.
- **`cache_space.delete_identity`.** Deletes one identity's files only, under
  the space lock, and raises `InUse` when the copy is open. A changed source
  also deletes the frame lists saved for the path (they are keyed by size and
  modification time only).
- **A bundle that fails its fast check** (for example a batch of the wrong
  size) is still verified from its raw description, with a "fast check:
  differs" part, not exit 2.
- **A bundle's source** is found as ADR 0110 finds it (the recorded path, then
  beside the bundle, accepted by the probe of eight 1 MiB slices). A source
  changed inside the probed slices, or small enough for the probe to cover it
  whole, is "not found" there, not a difference.
- **CLI and docs.** `ffast-cli cache verify` is registered beside `export`.
  No user page documented `ffast-cli cache`, so the section "Checking a cache
  or a bundle" went into `docs/usage.md`.
- **Benchmark.** `python -m benchmarks.ingest verify` writes `results.json`
  and a table, as `bundle.py` does, not Parquet rows. "Bytes read" is
  computed, not measured; peak memory is the child process's. Cold `cat` was
  not measured (`purge` needs root); the `cat` column is warm.
- **Flaky tests.** The final fast suite (`-n 8`, 5280 passed, 5 failed) failed
  only in known flakes, all passing alone with `-n 0`: the three launcher
  cases (`test_launcher_stops_its_server.py`, "the server still holds port"),
  `test_web_tour.py::test_first_steps_clicks_through_from_the_empty_screen` and
  `test_web_runtime.py::test_web_loading_a_session_that_does_not_exist_shows_an_error`.
  Their rows in `docs/flaky-tests.md` carry the error texts.

## Validation

Benchmark comparison (ADR 0105 harness), 2026-11-20, M3 Pro, FFAST memory
budget 3 GiB, runs taking turns, medium atoms, constant and variable counts.
Baseline `164641c7`; candidate `2c77a3d4`. The raw runs are
`temp/bench/2026-11-20-adr0111-loadB-1gb` (10 repeats) and
`temp/bench/2026-11-20-adr0111-loadB-10gb` (5 repeats). Median, interquartile
range in brackets; no case or measure reads *worse*.

First open (empty FFAST caches):

| Case | Load (s) old / new | Ratio | Peak memory (MiB) old / new | Ratio |
|---|---|---:|---|---:|
| 1 GB constant | 1.30 / 1.29 | 0.995 | 3195 / 3149 | 0.986 |
| 1 GB variable | 1.28 / 1.28 | 0.996 | 3227 / 3015 | 0.934 |
| 10 GB constant | 12.77 / 12.49 | 0.978 | 1471 / 1461 | 0.993 |
| 10 GB variable | 12.82 / 13.31 | 1.039 | 1505 / 1507 | 1.001 |

Reopen: load 0.45 to 0.46 s (1 GB) and 0.35 to 0.36 s (10 GB) on both sides,
ratios 0.998 to 1.014; peak memory ratios 0.964 to 1.011. Basic Errors (first
open and reopen): ratios 0.996 to 1.100, all no clear change. Cache size is
identical on both sides in every case.

The attempts that were rejected, on the same baseline (first-open load,
median old to new; "worse" is the harness's verdict):

| Build | 1 GB constant | 1 GB variable | 10 GB constant | 10 GB variable |
|---|---|---|---|---|
| A, hashed inline (`7a14b31f`, 5 repeats) | 1.45 to 1.77 s, worse | 1.37 to 1.57 s, worse | 13.26 to 15.67 s, worse | 14.85 to 16.37 s, worse |
| A, own thread (`fd550111`, 5 repeats) | 1.48 to 1.57 s | 1.42 to 1.56 s, worse | 13.40 to 14.02 s | 13.75 to 14.66 s |
| A2 (`08a654d2`; 1 GB 10 repeats, 10 GB 5) | 1.33 to 1.32 s | 1.28 to 1.28 s | 13.34 to 15.09 s, worse | 14.71 to 15.98 s |

The own-thread build also read worse on 1 GB constant peak memory (2798 to
3094 MiB, ratio 1.106).

Time of `ffast-cli cache verify` on the 10 GB file (`python -m benchmarks.ingest
verify`, 3 repeats, `temp/bench/2026-11-20-adr0111-verifyB`), with a warm
`cat file > /dev/null` beside it; every run exited 0:

| Case | cat | verify, source cache | verify, bundle with source | verify, bundle alone | Peak memory, source cache / bundle alone |
|---|---|---|---|---|---|
| 10 GB constant | 2.57 s | 12.02 s | 12.22 s | 2.85 s | 1.40 GB / 0.44 GB |
| 10 GB variable | 2.40 s | 12.03 s | 12.01 s | 2.99 s | 1.47 GB / 0.49 GB |

For comparison, with byte digests (build `fd550111`) the same check read the
source in about 7.3 s for the cache and 7.0 s for the bundle with its source.

Tests: the full fast suite gave 5280 passed and 5 failed, all known flakes that pass alone (see "What the build settled"); the reader, cache, CLI and benchmark tests pass. They cover a clean
cache, a damaged batch (named, deleted, written again, a second check clean),
an unreadable batch, a source changed with size and modification time put back
(the frames named, the whole cache of the identity deleted, the pin kept, other
copies of the path kept), a change in a column the cache did not keep, a file
in another state compared anyway, a check that writes nothing, a bundle with a
damaged batch (left unchanged), a bundle without its source, columns not
cached, a dataset open in another process, an unfinished scan, a file never
loaded, and the CLI exit statuses 0, 1 and 2.
