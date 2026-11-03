# A load checks memory before it reads, and stops before memory runs out

Status: Accepted 2026-11-03. Built c8ae99e..424db05.

## The problem

Loading a large extxyz file on a laptop had no limit. It used as much memory
as it needed, and when that was more than the machine had, the laptop
froze while swapping (moving memory to disk), or the operating system killed
the server without a message.

Three things decide the memory a load needs (measured 2026-11-03,
`md22_stachyose`):

| What | Size |
|---|---|
| The joined arrays the dataset keeps | ~57 bytes per atom (positions, forces, elements as 64-bit numbers) |
| One batch while it is parsed | ~6× its bytes in the file (one Python string per number) |
| The join at the end (before) | a second full copy of the arrays |

Each worker process parses one batch at a time, so a laptop with 10 cores
held 10 parsing batches at once.

## The decision

1. **The frame index keeps each frame's atom count.** The scan reads it
   anyway. An index saved before this reads the counts from each frame's
   first line (~1 s per million frames) and saves them.
2. **Check before reading.** The reader parses frame 0 alone to learn the
   columns, and estimates:
   `atoms × bytes per atom + frames × bytes per frame + workers × 7 × batch bytes`.
   When that is more than 90 % of the free memory, it first uses fewer
   workers. When one worker still does not fit, the load is refused with a
   message such as (numbers made up):
   `shard_00.extxyz needs about 14.2 GB of memory to load (…), but 6.1 GB is free. Close other programs, or load a smaller file.`
3. **No second copy.** The joined arrays are made once at their final size,
   and each batch is copied into its place as it arrives. Per-atom numbers a
   frame may lack, and header values, are small and still joined at the end.
   Measured with small batches: peak 82 MB → 67 MB for 40 MB of arrays.
4. **Stop before memory runs out.** After each batch the reader checks the
   free memory again. Below 512 MB it stops and says how far it got. Other
   programs can take memory during a load; this catches that.
5. **Free memory is read the same way everywhere.** `psutil` is now a
   dependency, so macOS and Windows report real free memory (before, without
   it, the code assumed half of the machine's memory, a number that never
   changes). On Linux, the tightest cgroup memory limit above the process
   counts too: a Slurm job, or a container, may have far less than the node
   shows free. Its droppable file cache is not counted as used.

6. **Every n-th frame goes through the fast reader too.** A stride load
   used to read its frames with ASE, one by one, and keep them as `Atoms`
   objects. Now `AtomsList(path, step=n)` holds a strided frame index
   (`FrameIndex.every(n)`), and the reader takes it like any other. Frames a
   few apart are read straight through and picked out; frames far apart get
   a seek each (`READ_THROUGH`). Reading 8 frames at once per worker was
   tried: 2× faster in a bare read test (45 vs 99 µs per frame), but no
   faster in a full load (133 s vs 130 s), so it was not kept. An index saved without atom counts is
   scanned again, which is faster than reading each frame's first line.
7. **Plain xyz is read fast too.** A header without `Properties=` gets
   ASE's columns, `species:S:1:pos:R:3`. A header without any `=` is a
   comment: ASE turns each word into a flag set to True, so it holds no
   number, cell or pbc, and the reader skips it. A plain file with extra
   columns (ASE drops them), or a bare `pbc` or `Lattice` word, still goes
   to ASE. Aspirin PIMD bead `aspirin.pos_09.xyz` (228 MB, 205,153 frames):
   21.7 s → 1.4 s, same fingerprint.
8. **Batches are capped at 16 MB of text**, not only at a number of frames,
   so a batch of large molecules does not take gigabytes while parsed, and
   more workers fit.

Measured 2026-11-03 on a laptop (18 GB, 11 cores), files on a USB disk:
omol25 `biomolecules_filtered_train.extxyz` (60 GB, 7,094,423 frames) and its
`MACE_OFF23_medium_PRED` file (60 GB), every 7th frame (1,013,489 frames,
66 M atoms), through the loading coordinator:

| Step | Before | After |
|---|---|---|
| Index scan, once per file (now with counts) | — | 73 s and 78 s |
| Dataset | ~14 min estimated (826 µs/frame with ASE), plus ~10 GB of `Atoms` | 87 s, 3.5 GB of arrays |
| Prediction | the same again | 95 s |
| Lowest free memory during the load | — | 2.9 GB |

9. **A file's arrays are kept on disk** (`ffast/io/frame_cache.py`, in
   `~/.ffast/frame-arrays`), keyed by path, size, modification time, stride
   and a format number (bump `FORMAT` when the reader changes its arrays).
   The selected energy and force keys are not part of the key: the arrays
   hold every column. They are opened read-only and memory-mapped, on the
   first load too (right after they are saved), so every load works on the
   same arrays and the operating system can drop them under memory
   pressure. The most recently used entries are kept, up to 20 GB and a
   quarter of the free disk. Same files, every 7th frame:
   dataset 90 s → 11.5 s, prediction 122 s → 5.6 s; 7.3 GB on disk.

`NotEnoughMemory` is not a `NotFast`: the loader must not fall back to ASE,
whose reader needs more memory, not less. The loading coordinator shows its
message as it is, without a traceback.

## Not done

- Files read with ASE (plain xyz, other formats, files whose columns change)
  have no check: their memory per frame is not known before reading.
- Keeping arrays on disk (memory-mapped) instead of in memory.
- A prediction file's positions are read and kept until its energies and
  forces are taken (~1.5 GB at the size above); they are not needed.
- 32-bit positions and forces: they would change the fingerprint (ADR 0058),
  so saved sessions and caches would no longer match.

## Consequences

- `available_memory()` now includes the cgroup limit, so the metric memory
  gate (ADR 0079) also stays inside a Slurm job's limit.
- `PARSE_PEAK_PER_BYTE = 7` comes from one file. A file with much longer
  numbers or headers per atom may need a different value.
