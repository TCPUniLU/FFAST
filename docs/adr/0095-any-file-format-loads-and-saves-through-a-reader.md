Status: Proposed 2026-11-12

# Any file format loads and saves through a reader

## The problem

A user has data in a format ASE cannot read. FFAST has no clean way to load it,
as a dataset or as a prediction, and no rules an outside author could follow to
add it.

What exists today (checked 2026-11-11, branch `lammps-live`):

- **Predictions know two formats, hard-coded.** `.npz` (fixed `E`/`F` keys) or
  ASE, decided in three places: `loadPrepredictedDataset`
  (`ffast/core/loading_coordinator.py:296`), `loadRemotePrediction` (`:922`)
  and the browser's `isNpzFile` (`remote_browser.js:206`). Any other file is
  probed as ASE and fails. No plugin can supply a prediction.
- **A dataset plugin gets almost nothing.** `loadDataset` (`:649-676`) gives
  "ase (auto)" a frame index, stride, progress, key choice, the memory check
  (ADR 0083) and error pop-ups. `sGDML` gets the stride only. Any other plugin
  is called as `loaderClass(path)` and gets none of these.
- **A plugin must build FFAST's internal object.** It subclasses
  `FlatDatasetLoader` (`ffast/loaders/dataset.py:485`) and sets about ten
  internal fields (`R_flat`, `z_flat`, `molecule_offsets`, `N`, `F_flat`,
  `lattice`, `chem`, `isVariable`, ...). A rename inside FFAST breaks every
  outside plugin.
- **Predictions inside a file** ("Add Prediction" rows, `prediction_keys`) and
  **Dataset Fields** (ADR 0023) work only for files loaded as "ase (auto)",
  whether ASE or our own xyz reader parses them.
- **A prediction is checked only partly.** `_predictionMatchesDataset`
  (`:478-504`) compares the frame count only when the file has energies, and
  atoms per frame only on the in-file path. Atom order and elements are never
  compared. A wrong file can attach silently.
- **Export** writes ASE formats or sGDML, chosen by an `if` in
  `_on_export_subset` (`ffast/session/server_session.py:1813`).
- **No guide** explains how to write a plugin for a new format.

## Decision

1. **A reader is a small class that returns plain numpy arrays.** It never
   touches FFAST's internal objects. FFAST builds its own dataset or prediction
   from what the reader returns. One reader per format serves every use:
   dataset, prediction, predictions inside the file, and export.

2. **Level 1, the simple contract.** A reader has:
   - `name` (unique across all readers), `extensions` (for example
     `["*.foo"]`), and optionally `package` (the pip name that provides it);
   - optionally `can_read(path)`: a quick look at the start of the file;
   - `__init__(path)`, `n_frames()`, `contents()` and `read(frames)`.

   `contents()` says what the file holds: positions, atomic numbers, cell,
   energy names and force names (listed separately), extra per-frame and
   per-atom fields, and optional unit labels.

   `read(frames)` gets frame numbers in increasing order and returns one dict
   of arrays for those frames. Atoms are one flat run plus `n_atoms` per frame,
   the storage FFAST already uses (ADR 0058). Keys are `numbers`, `positions`,
   `cell`, `energy:<name>`, `forces:<name>`, `frame:<field>` and
   `atom:<field>`.

3. **Level 2, the fast text contract, is optional.** Instead of `n_frames()`
   and `read()`, a reader supplies the following. It still has `contents()`,
   and `parse` returns the same keys as `read()`, energies included:
   - `find_frames(file)`: one pass, returning where each frame starts and its
     atom count;
   - `parse(data, first_frame)`: whole frames' bytes to the same dict as
     `read()`.

   FFAST supplies the rest, as it does for xyz today: the frame list saved in
   `~/.ffast/frame-index`, parsing in batches in worker processes, every n-th
   frame without reading the others, progress, and the memory check. Our xyz
   reader becomes the first Level 2 reader.

4. **FFAST does the common work for every reader:** stride, progress, the
   memory check, and error pop-ups. It asks `read()` for frames in increasing
   chunks, so a reader that can only read front to back still works. The
   memory need is estimated from `n_frames()` and the size of the first chunk.

