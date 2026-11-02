# A dataset still being read is a preview, without a fingerprint

Status: Proposed 2026-11-01. Not built. Comes after the frame index
(`docs/implementation/2026-11-01-frame-index.md`, parts 1 and 2), and after
measuring what is left of the wait once those are in.

**On hold (2026-11-02).** ADR 0076 removes the wait instead of hiding it:
the load reads the file once, with our own extxyz reader, in parallel
(locally 32× faster than ASE on 400,000 frames). Decide on this ADR after
measuring that load on MeluXina, for `shard_00` and `shard_03`. If a load
still takes more than about a minute, a preview is worth building; if it
takes seconds, close this ADR as Rejected.

Measured 2026-11-02 (ADR 0076, "Measured"): `shard_00` is in the sidebar
29 s after Load, its prediction 28 s. `shard_03` reads in 26.5 s plus a
21.6 s fingerprint, about 50 s, below the one-minute line. The decision is
the user's.

## The problem

Loading a 30.3 GB extxyz file (1,773,605 frames) on MeluXina kept the page
waiting for minutes with nothing to look at. Measured 2026-10-30 to 2026-11-01:

| Step | Before | With the frame index (measured or expected) |
|---|---|---|
| Energy/force names (frame 0) | 54 s | well under 1 s (reads frame 0 only) |
| Frame count | 2 min 7 s | 23.3 s first time (plain scan, measured); near instant after (saved index) |
| Parsing every frame (energies, forces, fingerprint) | several full-file ASE reads | one ASE parse, ~240 MB/s, so ~2 min for 30 GB (expected) |

The frame index removes the counting. Parsing remains: the dataset's
fingerprint is an MD5 hash of every frame's elements, positions, energies and
forces (`aseDatasetLoader.getFingerprint`), so no dataset exists until the
last frame is parsed. The user asked for a load that feels alive: numbers
that grow, and something to look at while the rest arrives.

## The decision

1. **A file being read is a dataset preview** (CONTEXT.md). It appears in the
   page as soon as its first frames are parsed, and its frame count grows
   while reading continues.
2. **A preview has no fingerprint.** It carries a temporary ID that is never
   cached, saved in a session, or used as a cache key. When the last frame is
   read, the fingerprint is computed as today and the preview becomes an
   ordinary dataset. Nothing that relies on fingerprints changes.
3. **A preview is look-only.** The 3D view (step through the frames read so
   far), the reference energy and force plots, and the growing count work.
   The plots refresh every few seconds, not on every frame. Ticking Sub,
   loading a prediction, editing metrics and saving the session are greyed
   out, each saying "available when loading ends".
4. **Stop and Cancel.** Stop ends the read and keeps the frames read so far as
   a normal dataset. Its fingerprint comes from those frames, and its name says
   so, e.g. "shard_03 (first 400,000)". Cancel ends the read and keeps nothing.
5. **A broken frame met while reading** ends the preview with the frame index's
   message ("Frame 812,004 (line 201,376,993) is not a valid frame: …").
   Whether to offer "keep the frames before it" is decided when this is built.

## What this gives up

- A preview is not a full dataset: during loading you can look, not analyse.
  Metrics on the frames read so far were rejected because each refresh would
  redo work that is thrown away when the fingerprint arrives.
- A dataset kept with Stop gets a different fingerprint from the full file,
  so predictions computed for the full file do not match it.

## Alternatives

- **Fingerprint from the file, not its content** (path + size + modification
  time + frame index). Known after the ~23 s scan, so everything would work
  during loading. Rejected: the same data in two files would stop matching,
  and caches and sessions saved under content fingerprints would stop
  matching. Hard to reverse.
- **A temporary ID, renamed to the real fingerprint at the end.** Everything
  works during loading, but every cache entry, view, prediction and tab keyed
  by the temporary ID must be renamed in one step. Rejected as the riskiest
  code for the least gain.
- **Load waits for the whole parse** (today's behaviour, minus the counting).
  Simple, but leaves a ~2 min blank wait on the largest files.
- **A faster parser (C or Rust)** instead of a preview. The libAtoms C parser
  (`ase-extxyz`) and the Rust reader `oxyz` exist. To be measured after parts 1
  and 2: if parsing 30 GB drops to seconds, this ADR may not be needed.
- **Convert files once to a binary store** (LMDB, ASE database, HDF5), as
  machine-learning training pipelines do. Fastest reads afterwards, but one
  slow conversion and a second copy of a 30 GB file. Left for later.

## Open before building

- What exactly the page shows for a preview in the dataset list and the tab
  bar, and what the greyed-out actions look like (sketch first).
- How the preview's arrays reach the page as they grow: whole resend or
  append-only.
- Measure after parts 1 and 2: parse time of a 30 GB file on MeluXina, with
  ASE and with one faster parser.
  Measured 2026-11-01 on `shard_00.extxyz` (5.2 GB, 1,773,606 frames, job
  5410316), with parts 1 and 2 built: Load clickable after 2 s, keys ~1 s,
  count ~6 s. The load itself was cancelled after 60 min. `py-spy` showed it
  in ASE's per-frame parsing, at ~1,470 frames/s, so one pass over the file
  takes ~20 min. The load makes **7 full passes**: the loader's
  constructor (2: the frame loop, then the cell list), the fingerprint (1),
  `z_flat` (1), and `_joined` (3: positions, forces, energies). Each pass
  re-parses the file because `AtomsList` holds one 300,000-frame chunk.
  Measured locally on 20,000 of its frames: one parse 4.6 s, the load's
  passes 46 s in all; parsing in 4 / 10 processes 2.6 / 1.9 s.
  Doing it in one pass comes before deciding on a preview.
