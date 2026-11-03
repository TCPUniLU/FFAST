# Predicted forces are stored flat, like reference forces

Status: Accepted 2026-11-03. Makes the decision ADR 0058 step 6 left open for
"the prediction load, the zero model and the exports". Follows ADR 0079.
Plan: `docs/implementation/2026-11-03-flat-predicted-forces.md`.

## The problem

MeluXina, 2026-11-03, `shard_03_pred.extxyz` (1,773,605 structures of
different sizes), run 11 with the step timing log (`bd79b7f`). Basic Errors
took 91.9 s to the last panel. Eight force metrics each spent **42–51 s
reading their inputs**, before computing anything. The same read took 1.6 s
when only one metric did it.

Reference forces are one flat array (`F_flat`, all atoms × 3, with offsets
saying where each structure starts; ADR 0058 decision 1). Predicted forces
are not. The in-file prediction load calls `getForces()`, which for structures
of different sizes answers with a Python list of 1.77M per-structure arrays,
and that list is what the cache keeps. Every metric that reads
`prediction.forces` joins the list again into a new 4 GB array
(`_flatPerAtom` → `_flatten`). Joining is Python-level work that holds the
GIL, so eight at once take ~45 s each. Each list entry is a slice of the
loader's own flat array, so the flat array already existed.

For structures of the same size the cache keeps one `(frames, atoms, 3)`
array, which costs no join, but is a second form readers must handle.

## The decision

1. **A prediction's forces are stored as one `FlatFrames` value, for every
   dataset**: `values`, all atoms × 3, frame after frame, and `offsets`,
   `(frames + 1,)`, where each frame starts. The same form as reference
   forces. Same-size data is stored the same way (its offsets are evenly
   spaced).
2. **`FlatFrames` is a small value type** (`ffast/core/flat_frames.py`).
   `ff[i]` is frame `i`'s rows (a view, no copy), `len(ff)` the number of
   frames, `ff.values` the flat array, `ff.frames(idx)` the same for a subset
   of frames, `ff.atoms(flat_index, offsets)` the kept atoms of an atom view.
   Readers that today write `forces[idx]` keep working.
3. **The forces data type converts on the way in**: `newDataEntity(forces=…)`
   accepts a per-frame list, a `(frames, atoms, 3)` array or a `FlatFrames`,
   and stores a `FlatFrames`. Every writer goes through it (model predictions,
   file loads, the zero model, session load, the desktop client), so writers
   do not change. A bare 2D array is refused: it does not say where frames
   start.
4. **The in-file prediction load passes the loader's flat array and offsets
   straight in**, so loading makes no list and no copy. Its fingerprint must
   stay byte-for-byte the same as today.
5. **What leaves the server does not change.** The wire already carries flat
   forces (`server_session` joined the list before sending); it now sends
   `values` as is. Session files keep loading: an old file's per-frame list is
   converted on load. Exported files are byte-for-byte the same.
6. Energies are already one array per prediction and do not change.

## Why

- Ragged per-atom data stored as flat values plus offsets is the usual form:
  Apache Arrow's ListArray, Awkward Array's ListOffsetArray, PyTorch
  Geometric's `ptr`. FFAST already uses it for every reference array (ADR 0058).
- One form removes the `isinstance(x, list)` branches the readers carry for
  predictions (sub-entity, atom filter, scene subset path, resolver, server
  send) and the 4 GB copy each force metric made.
- Converting in `newDataEntity` puts the rule in one place that every writer
  already calls.

## Consequences and risks

- A metric reading `prediction.forces` gets `values` with no copy. Expected:
  the force metrics' input read drops from ~45 s to well under a second on
  shard_03, and Basic Errors to about 40–45 s (to be measured).
- Code outside the readers listed in the plan that iterates a prediction's
  forces as a list must use `FlatFrames` (`iter(ff)` gives frames, so a
  `for frame in forces` loop still works).
- The desktop client is not edited. Its cache entries go through the same
  `newDataEntity`, so they become `FlatFrames` too, and its `forces[idx]`
  readers keep working; its tests decide.
- A session file saved after this change stores `values` and `offsets`; an
  older FFAST cannot read that entry.

## Measured

MeluXina, 2026-11-03, the same file and drive script as run 11
(`docs/handoffs/2026-11-02-scripts/drive.py`), at `ee399b7`. The job rebuilt
and reinstalled the server wheel, so it ran this code. Timing log
`~/.ffast/logs/timing-20261103-110719.log` on the user's Mac; server log on
the cluster.

| | Run 11 (`bd79b7f`) | This run |
|---|---|---|
| Each force metric's `inputs read in` | 42–51 s | 0–2 ms |
| Basic Errors, click to last panel | 91.9 s | 41.8 s |
| Colour atoms by force error, click to last reply | — | 15.4 s |

What the 41.8 s is now spent on (server log):

- The three density steps (energy MAE, force MAE per structure, force RMSE):
  ~18 s of compute each. Two of them waited ~11 s before starting, so they
  finish last, at ~29 s.
- Force difference steps (MAE/RMSE, per dataset and per structure):
  8–11 s each, running side by side.
- The checksum of large results: 4.7 s and 1.6 s inside the
  colour-by-force-error click.

## Considered and rejected

- **Only structures of different sizes go flat; same-size data keeps its
  `(frames, atoms, 3)` array.** Fixes the slow case with less code, but keeps
  two forms and every branch that tells them apart.
- **Keep the list and also keep a flat copy built on first use.** Smallest
  change, but holds a second 4 GB copy and keeps two forms.
- **Store a bare flat array; each reader slices it with the dataset's
  offsets.** No new type, but every reader repeats the slicing, and the cache
  entry no longer says where its own frames start.
