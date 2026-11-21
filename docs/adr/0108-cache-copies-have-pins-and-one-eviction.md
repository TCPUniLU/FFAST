# Parsed cache copies have pins, open-copy locks and one eviction

Status: Accepted and implemented 2026-11-19; protection of older file versions
changed 2026-11-21 (see the last section). Builds on ADRs 0083, 0103, 0106 and 0107.
Replaces the SQLite catalog planned in spec v1.1 §11a item 2.

FFAST keeps parsed copies of `.xyz` files on disk so the second open is
fast: whole-array entries in `~/.ffast/frame-arrays/<key>/` (ADR 0083) and
memory-saving batches in `~/.ffast/frame-arrays/batches/<identity>.*`
(ADRs 0103, 0107). Both share one disk budget (`FFAST_PARSED_CACHE_BYTES`,
20 GiB, at most a quarter of free disk). Three problems:

- **Another process can delete a copy that is in use.** The open-copy guard
  is a `WeakSet` inside one server. Two FFAST servers sharing `~/.ffast`
  (two browser windows on a cluster, a server plus `ffast-cli`) cannot see
  each other's open copies. Whole-array entries have no guard at all.
- **Nothing can be kept on purpose.** A user with one large reference file
  cannot stop it from being evicted by a few smaller files opened later.
  Spec §4.2 asks for pinning and a confirmed manual delete.
- **Eviction is written three times** (`frame_cache._make_room`, and twice in
  `IndexedStore`: `_save_manifest` and `_save`), with different orders. The
  batch paths sort by *write* time, not by last use, so a file opened every
  day but parsed a month ago goes first.

Example of the wanted behaviour. `water.xyz` (8 GB) is pinned and open in one
window; `aspirin.xyz` (6 GB) was last opened yesterday. Opening `benzene.xyz`
(9 GB) needs room: `water.xyz` is skipped (pinned, and open), `aspirin.xyz`
goes. `ffast-cli cache list` shows what is kept; `ffast-cli cache delete
water.xyz` asks before deleting a pinned copy.

## Decision

- **A dataset is a source file.** Every cache entry records the absolute
  path (symlinks resolved) of the file it was parsed from: whole-array
  entries already do in `meta.json`; each batch identity gets a small
  `<identity>.source.json` (`{"path", "state"}`) written when its store first
  writes to the folder. Entries with the same path form one dataset, across
  both stores and across file versions. An entry whose path cannot be read
  (written before this ADR) is its own dataset, never pinned.
- **Last use is a file time.** Opening a whole-array entry already touches
  its `meta.json`; opening a batch store touches its `.source.json`. A
  dataset's last use is the newest of its entries.
- **Open copies hold a shared lock.** Whoever opens a copy takes a shared
  `flock` on `frame-arrays/<key>/.open` or `batches/<identity>.open` and
  holds it as long as the copy is in use: the lock's file handle is closed
  when the `FrameArrays` or `IndexedStore` object is garbage-collected, or
  when the process ends. The OS drops the lock of a process that dies, so no
  stale "open" state can build up. Eviction tests each `.open` file with an
  exclusive non-blocking lock: if it cannot get it, the copy is open in some
  process, this one included. This replaces the `_live_stores` checks.
- **Pins are paths in one file.** `frame-arrays/pins.json` lists pinned
  absolute paths. A pin covers every entry of that path, now and after the
  file changes. A path can be pinned before it has any copy. Only the CLI
  writes the file, with write-then-rename.
- **One eviction function.** A new module `ffast/io/cache_space.py` owns:
  finding entries and grouping them by path, the bytes used, pins, the open
  locks, and `make_room(need, saving)`. All three call sites use it. It runs
  under one exclusive lock file, `frame-arrays/.space.lock`, which also
  covers publishing a new batch or entry (it replaces `batches/.lock`).
- **Eviction order.** A dataset is protected when it is pinned, when any of
  its entries is open, or when it is the dataset being saved. Unprotected
  datasets go oldest last-use first. Inside one dataset the big parts go
  first (per-atom batch files, then whole-array entries, then per-frame
  batch files); its small records (`.json` manifests, `--1--1.npz` scalar
  batches, `.chunks.npz` digests, scan record files) go last, so a later
  reopen skips the full rescan if only big parts were needed. When a
  dataset's last file goes, its `.source.json` and `.open` go with it. The
  evictor deletes a `.open` file only while holding its exclusive lock, and
  an opener that gets a shared lock checks the file is still the one at that
  path (same inode) and that the entry still exists, or treats it as a
  cache miss.
- **The dataset being saved is never trimmed by its own save.** Today a
  saving store may delete its own older batches. Since ADR 0107 those are
  the committed windows a resumed scan needs. If the saving dataset alone
  does not fit, the existing level drop (ING-05) and "skip this save" paths
  apply. One exception: writing the manifest at the end of a finished scan
  may delete the store's own per-atom batches to make room for it. Resume no
  longer needs them then, and a kept manifest spares the next open a full
  rescan.
