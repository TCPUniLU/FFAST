Status: Proposed. Decided 2026-10-16, after architecture review problem 1
(`docs/research/architecture-review-2026-10-14/01-dataset-kinds.md`).

# One storage form for datasets, one view for everything cut from them

## The problem

FFAST has two kinds of file dataset and three kinds of dataset cut from another
one. Each answers "what is in frame *i*?" its own way, so code written against
one kind fails on another. The errors seen while fixing the first three:

- Extract on a dataset whose structures all have the same atoms:
  `aseDatasetLoader.getElements() takes 1 positional argument but 2 were given`.
  No subset appeared; the server only logged a warning (d102224).
- A subset of a periodic dataset never showed its unit cell: `getLattice(i)`
  raised `TypeError` inside the scene builder, which logged it at debug level.
  The frame subset also answered with all of its parent's frames (943855e).
- A subset of a subset: `'SubDataset' object has no attribute 'z'` (e8a627d).

Three more are recorded as expected failures in
`tests/ffast/test_subset_known_gaps.py`: an atom subset of a variable dataset
finds no bonds (`'<' not supported between 'float' and 'NoneType'`), a frame
subset of a variable dataset cannot pack its arrays for the client
(`inhomogeneous shape`), and freezing an atom subset reads `modelDep`, which it
does not have. Four earlier subset fixes landed in two days, each adding one
more special case.

Two causes sit under all six.

**The same call means different things.** On a uniform dataset `getElements()`
returns one structure's elements; on a variable one it returns every atom of
every structure, joined. `getCoordinates()` returns one 3D array on a uniform
dataset and a list of arrays on a variable one. So callers must know the kind to
read the answer. Measured on 2026-10-16: 10 places outside `ffast/loaders` ask
`isVariable` for this reason, and `SubDataset` asks it 10 more times itself.

**"Ask the parent, then restrict" is written once per method per class.**
`SubDataset` and `AtomFilteredDataset` each re-derive it, with different method
signatures, and the same attribute name `indices` holds frame numbers in one
and atom numbers in the other.

## The decision

### 1. One storage form

Every file dataset describes its structures the same way: offsets saying where
each structure's atoms start in one flat run of atoms. A uniform file is the
case where every structure has the same size: offsets `0, n, 2n, ...`. One
loader class replaces `aseDatasetLoader` and `VariableASEDatasetLoader`, and
`isVariable` goes away.

Only the offsets are held in memory. Coordinates, forces, energies and elements
are read from the file per frame, through `AtomsList`
(`ffast/core/data_types.py`), which keeps one chunk of a large file in memory
at a time. The uniform loader works this way today, so files of several GB keep
working; the variable loader, which today reads the whole file into memory,
moves to it. Flat arrays covering all frames are built only when a caller asks
for all frames, and then kept: rebuilding them costs one file read per frame.

Measured on 2026-10-16 (`docs/research/bench_frames.py`, a variable extxyz file of 5000
frames of 10 to 60 atoms, 18.5 MB, with energies and forces; two runs agreed
within 10%):

| | held in memory | one frame, in order | one frame, random |
|---|---|---|---|
| variable loader today | 44 MB | 1 µs | 1 µs |
| read per frame, file in one chunk | 33 MB | 60 µs | 59 µs |
| read per frame, 500-frame chunks | – | 245 µs | 87,000 µs |

A frame read is 60 times slower but stays far below one screen refresh
(16,700 µs at 60 Hz). Memory falls only 25%, because the variable loader keeps
the file twice today: as `AtomsList`'s ASE objects and as flat arrays.
`AtomsList` holds 300,000 frames per chunk (its docstring says atoms; the code
counts frames), so a smaller file is fully in memory either way. Only a larger
file reads one chunk at a time, and then a jump to a distant frame re-reads the
file from the start: 87 ms here, more on a larger file. Uniform files of that
size behave this way today.

- Per-frame calls keep their names and take a frame number on every kind:
  `getElements(i)`, `getCoordinates(i)`, `getLattice(i)`.
- "All frames" gets new, explicitly named methods returning the flat arrays and
  the offsets. A helper reshapes them into `(frames, atoms, 3)` when every
  structure has the same size, for the ML plugins (sGDML, SchNet, SpookyNet)
  that want one 3D array. The old no-argument calls, about ten outside the
  loaders, move over one at a time.
- Bond sizes are computed per structure from its elements, as variable datasets
  already do. Uniform datasets lose their pre-computed `bondSizes` matrix.
  Measured cost: about 5% of the bond step at 200 and at 2000 atoms, where the
  distance matrix, computed per frame by both kinds today, dominates. With it
  goes the copying of `bondSizes` into subsets, and the bonds bug above.
