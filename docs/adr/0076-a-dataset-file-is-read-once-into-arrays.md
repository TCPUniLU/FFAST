# A dataset file is read once, into arrays, by our own extxyz reader

Status: Accepted 2026-11-02. Not built. Plan:
`docs/implementation/2026-11-02-read-once.md`. Follows the frame index
(`docs/implementation/2026-11-01-frame-index.md`). Puts ADR 0075 (dataset
preview) on hold.

## The problem

Loading `shard_00.extxyz` on MeluXina (5.2 GB, 1,773,606 small molecules with
long header lines, job 5410316, 2026-11-01) was cancelled after 60 minutes.
`py-spy` showed the server inside ASE's per-frame parsing at ~1,470 frames/s:
~20 min for one pass over the file.

A load made **8 passes**, because `AtomsList` holds one 300,000-frame chunk
and every loop over all frames parses the file again:

| Where (`ffast/loaders/ase.py`) | Passes |
|---|---|
| constructor: frame loop (counts, elements, forces/energies present) | 1 |
| constructor: `isUniformAtomsList`, 60 frames ~30,000 apart, each outside the chunk | ~1 |
| constructor: `self.lattice = [atoms.get_cell() …]` | 1 |
| `getFingerprint` | 1 |
| `z_flat` | 1 |
| `_joined`: positions, forces, energies, one list each | 3 |

On top of that, `AtomsList()` parsed its first 300,000 frames when created,
and a prediction file's Dataset Fields were read one full pass per field
(`_extractPredictionFields`). Projected: 8 × 20 min, longer than the 2-hour
job. `shard_03` is 6× larger.

One ASE pass is slow on its own. ASE's reader parses each header character by
character in Python and builds an `Atoms` object and a calculator per frame:
~250 µs per frame locally, ~680 µs on MeluXina.

Measured locally (laptop, 2026-11-02), with a NumPy reader prototype:

| | Time |
|---|---|
| ASE, 20,000 frames of `shard_00` | 4.7 s |
| NumPy reader, same frames, 1 process | 0.83 s |
| ASE, 400,000 frames (estimated from 40,000) | ~99 s |
| NumPy reader, 400,000 frames, 1 / 4 / 10 processes | 18.5 / 4.9 / 3.1 s |

The NumPy reader's counts, elements, positions, forces, cells, pbc, energies
and header fields are **byte-identical** to ASE's on the 20,000-frame sample
and on all 14 local example files (the one it refuses is below).

## The decision

1. **Read once.** The loader's constructor reads every frame once and keeps
   joined arrays: offsets, elements, positions, forces, energies, cells.
   Everything else (uniform or variable, the fingerprint's formula rule, the
   fingerprint, one-frame reads, Dataset Fields) comes from those arrays.
   This applies to every ASE format; only the reader differs.
2. **Same fingerprint.** The fingerprint hashes the same bytes in the same
   order as before (ADR 0058 section 3). An md5 fed frame by frame equals
   one md5 of the joined array, so it is computed from the joined arrays.
   Saved sessions and caches still match.
3. **Our own extxyz reader** (`ffast/io/extxyz_arrays.py`) for `.xyz`/`.extxyz`
   files with a frame index. It reads the header with one regular expression
   and the atom lines as one NumPy table, and copies ASE's rules where they
   change bytes: a value is tried as a whole number before a decimal (so
   `energy=-5` stays `int64`), `I` columns are `int32`, `Lattice` rows are
   cell rows, `pbc` defaults to true when `Lattice` is given, element symbols
   are capitalised.
4. **ASE stays the fallback.** Anything the reader does not handle raises
   `NotFast`, and the loader reads the file with ASE, in one pass. Plain xyz
   (no `Properties=`), `L` or extra `S` columns, an unreadable header, other
   formats (`.traj`, `.db`, …) and strided loads go this way.
5. **Columns that change between frames.** A one-number atom column (`R`
   or `I`, such as `magmoms`) may be missing from some frames. The reader
   keeps it, fills those frames' atoms with NaN, and records where in a
   separate mask (`FrameArrays.missing`). Elements, positions, forces and
   every wider column must be listed by every frame. If one differs, the
   reader stops and the loader warns, then reads with ASE:
   `forces_change.extxyz: Frame 2 lists the columns species:S:1:pos:R:3, but frame 0 lists species:S:1:pos:R:3:forces:R:3. Reading it with ASE, which is slower.`
   Like every warning, it also pops up (ADR 0066). The example file
   `mptrj_sampled_over_200.extxyz` (frame 3 adds `magmoms`) now loads fast,
   without a warning (amended 2026-11-02, after the hand test).
5a. **A missing value is "no value", a NaN in the data stays bad.** The mask
   travels as its own input, never as a NaN bit pattern, so arithmetic,
   casts and joins cannot lose it. Loaders, subset views and stored
   prediction fields answer `getAtomFieldMissing(key)`; the input resolver
   answers `reference.atoms.<key>#missing` (and `prediction.…`), or None
   when no frame lacks the column. A formula variable bound to an atom field,
   directly or through a field metric, gets the mask as the hidden optional
   input `_missing_<name>`, and those rows are "no value" (ADR 0064 section
   8). Every other NaN in a field is still a bad value. Formula results over
   atom fields are recomputed once, because their inputs changed.
