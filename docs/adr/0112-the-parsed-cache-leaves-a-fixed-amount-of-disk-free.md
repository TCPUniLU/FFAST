# The parsed cache leaves a fixed amount of disk free

Status: Accepted and implemented 2026-11-21. Changes the disk rule of ADRs
0106 and 0108; the priority levels (ING-05) stay.

The parsed cache keeps whole priority levels while the dataset fits its
allowance: (1) per-frame values and species, (2) forces, (3) positions and
other per-atom columns. The allowance was the smaller of 20 GiB and a
quarter of the disk's free space.

On a laptop with 45 GiB free (90% full), an 83 GB extxyz file (7,094,423
structures, 463M atoms, reference and predicted forces) got 11 GiB. Its
forces need 22.2 GB (two columns of 463M x 3 float64), so only level 1 was
kept. Every force plot then parsed the forces from the text again: the
Basic Errors force pass spent 61% of its 62-70 s parsing the reference
forces (profile 2026-11-21), and it did so again after every restart or
when the pass was evicted.

A quarter of free disk is a share that shrinks fastest exactly where large
files live: a nearly full disk holding a big dataset. 20 GiB was also below
the forces of this one file.

## Decision

The parsed cache may grow until `keep_free` bytes of its disk would be left
free: 20 GiB by default (`FFAST_CACHE_KEEP_FREE_BYTES`). The size cap
(`FFAST_PARSED_CACHE_BYTES`, `resources.parsed_cache_bytes`) rises from
20 GiB to 100 GiB.

`cache_space.allowance(root, free)` is the one place the rule lives: the
smaller of the cap and what the cache holds now plus the free space beyond
`keep_free`. What the cache holds counts as room because other datasets give
way, oldest last use first (ADR 0108); pinned and open ones do not, and a
batch that then does not fit is not written and is read from the source.
The scan reads the allowance once (ADR 0107), as before; the manifest and
the whole-array entries (`frame_cache.max_bytes`) use the same rule. Their
own cap was 20 GiB at first; it is 100 GiB since 2026-11-21, the same as the
parsed cache. On a MeluXina node (503 GB RAM) a 48 GB file is read whole, its
arrays were 22.4 GB, and every reopen parsed the text again (42 s).

On the laptop above the allowance is about 25 GiB: the forces are kept.

## Alternatives

- **Half of free disk.** 22.5 GiB on that laptop: forces plus level 1 (23 GB)
  still did not fit, so it gave nothing there, and like a quarter it
  shrinks as the disk fills.
- **An opt-in setting only.** Nothing changes unless a user finds it; the
  user who needed it did not know the cache had left the forces out.
- **Forces stored as float32 on disk.** Halves the size, but changes values
  beyond about 7 digits; the plots would no longer show the file's numbers.
- **Compression.** Forces compress poorly (dense float mantissas) and every
  read would pay for decompression.

## What it gives up

- The cache can now take most of a disk's free space, down to 20 GiB. A user
  who needs more free space must lower the cap or raise `keep_free`.
- A cache written under the old rule keeps its levels: a scan's kept level
  may fall, never rise (ADR 0107). To gain the forces, delete that file's
  cache (`ffast-cli cache delete <file>`) and load it again.
- A cold load writes the forces too: more disk writing during the first load
  (measured below).

## Measurements

The same 83 GB file, laptop, 14.4 GiB RAM budget, each run with a fresh
cache folder:

| Run | Load | Basic Errors force pass | Cache |
|---|---|---|---|
| Old rule (forces not kept), first load | 112.7 s | 69.3 s (CPU 461 s) | 1.2 GB |
| New rule, first load | 139.0 s | 22.1 s (CPU 73 s) | 21.9 GB |
| New rule, second start | 2.7 s | 22.2 s (CPU 73 s) | 21.9 GB |

The first load cost 26 s more, once, for writing the forces: each batch
was written and flushed in line, so the next window's parse waited for it.
A load now writes each batch on a writer thread while it parses the next
window (both load paths; at most one write waits, so at most two windows
are held). The scan record of a window is taken when the window is done and
written by the writer thread after its batch, so it still names exactly the
written batches (ADR 0107).

| Run | Load | Basic Errors force pass |
|---|---|---|
| New rule + writer thread, first load | 118.6 s | 23.0 s (CPU 76 s) |
| New rule + writer thread, second start | 2.7 s | 22.9 s |

The first load is now 6 s longer than with the old rule (112.7 s), not 26 s. Every force
pass after it, in that session and in later ones, takes 22 s instead of
69 s, and 6x less CPU: it reads the cache (about 1 GB/s) instead of parsing
the text. The disk had 23.7 GiB free afterwards, above the 20 GiB kept free.