- Missing energies or forces are absent, never filled with zeros. This is what
  the uniform loader does today; the variable loader fills in zeros and so
  claims forces it does not have. Asking for an absent result raises
  `RuntimeError`, as the uniform loader's ASE frames do, and callers already
  catch it. If any frame lacks a result, the file has none: a partly filled
  file is not guessed at.

### 2. One view for everything cut from a dataset

`SubDataset`, `FrozenSubDataset` and `AtomFilteredDataset` become one class: a
parent, a frame map and an atom mask, plus whether its frames follow a plot's
zoom. Frame *i* of a view is frame `frames[i]` of its parent; its atoms are the
parent's atoms at the mask. That translation is written once.

The glossary's subset, frozen subset and atom subset stay what users see; they
are states of one view. A subset of a subset is a view whose parent is a view.
`isSubDataset`, `isAtomFiltered` and `frozen` stay as read-only attributes
derived from the view's state while callers still read them.

### 3. Fingerprints do not change

A file dataset's fingerprint is the key of its saved session entry and of its
saved prediction caches, so it stays byte-identical. Today the uniform formula
hashes one structure's elements and the variable one hashes all of them; the
single loader keeps the uniform formula when every structure has the same
chemical formula, and the variable formula otherwise. Two checks apply that rule
today: opening a dataset compares randomly chosen frames
(`check_homogeneity` in `ffast/plugins/loaders/ase.py`), loading predictions a
fixed sample (`_isUniformAtomsList` in `loading_coordinator.py`). The single
loader uses the fixed one (now `isUniformAtomsList` in `ffast/loaders/ase.py`,
the only check). A large file that the random check classifies
differently from run to run has no stable fingerprint today, so there is none to
keep. A file whose frames share a formula but
list their atoms in a different order keeps its fingerprint; its elements,
wrong today because frame 0's are used for every frame, become right.

A view keeps the three formulas in use today, chosen by its state: a live subset
hashes parent, model and name (so the same subset is refreshed as the zoom
changes); a frozen subset adds its frames; an atom subset hashes parent and
atoms. Saved sessions do not store subsets (`getAllDatasets(excludeSubs=True)`),
but open windows hold these fingerprints, and the Qt client computes one
directly (`UI/Plots.py:736`).

**Not reversible:** a variable file that has no forces or no energies gets a new
fingerprint, because zeros are no longer hashed in. Its saved sessions and caches stop
matching. ADR 0008 already declines to migrate sessions.

### 4. sGDML datasets join

The sGDML loader (`ffast/plugins/models/sgdml.py`) fills the same flat storage.
Keeping a second storage form alive for one plugin would bring back the split.

### 5. The Qt client is knowingly broken, and tracked