- **Protected data never gets deleted to make room.** When only protected
  datasets are left and the new data still does not fit, the save is
  skipped and the user gets one pop-up per server run: the cache is full of
  pinned or open copies, with the sizes, and the two ways out
  (`ffast-cli cache unpin`, `FFAST_PARSED_CACHE_BYTES`).
- **Commands.** `ffast-cli cache list` prints each dataset: path, size,
  pinned, open, last use, and the total against the budget; pins without a
  copy and copies whose source file is gone are shown as such.
  `ffast-cli cache pin PATH` and `unpin PATH` edit the pin file.
  `ffast-cli cache delete PATH` removes every copy of that path: it refuses
  while a copy is open, asks yes/no when the path is pinned (`--yes` skips
  the question), and removes the pin. A browser panel comes later, after a
  sketch.
- **Scope.** The parsed cache only (`frame-arrays/` including `batches/`).
  Result arrays (`FFAST_RESULT_CACHE_DIR`), the metric cache and the frame
  index keep their own limits and eviction. The budget numbers do not
  change.

## Alternatives

- **An SQLite catalog** (spec v1.1 §11a). One row per dataset with pin,
  last use and an open lease. Dropped: the files would stay the truth, so a
  database is a second copy that can disagree after a crash and needs
  repair code; SQLite's locking is unreliable on network home folders,
  where `~/.ffast` lives on some clusters; and the cache holds tens of
  datasets, which a folder listing sorts in milliseconds. Revisit if access
  history over time is ever needed.
- **Lease records with a process id**, in a file or database. Rejected: they
  outlive a crashed process and need a liveness check that a reused process
  id can fool. A `flock` is released by the OS.
- **Whole-dataset eviction.** Simpler, but a reopen then rescans from zero
  even when the small records alone would have saved the scan.
- **Per-file eviction by last use.** Fixes the clock but leaves datasets
  half-cached in arbitrary ways and cannot express pins per dataset.
- **Pin one cache entry.** Precise, but editing a pinned file would silently
  drop the protection; users think in files.

## Consequences

- Two servers sharing `~/.ffast` no longer delete each other's open copies;
  whole-array entries are protected for the first time.
- A leaked reference to a `FrameArrays` or store keeps its dataset protected
  until the process ends. That errs on the safe side.
- A copy deleted while a memory map of it is still open stays readable on
  macOS and Linux (the data lives until the map closes), so a lock released
  slightly early cannot crash a reader.
- Pins follow paths: a moved or renamed file loses its pin. Portable caches
  (spec §4.2, next step) may key pins by content later.
- Each entry gains two tiny files (`.open`, and `.source.json` for batches).
- Platforms without `fcntl` get no cross-process protection; FFAST targets
  macOS and Linux.
- `flock` on a network filesystem may be unreliable; the parsed cache is
  meant for local disk (spec §11a), and the scan lock of ADR 0107 already
  depends on it.
- The spec's SQLite catalog is dropped from §11a item 2; Parquet + DuckDB
  (ADR 0105) and the NumPy vs Zarr comparison stay.

## Measured and built

Measured 2026-11-19 with the ADR 0105 harness, five turns per case, 100 MB
and 1 GB files, small and medium atom counts, constant and variable: the
commit before the build against the built one. Every load, Basic Errors and
reopen time, peak memory and cache size read "no clear change" (1 GB load 1.25
to 1.34 s on both sides; reopen load 0.44 to 0.48 s on both; ratios 0.95 to
1.10, inside the spread). Deviations from the plan: the read-once arrays of
`readers/text.py` were a third eviction site and now use the same module; unlocked
files in `batches/` with names a store did not make are counted and evicted as
small records; the manifest write at the end of a finished scan may trim its own
per-atom batches (the exception above); the lock of a read-once array set lasts
until its last array is freed.

## Change 2026-11-21: protection covers the current version only

The rule "a pin covers every entry of that path, now and after the file
changes" also applied to an open copy and to the dataset being saved: they
protected every copy of the path. A file that grows (live LAMMPS output)
gets a new copy for each version that is loaded, so a pinned or open
growing file left one copy per version that nothing could evict. Found by
`tests/ffast/readers/test_cache_combinations.py`.

Now a pin, an open copy or a save protects only the copies made from the
file as it is now (batches compare device, inode, size and modification
time from `.source.json`; whole-array entries compare size and modification
time, now written to `meta.json`). A copy of an older version is protected
only while it is open itself, and is otherwise evicted on its own, oldest
first. A copy with no recorded state (made before this change), or a
source file that is gone, counts as current, so it keeps its protection.
The pin file and `cache delete` are unchanged.
