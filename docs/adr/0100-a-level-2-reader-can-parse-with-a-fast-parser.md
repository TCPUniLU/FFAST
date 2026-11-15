Status: Accepted 2026-11-14; built 9d16f7dc..2b905ac1 (see "Measured").
Updated by [ADR 0101](0101-the-cluster-offers-to-install-the-fast-reader.md):
cluster provisioning stages source for an optional build, and the browser
offers installation before a relevant cluster file load in a managed venv.
Changes ADR 0076 (decision 3: our own extxyz reader, in Python) and adds to
ADR 0095 (decision 3: Level 2). Plan:
`docs/implementation/2026-11-14-rust-reader.md`.

# A Level 2 reader can parse with a fast parser

## The problem

The first open of a large `.xyz` file is mostly parsing text. Measured
2026-11-13/14 on a laptop (Apple, 11 cores, file already in memory), two
1.2 GB files: 130,221 frames of an 87-atom molecule ("large molecules"), and
581,558 small crystals with long header lines, the shape of `shard_00`
("small crystals").

| | Large molecules | Small crystals |
|---|---|---|
| Find the frames | 1.0 s | 1.3 s |
| Parse, Level 2 path, 11 worker processes | 5.6 s (214 MB/s) | 8.9 s (136 MB/s) |
| Parse, Python, 1 process | 18.8 s (64 MB/s) | 30.4 s (39 MB/s) |
| Fingerprint | 1.0 s | 0.9 s |

A second open reads the kept arrays (ADR 0076) and parses nothing, so this
is about first opens only.

The Python parser is slow in two places. Turning text into numbers goes
through one Python string per number (NumPy `astype`), and each header line is
read by Python code, key by key. Parsing also holds about 7× the text's size
in memory, so on a laptop memory limits how many worker processes can run
(ADR 0083). Faster NumPy routes were tried: `loadtxt` was 1.3–1.5× faster,
pyarrow 1.2–1.8× overall, because neither touches the header code.

A prototype in Rust (PyO3, rayon), reading into arrays Python made at full
size, on threads with Python's lock released:

| | Large molecules | Small crystals |
|---|---|---|
| Rust, 1 thread | 4.0 s (297 MB/s) | 3.9 s (312 MB/s) |
| Rust, 11 threads | 0.51 s (2.3 GB/s) | 0.58 s (2.1 GB/s) |

Its arrays were identical, bit for bit, to the Python parser's on both files
and on the 10 example files, except where the table under decision 4 says.

## Decision

1. **Level 2 gets an optional piece, `parse_into`.** A reader that has it
   fills arrays FFAST made at full size for a group of frames, on several
   threads, with Python's lock released. `parse(bytes)` stays: it is the
   Python parser and the fallback. FFAST uses `parse_into` when the reader
   has it and the compiled part is installed.

2. **Two names (CONTEXT.md).** The **fast parser** is a reader's compiled
   `parse_into`. The **Python parser** is its `parse`, run in worker
   processes as today.

3. **A separate, optional package: `ffast-xyz`, not on PyPI.** Its source is
   `rust/ffast-xyz/` in this repo, built with maturin. FFAST stays pure
   Python. GitHub Actions (`maturin-action`) builds install files (wheels) for
   Linux (manylinux), macOS and Windows; a tag `ffast-xyz-v<version>` puts them
   and their `SHA256SUMS` on a GitHub Release. `ffast install-fast-reader`
   installs it into the Python that runs FFAST: the release's wheel for this
   computer, checked against `SHA256SUMS` and installed as that file (never by
   name); else (no internet, no release, no fitting wheel) a build from the
   checkout's source, which needs Rust (found on `PATH`, in `$CARGO_HOME/bin`
   or in `~/.cargo/bin`); without Rust it says Rust is required and where to
   get it. `--wheel <file>` installs a wheel copied by hand (a node without
   internet). On MeluXina the build from source works on a compute node with
   the `Rust/1.83.0-GCCcore-13.3.0` module; the cluster bootstrap (ADR 0028) does
   not install the package. The package reports the version of the
   `parse_into` contract it follows; when FFAST expects another, it uses the
   Python parser and says the package needs an update.

4. **Same values and number types as the Python parser.** Where the fast
   parser cannot match the Python parser exactly, it hands the work to Python,
   never rounds or drops. What a comparison of both on 66 edge-case and
   example files found, and the rule for each:

   | Case | Python parser | Rule for the fast parser |
   |---|---|---|
   | A whole-number header value (`energy=-5`, `charge=2`), in 4 of the 10 example files | keeps it as a whole number (`int64`) | the same; the fingerprint hashes the energy's bytes, so a float would change it |
   | A whole number beyond 64 bits (a 128-bit seed) | keeps it exact | gives the text back; Python converts those values only |
   | An underscore in a number (`1_000`) | reads it (Python's `float()` does) | gives the text back, as above; in an atom line, hands the file over |
   | A key Python's header rule refuses (`a-b=1`, `1a=2`, `=5`, `"a"b=2`) | refuses (ASE reads the file) | refuses the same |
   | A key given twice | the last value wins | the same |
   | Escaped quotes; `_JSON` values | reads them; skips `_JSON` | the same |
   | `Z` column, whole-number and optional per-atom columns | reads them | the same |

   A test runs both parsers on the edge-case files and the example files, so
   a new difference fails the suite.

5. **The user is told when the slower parser is used.** When `ffast-xyz` is
   not installed (or too old): one pop-up per server start, "Fast reader not
   installed: .xyz files load with the slower Python parser. Run
   `ffast install-fast-reader` to install it and load them up to 10× faster."
   When the fast parser hands one
   file over: a log warning and the load's status line, "file.xyz: read with
   the slower Python parser (reason)". No pop-up for that.

6. **Python drives groups of frames.** Python calls the fast parser once per
   group: 1/50 of the frames, at most 256 MB. Inside a call, threads split the
   group's frames, each writing into its own part of the arrays. Between
   calls Python moves the progress bar, stops on cancel, and stops when free
   memory falls below the floor (ADR 0083). The fast parser never calls back
   into Python from a thread: that would retake Python's lock and make the
   threads queue on it. At 2 GB/s a group of `shard_00` is about 50 ms; the
   256 MB cap keeps cancel within about 0.2 s on `shard_03`.

   **Plain reads, not a mapped file.** Each thread reads its own frames' bytes
   with `pread` and parses them, so reading overlaps parsing across threads.
   Mapping the file (`memmap2`) was tried first: on MeluXina's Lustre, threads
   faulting in pages of a cold file waited for each small piece in turn, about
   12× slower than plain reads (see "Measured").

7. **Threads:** the cores this process may use, at most 32
   (`FAST_THREADS`), or `FFAST_READ_WORKERS` when set. More were slower on a
   256-core node.

8. **The same crate finds the frames and reads energies.** `find_frames`
   gives `frame_index.scan`'s frame list (Python's loop goes on from any
   frame it leaves, so errors keep their wording); `energies` gives
   `XyzReader.energies` for sampling (ADR 0096), a header only Python can
   judge read by Python.