6. **Parallel.** A large file (≥ 100,000 frames) is split into batches of
   frames by the frame index. Worker processes each read their own byte
   range and return arrays, and the server joins them in file order. Workers
   start with `spawn`, not `fork`, because the server has threads. Worker
   count: `FFAST_READ_WORKERS` if set, otherwise the cores the process may run
   on (`os.sched_getaffinity`, so a Slurm job's allocation, not the node's).
7. **Header fields come with the read.** Every header value that is a number
   in every frame, and every one-column numeric atom column, is kept from the
   same pass, so Dataset Fields (ADR 0023) need no extra pass. This replaces
   the earlier idea of reading them on request: a prediction's loader is
   discarded after the load, so its fields are read eagerly (ADR 0061), and
   each field cost one full pass.
8. **Progress.** The Tasks list shows
   `Reading shard_00.extxyz: 900,000 of 1,773,606 frames…` as batches finish,
   at most once a second and always at the end. One message per batch
   (887 in 15 s) filled the page's queue on MeluXina.

## What this gives up

- **A second reader to maintain.** ASE is the reference. Tests compare the
  two byte for byte on fixture files and the repo's example files. A file
  that drifts from ASE's behaviour is a bug in our reader.
- **Memory.** All numeric atom columns and numeric header values are held,
  not only the energy and forces in use. Joining batches briefly needs twice
  the arrays' size. Measured peak of the main process: 3.7 GB for `shard_00`
  (26.6 M atoms), 27.9 GB for `shard_03` (248.6 M atoms); each worker under
  1 GB.
- **Field discovery looks at every frame, not frame 0.** A header key
  missing from some frames (`bandgap` in 43 of 202 frames of
  `predictions_mptrj_sampled.xyz`) is no longer listed as a field. Before, it
  was listed and then failed to read, so the result is the same.
- **An explicit energy or force key missing from some frames** now means "no
  energies/forces" instead of a `KeyError` that failed the load.
- **ADR 0058 decision B is replaced.** It read one frame from the file and
  built the joined arrays on the first all-frames request, to avoid holding
  every frame. Now the arrays are built during the load and one-frame reads
  slice them. Memory is held from the load on, not from the first plot. In
  practice every load reached that request anyway: the fingerprint, the
  energy plots and in-file predictions all read every frame.
- **`getLattice(i)`** returns a NumPy `(3, 3)` array instead of an ASE `Cell`.
  Its three callers only read numbers from it.
- Loads still cannot be cancelled (known before this ADR).

## Alternatives

- **Read once, keep ASE.** One pass instead of 8: ~20 min for `shard_00`,
  ~2 h for `shard_03`. Still too slow.
- **A C or Rust reader** (`ase-extxyz`, `oxyz`). Faster than our NumPy reader
  on one core, but a compiled dependency to install on every cluster, and its
  byte-for-byte agreement with ASE (int energies, `int32` columns) is
  unknown. The NumPy reader plus workers already lands in seconds. Revisit if
  single-core speed matters.
- **Dataset preview (ADR 0075).** Hides the wait instead of removing it. On
  hold until this is measured on MeluXina.
- **Convert once to a binary store** (HDF5, LMDB, ASE database). Fastest
  re-reads, but one slow conversion and a second copy of every file. Left for
  later.
- **Threads instead of processes.** Header parsing is Python code, so threads
  would wait on each other (the global interpreter lock).

## Measured (MeluXina, 2026-11-02)

Profile "MeluXina CPU1". It asks for 16 CPUs, but Slurm gave the whole node:
`nproc` = 256, so 256 workers. Page runs: job 5411886, driven by
`docs/handoffs/2026-10-31-cluster-slowness/scripts/cluster_drive.py` (load
limit raised to 120 min). Read-only runs: job 5411933, a script that times the
loader constructor and the fingerprint.

| File | Before (ASE) | Now |
|---|---|---|
| `shard_00.extxyz` (5.2 GB, 1,773,606 frames, 26.6 M atoms), full load in the page | not finished after 60 min; ~20 min per pass, 7 passes | **29 s** to the sidebar (read 16 s) |
| `shard_00_pred.extxyz` (5.5 GB), full load in the page | not measured | **28 s** to the sidebar (read 18 s) |
| `shard_03.extxyz` (32.5 GB, 1,773,605 frames, 248.6 M atoms), read + fingerprint | not measured | **26.5 s + 21.6 s**, peak 27.9 GB |

`shard_00`, read only, by worker count:

| Workers | Read | Frames/s |
|---|---|---|
| ASE, 1 core (handoff 5) | ~20 min per pass | ~1,470 |
| 1 | 213 s | ~8,300 |
| 16 | 15.8 s | ~112,000 |
| 256 | 6.0 s | ~296,000 |

- One core reads 5.7× faster than ASE; the fingerprint is the same in every
  run (`d5568bd3…` for `shard_00`).
- The fingerprint of `shard_00` takes 2.3 s; of `shard_03`, 21.6 s. It runs on
  one core (md5) and is now as long as the read for large files.
- In the page, `shard_00`'s read took 16 s against 6.0 s in the script, with
  the same 256 workers. Not investigated; worker start-up inside the server
  is one candidate.
- The frame index was already saved for both files (index 0.1 s).