The Qt client builds subsets itself, on the client, over its proxy
`CachedRemoteDataset` (`cluster/remote_dataset.py`): ticking SUB
(`UI/Plots.py:764`), the atom filter (`modules/loupe/loupeAtomFilter.py:145`) and
Freeze (`UI/SideBar.py:220`). The proxy keeps both storage forms itself (18 kind
checks). It is out of scope here. From the step that introduces the view, Qt
subsets of a remote dataset are expected to fail until GitHub issue #22
(https://github.com/TCPUniLU/FFAST/issues/22) is done. The
browser client is unaffected; Qt starts by default whenever PySide6 is
installed (3d8a7a4), so this breaks real use, not just a test.

## Considered options

- **Keep two storage forms, agree on signatures only.** What d102224 and 943855e
  did. Smallest, but `getElements()` keeps two meanings and the caller branches
  and the bonds and transfer bugs stay.
- **Keep two storage forms with the same meaning for every call.** Removes the
  caller branches but writes every rule twice, once per class, and relies on the
  copies staying identical. The two ASE loaders already disagree about missing
  forces.
- **Pad every structure to the largest size, with a mask.** Numpy-friendly, but
  every metric must respect the mask, and forgetting it gives silently wrong
  numbers. Rejected.
- **Separate the numbers from the identity:** a plain table of arrays with a
  `select(frames, atoms)`, and dataset objects holding only name, fingerprint,
  colour and events. The cleanest form, but it touches events, the registry and
  the caches, and a live subset would rebuild its table at every zoom. Too large
  as a first step; this decision does not rule it out later.
- **One access module in front of today's classes.** Cheap, and callers would
  not change again, but it hides the two meanings rather than removing them.

## What it gives up

- Qt subsets of a remote dataset, until GitHub issue #22 is done.
- The pre-computed bond-size matrix (about 5%, measured).
- Fingerprints of variable files without forces or energies (see section 3).
- Instant frame reads on variable files: 1 µs becomes 60 µs, and on files over
  300,000 frames a jump to a distant frame re-reads the file (measured, section 1).
- The 11 checks that ask "is this cut from another dataset?" (registry, data
  service, data types, freeze, server session) stay. That is a different
  question from the one this decision removes.

## Steps

One commit each, so each can be reverted alone.

1. A table-driven test: every kind of dataset and view, over a uniform and a
   variable parent, against every per-frame question, plus a test that every
   file dataset's fingerprint is unchanged. The expected failures in
   `test_subset_known_gaps.py` turn green as the steps land.
2. The uniform loader gets the variable loader's offsets (`molecule_offsets`)
   and answers `getNAtoms(i)` for one frame; fingerprints unchanged.
3. The two ASE loaders become one, reading per frame through `AtomsList`.
   Missing data is absent, not zeros. Measure per-frame reads on a variable
   file before and after, since they move from memory to the file.
   As built (aa91652..b1f61a1), in this order: missing results absent; the
   variable loader fingerprints, then reads, frame by frame, joining all-frames
   arrays only on request (44 MB held became 34 MB, 1 µs per frame became
   60 µs); `VariableDatasetLoader` inherits `DatasetLoader`; one fixed rule
   (`isUniformAtomsList`) for datasets and predictions, the random check
   deleted; that loader learns uniform data, checked against the old uniform
   loader question by question; both choosers use it; the old class is
   deleted and the survivor is `aseDatasetLoader`. Each kind keeps its old
   all-frames shape until its last callers move (`tests/ffast/test_ase_loader_shapes.py`).
4. The "all frames" methods, built once and kept, and the ten callers moved to
   them one at a time. First count how often each caller asks for all frames:
   without the kept copy, every call reads the whole file (0.3 s for 5000
   frames, about a minute for a million).
   As built (1d8e504..b9cb4cd): `getOffsets`, `getAllElements`,
   `getAllCoordinates`, `getAllForces`, `getAllEnergies` on every dataset, and
   `stackFrames` (raises `ValueError` when sizes differ). They return read-only
   arrays, because they are shared. A file holds them after the first request
   (5000 variable frames: 0.6 s once, under 1 µs after; rebuilding one array
   from frames already in memory costs 6 ms). Every other kind translates its
   older answer, and goes when step 5 replaces the views. The count found 19
   call sites in 11 files outside the loaders and the Qt client, not ten. Moved: the scene
   builder (one frame's elements), the metric resolver (energies, atom counts),
   per-atom scatter subsets, displacement colouring (which now says why it
   falls back on a variable file), the force arrows from a difference, and the
   four models, through `uniformFrames` (`ffast/loaders/model.py`). Not moved,
   because the answer's shape is part of a contract downstream:
   - the resolver's `reference.forces`, `.positions`, `.elements` and `.masses`:
     metrics such as `force_mae_per_element` take 3D forces and one structure's
     elements on uniform data, and `offsets` is `None` there to say so. Flat
     arrays would change what the metrics receive;
   - the prediction load (`loading_coordinator.py`), the zero model and the
     exports, which store or write each kind's own shape;
   - the Qt client (GitHub issue #22).
   Each is a decision about a contract, not a rename. Step 6 cannot delete the
   `isVariable` branches until they are made.
5. One view replaces the three derived classes. Qt subsets break here.
   As built: `DatasetView` (`ffast/loaders/dataset.py`) holds a parent, a
   frame map (`frames`) and an atom mask (`atoms`), plus `live` for a subset
   that follows a zoom. `DatasetView.subset`, `.frozenSubset` and
   `.atomSubset` make the three states. Their fingerprint formulas are static
   methods, so callers ask for a fingerprint without building a view, and the
   golden fingerprints are unchanged. `indices`, which held frames in one class
   and atoms in the other, is gone: the data layer reads `frames` or `atoms`.
   Decided 2026-10-20: an atom subset that names an atom some structure lacks
   is refused at creation, since atoms are picked on frame 0. The check reads
   only the offsets. Each frame of an atom subset now has its own elements, and
   a view follows its parent's kind, so an atom subset of a variable file
   exports frame by frame (it wrote frame 0's elements into every frame
   before). Bonds of an atom subset are the parent frame's bonds between its
   atoms. All 17 expected failures from step 1 pass;
   `test_subset_known_gaps.py` is gone, its cases now asserted in
   `test_dataset_frame_signatures.py`. The table's atom mask is `[0, 1]`,
   which every test structure has. Qt: a subset and an atom subset over the
   Qt client's proxy (`CachedRemoteDataset`), uniform and variable, answer
   every per-frame and all-frames question in a script. Issue #22 may need
   less than expected.
   Desktop GUI tried 2026-10-20 (`examples/data/dataset.xyz`, 100 structures
   of 4 to 50 atoms, with `prediction.xyz`, local server). The view held up;
   what broke was around it, mostly older than this step, and each is fixed
   in its own commit:
   - A desktop subset drew a black 3D view: the desktop made SUB, Freeze and
     Extract only in its own window, and the 3D view asks the server to draw
     by fingerprint. They now make the same subset on the server too
     (d0791b6); the window ignores the server's announcement of a dataset it
     already holds, so nothing appears twice.
   - Extract's refusal showed only a console traceback; it is now a warning
     box with a plain sentence (3c5aca2).
   - An atom subset of a variable file was labelled "Variable (2-2 atoms)";
     it shows its formula when every frame has the same elements (ffe6c75).
   - Predicted forces of an atom subset of a variable file failed
     (`AtomFilteredEntity` read `.shape` off the per-frame list), so its 2D
     plots stayed empty (a150683).
   - A plot draws every active dataset, so SUB made a subset of its own SUB,
     which a zoom then gave frames it no longer had. SUB skips live subsets
     (0be0667). A frozen copy is still drawn and still gets a SUB on the
     next zoom; left as it is for now.
   - After a zoom the 2D plots waited for Load, then took about 3 s: the
     desktop started one waiting task per 0.1 s turn, one task per metric,
     while the server computed the ~34 results in about 40 ms. Plots now
     reload a moved subset after 0.5 s still (bf51725), and every waiting
     task starts each turn (4183a82).
   - Extract is now offered on a frozen subset, on both clients (1060598).
   Still issue #22's scope: the proxy keeps both storage forms and branches
   on its own `isVariable`.
6. The caller branches on `isVariable` are deleted.
   Decided 2026-10-20: **metrics receive flat arrays on every kind.** Per-atom
   inputs are `(atoms, ...)` with one row per atom, frame after frame;
   `elements` and `masses` are per atom; `offsets` is always given. Read on
   2026-10-20, before building: no plot, table, browser or subbing code breaks,
   because every consumer outside the metrics package flattens a per-atom
   result first, and joining frames gives the same order as flattening
   `(frames, atoms, k)`. What changes is inside the metrics: the uniform-only
   branches (`offsets is None`, `ndim == 3`) in `force_rmse`,
   `force_mae_per_structure`, `force_net_residual`, `gyradius` and the
   per-element metrics, and the acceleration family. `accel_difference` adds a
   frame axis to flat input, so `accel_mae` and `accel_rmse` come out as one
   row; that is already wrong on variable data today. "Try a formula"
   (`try_formula.py`) cuts per-atom inputs by atom offsets, so today it is
   wrong on uniform data; flat inputs fix it. Prediction caches stay as they
   are on disk: the resolver flattens predictions when it reads them.
   Planned, one commit each:
   a. The metrics take flat input with offsets and give the same numbers as
      today on uniform data, checked on both forms before the switch.
      Per-structure sums use `np.add.reduceat` (one numpy call per segment
      list), because a Python loop over frames is slow on large uniform files.
      Measure it on 100,000 frames.
   b. The resolver sends `getAll*`, per-atom elements and masses, and
      `getOffsets()` on every kind, and flattens predictions and atom fields.
      The colouring path (`color_values.py`) gives `offsets` `[0, n]` for its
      one frame. The embedded `tests=` move to flat inputs.
      `ffast-cli metrics run --json` nests per-atom values one level less: an
      external change.
   c. The metrics' 3D branches are deleted. `accel_mae` and `accel_rmse` work
      per frame through offsets. `accel_mae_per_atom` (no tab uses it) means
      each atom of each frame; say so or drop it.
   d. The remaining callers of the old no-argument calls move to `getAll*`:
      prediction load, zero model, exports (always frame by frame), loader
      reporting. Then the old no-argument shapes and
      `test_ase_loader_shapes.py` go, and the `isVariable` branches in callers
      with them (sGDML's own check, cluster errors, the imported prediction
      offsets, the arrays routing, log lines).
   e. The Qt client's two: `UI/panels.py` `_flat_to_config` and
      `UI/loupe/canvas.py` move to offsets and one-frame calls. The proxy's own
      branches stay for issue #22. The wire keeps a `variable` flag computed
      from the offsets, for the proxy.
7. sGDML fills the same storage.
