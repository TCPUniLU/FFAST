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
for all frames.

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
  claims forces it does not have.

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
loader uses the fixed one. A large file that the random check classifies
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

**Not reversible:** a variable file that has no forces gets a new fingerprint,
because zeros are no longer hashed in. Its saved sessions and caches stop
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
- Fingerprints of variable files without forces (see section 3).
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
3. The two ASE loaders become one, reading per frame through `AtomsList`; both
   `check_homogeneity` and `loading_coordinator.py:388` pick it always. Missing
   data is absent, not zeros. Measure per-frame reads on a variable file before
   and after, since they move from memory to the file.
4. The "all frames" methods, and the ten callers moved to them one at a time.
5. One view replaces the three derived classes. Qt subsets break here.
6. The caller branches on `isVariable` are deleted.
7. sGDML fills the same storage.