9. **Not changed:** the older path through `extxyz_arrays.read_frames`
   (`ffast/loaders/ase.py`), used only for in-file predictions on datasets a
   plugin loader made, stays in Python.

## Alternatives considered

- **Rust behind `parse(bytes)`, worker processes kept.** The single-core gain
  (5–8×), but each process still copies its batch back to the main process,
  workers still cost start-up time, and a file under 100,000 frames still
  parses on one core.
- **A special case for xyz inside `ffast/readers/text.py`.** Less code, but it
  breaks the Level 2 contract: no other reader could use it.
- **One package mixing Python and Rust (maturin's default; cryptography,
  tokenizers, orjson).** The usual layout, but FFAST's install file would be
  per platform, and the cluster bootstrap builds that file on the laptop and
  copies it to a Linux node.
- **pyarrow, NumPy `loadtxt`, Numba.** 1.2–1.8× at best (pyarrow), and none
  helps the header code that dominates `shard_00`-shaped files.
- **No fallback, the fast parser required.** What cryptography, pydantic,
  polars, tokenizers and orjson do. Rejected so FFAST installs and works
  without a compiled package, and so a rule the fast parser has not copied
  yet costs speed, not a wrong value.
- **Publishing `ffast-xyz` to PyPI** (`pip install ffast[fast]`). Rejected by
  the user: nothing goes to PyPI. GitHub Releases plus a build from source
  cover the same users.
- **A mapped file (`memmap2`).** Built first; 12× slower on a cold file on
  Lustre (see "Measured"). Replaced by plain reads.

## Measured (2026-11-14)

Same arrays, energies and frame lists as the Python parser in every run below
(compared by hash). First open = finding the frames + parsing.

Laptop (Apple, 11 cores, file in memory), 1.2 GB files:

| First open | Python parser (11 workers) | Fast parser |
|---|---|---|
| Large molecules | 6.6 s | 0.82 s |
| Small crystals | 7.0 s | 0.98 s |

MeluXina (AMD EPYC 7H12, 256 cores, files on Lustre), the
`biomolecules_filtered_train_shards` files, 1.77 M frames each; Python parser
with 256 workers, fast parser with 32 threads:

| | Python parser | Fast parser | |
|---|---|---|---|
| `shard_00` (5.2 GB), first open | 16.3 s | 3.4–3.8 s | 4.6× |
| `shard_00`, energies for sampling | 5.3 s | 0.8–0.9 s | 6× |
| `shard_03` (32 GB), first open | 84 s | 31.7–32.3 s | 2.6× |
| `shard_03`, energies for sampling | 127 s | 15–17 s | 8× |

What limits it there:

- **The disk, for a file larger than the node keeps in memory.** `shard_03`
  is read from Lustre at about 2 GB/s, once to find the frames (14 s) and
  once to parse (17.5 s); parsing hides behind the reading. Two reads cap
  this file at about 2.6× whatever the parser does.
- **Not memory.** The node reads memory at 19–74 GB/s and a file in its page
  cache at 13–26 GB/s; the fast parser parses at about 3 GB/s.
- **A mapped file on a cold Lustre file**: finding the frames of a cold
  `shard_00` took 19.7 s mapped, 1.6 s with plain reads; parsing 20.7 s
  against 5.2 s.
- **Python is stronger on 256 cores** than on a laptop (514 MB/s against
  210 MB/s), so the ratio is smaller than the laptop's.

**Follow-up, not built:** one pass that finds the frames and parses them
(and reads the energies when sampling) would read a large file once: about
20 s for `shard_03` (about 4×), estimated, not measured. It changes the flow
of decision 6, where all frames are found before parsing.

## Consequences

- Two parsers to keep in agreement. The comparison test is what holds them
  together; the Python parser stays the reference.
- A Rust toolchain for whoever changes `rust/ffast-xyz/`, and a release job
  that puts its wheels on GitHub. A user whose computer has a wheel in the
  release never needs Rust; others need Rust to build it, or keep the Python
  parser.
- A file the fast parser hands over loads at today's speed, with a warning.
- Memory: the fast parser holds little beyond the final arrays, so the 7×
  parse peak goes away for files it reads.