5. **One reader for datasets and predictions.**
   - A file with positions can load as a dataset. Its other energy/force names
     appear as "Add Prediction" rows.
   - Any file with energies or forces can load as a prediction for a dataset
     that is already loaded.
   - FFAST pairs energy and force names that match (`dft` with `dft`). The user
     can change the pairing, as for ASE files today.

6. **Strict checks before a prediction attaches, for every reader.**
   - **Refused:** a different frame count, different atoms per frame, or
     different elements when the file has them. The message says what differs.
   - **Warned:** when both have positions and they differ by more than
     0.001 Å. Files often round positions, so this is not a refusal.
   - **Warned:** unit labels that differ from the dataset's. FFAST never
     converts units.

7. **Extra fields are optional.** A reader may list per-frame and per-atom
   fields. They become Dataset Fields (ADR 0023) like those of xyz files.

8. **Errors reach FFAST's pop-ups.** An author imports only two names:
   - `ReaderError("Frame 12 has no forces")`: the pop-up shows exactly this
     text, with no traceback.
   - `warn("3 frames have no cell")`: the load goes on, and the text goes to
     the bell list.

   Any other exception shows as "Foo reader could not read run.foo:
   \<message\>", with the traceback in the log.

9. **Choosing a reader.** Readers that claim the file's ending are asked
   `can_read` in turn, and the first yes wins. Without `can_read`, the ending
   decides. The user can always pick another reader in the Type list.

10. **ASE and `.npz` become readers.** ASE is a Level 1 reader for every format
    ASE reads. sGDML `.npz` is a Level 1 reader. The `"ase (auto)"` and
    `"sGDML"` branches in the loading code go away.

11. **Writers.** A reader may also write:
    - `open_writer(path, contents)` returns an object with `write(chunk)`
      (called several times, same dict shape as `read()`) and `close()`;
    - a `can_write` declaration says what the format can hold (for example
      one energy set, no cell, no fields).

    Export lists every reader that can write. Its dialog offers the reference
    values plus each loaded prediction, with only the reference ticked by
    default. It greys out what the format cannot hold, and says why, before
    writing starts.

12. **A missing reader.** A saved session stores the reader's name. If that
    reader is not installed, the pop-up names it and its `package` ("run.foo
    needs the Foo reader: pip install ffast-foo"), and the rest of the session
    loads.

13. **How authors connect a reader.** A pip package registers the reader
    class through an entry point. FFAST's own readers ship inside `ffast`.
    `docs/writing-a-reader.md` gives one Level 1 and one Level 2 example.
    `check_reader(MyReader, sample_file)` tells an author whether a reader
    follows these rules, including a write-then-read round trip when it has a
    writer.

14. **Scope.** Server and web client only. The desktop (Qt) client keeps its
    ASE and `.npz` paths unchanged (ADR 0045).

## Alternatives considered

- **Keep subclassing `FlatDatasetLoader`.** No new code, but authors depend on
  FFAST internals, and a plugin still gets no stride, progress or predictions.
- **Separate dataset readers and prediction readers.** Two plugins per format,
  and the "predictions inside the file" case fits neither.
- **Each reader handles stride, progress and memory itself.** Every author
  rewrites the same code, and most would skip it.
- **Convert every format to `.npz` on load.** A second copy of every file on
  disk and a slow first load, for no gain over reading arrays in memory.

## Consequences

- An author writes about four methods (Level 1) and gets every loading
  feature, plus a test that checks the reader.
- The loading code loses its format branches: one path for every reader.
- A Level 1 reader that cannot jump to a frame reads the file front to back,
  even for "every 10th frame". Level 2 exists for that case.
- A prediction that loaded before may now be refused by the stricter checks.
  That is intended: such a file did not match its dataset.
- Moving ASE and xyz onto the contract touches the most-used load path. It is
  a separate build step, with the existing load tests as its safety net.
- `FlatDatasetLoader` stays as FFAST's internal storage. It is no longer part
  of any outside contract.
