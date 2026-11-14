# Writing a reader

A **reader** teaches FFAST one file format. You write a small Python class that
turns the file into plain numpy arrays. FFAST does the rest: it loads the file
as a dataset or as a prediction, applies "every Nth frame", shows progress,
checks that the data fits in memory, shows your error messages, and can export
to the format too. You do not import anything from FFAST's inside, and you never
build a FFAST object.

You need Python and numpy. You do not need to know how FFAST works.

## What a reader does

A reader is a class with these parts. Level 1 is all you need to start.

| Part | What it is |
| --- | --- |
| `name` | A name no other reader has. It shows in the **Type** list. |
| `extensions` | The file endings you read, such as `["*.mol"]`. |
| `package` | Optional. The pip name that provides your reader, so FFAST can tell a user "run.mol needs the mol reader: pip install ffast-mol" when it is missing from a saved session. |
| `can_read(path)` | Optional. A quick look at the start of the file: `True` if this is your format. Needed only when other readers claim the same ending. |
| `__init__(path)` | Opens the file. |
| `n_frames()` | How many frames the file has. |
| `contents()` | What the file holds (see below). |
| `read(frames)` | The data of the frames asked for (see below). |

**`contents()`** returns a dict. Leave out what the file does not have.

| Key | Meaning |
| --- | --- |
| `positions`, `numbers`, `cell` | `True` when the file has atom positions, atomic numbers, a cell for each frame. |
| `energies`, `forces` | Lists of **names**. A file may hold several sets, such as `["dft", "mace"]`. |
| `frame_fields`, `atom_fields` | Names of extra numbers, one per frame or one per atom. They become Dataset Fields, usable in your own metrics. |
| `units` | Labels such as `{"energy": "eV"}`. FFAST never converts units; it shows the label and warns when a prediction's label differs from its dataset's. |

**`read(frames)`** gets frame numbers in increasing order, for example `[0, 3, 6]`,
and returns one dict of numpy arrays for those frames. All atoms of all those
frames come in one run, one after the other, plus `n_atoms` to say where each
frame ends.

| Key | Shape | Meaning |
| --- | --- | --- |
| `n_atoms` | `(frames,)` | Atoms in each frame. Always needed. |
| `numbers` | `(atoms,)` | Atomic numbers, whole numbers. |
| `positions` | `(atoms, 3)` | Positions. |
| `cell` | `(frames, 3, 3)` | The cell of each frame. |
| `energy:<name>` | `(frames,)` | One energy set. |
| `forces:<name>` | `(atoms, 3)` | One force set. |
| `frame:<field>` | `(frames,)` | A per-frame field. |
| `atom:<field>` | `(atoms,)` | A per-atom field. |

If a frame has no value for a key the other frames have, put `nan` there. If the
file never has a key, leave the key out. Never fill in zeros for a value you do
not have.

## A complete example

A `.mol` file holds frames. Each frame is the atom count, the energy, then one
`symbol x y z` line per atom:

```
3
-76.4
O 0.0 0.0 0.117
H 0.0 0.757 -0.469
H 0.0 -0.757 -0.469
3
-76.5
O 0.0 0.0 0.12
H 0.0 0.76 -0.47
H 0.0 -0.76 -0.47
3
-76.6
O 0.0 0.0 0.13
H 0.0 0.77 -0.48
H 0.0 -0.77 -0.48
```

This reader is the whole of it:

```python
import numpy as np
from ase.data import atomic_numbers

from ffast.readers import ReaderError


class MolReader:
    """A .mol file: for each frame, the atom count, the energy, then one
    "symbol x y z" line per atom."""

    name = "mol"                 # unique; the name in FFAST's Type list
    extensions = ["*.mol"]
    package = "ffast-mol"        # the pip name, for the "not installed" message

    def __init__(self, path):
        with open(path) as f:
            lines = [line.split() for line in f if line.strip()]
        self.frames, i = [], 0
        while i < len(lines):
            count = int(lines[i][0])
            atoms = lines[i + 2:i + 2 + count]
            if len(atoms) != count:
                raise ReaderError(f"Frame {len(self.frames)} has {len(atoms)} atoms, not {count}")
            self.frames.append((float(lines[i + 1][0]), atoms))
            i += 2 + count

    def n_frames(self):
        return len(self.frames)

    def contents(self):
        return {"positions": True, "numbers": True, "energies": ["dft"],
                "units": {"energy": "eV"}}

    def read(self, frames):
        chosen = [self.frames[i] for i in frames]
        atoms = [a for _, frame in chosen for a in frame]
        return {
            "n_atoms": np.array([len(frame) for _, frame in chosen]),
            "numbers": np.array([atomic_numbers[a[0]] for a in atoms]),
            "positions": np.array([a[1:4] for a in atoms], dtype=float),
            "energy:dft": np.array([energy for energy, _ in chosen]),
        }
```

Load a `.mol` file from FFAST's Load Dataset dialog and the file loads with
stride, progress, a memory check and pop-ups for your errors. Load the same
file as a prediction and FFAST checks it against its dataset first (below).

### Predictions

A file with energies or forces can be loaded as a prediction for a dataset that
is already loaded. If the file holds several sets, FFAST pairs the energy and
force names that match (`dft` with `dft`); the user can pick another pairing in
the dialog. A file with positions can also be a dataset: its other energy and
force names appear as extra predictions to add.

Before a prediction attaches, FFAST checks it against its dataset:

- **Refused**, with a message naming the first frame that differs: a different
  number of frames, different atoms per frame, different elements.
- **Warned**, and loaded: positions that differ by more than 0.001 Å (files
  often round positions), and unit labels that differ.

## Errors and warnings

Import two names from `ffast.readers`:

```python
from ffast.readers import ReaderError, warn
```

- `raise ReaderError("Frame 12 has no forces")` shows exactly this text in the
  pop-up, with no traceback. Use it for anything the user can fix.
- `warn("3 frames have no cell")` loads the file anyway; the text goes to the
  pop-ups and the bell list.
- Any other exception shows as "mol reader could not read run.mol: *message*",
  and its traceback goes to the server's log.

## Level 2: a big text file

A Level 1 reader that cannot jump to a frame reads the file from the start, even
for "every 10th frame". For a big text file where each frame is a block of
lines, write **Level 2** instead of `n_frames()` and `read()`:

- `find_frames(path)` reads the file once and returns where each frame starts
  and how many atoms it has. FFAST keeps that list on disk, so the second load
  does not read the file again.
- `parse(data, first_frame)` turns the bytes of whole frames into the same dict
  `read()` returns.

FFAST then parses in batches, in worker processes for a big file, reads only
the frames a stride keeps, and keeps the result so the next load of the same
file is quick. `parse` runs in worker processes: make it a `staticmethod` (or a
function at the top of your module) and keep the class importable.

```python
import numpy as np
from ase.data import atomic_numbers


class MolTextReader:
    """The same format as a Level 2 reader: FFAST finds the frames, keeps
    their list, and hands whole frames' text to parse(), in worker processes
    for a big file."""

    name = "mol-text"
    extensions = ["*.molt"]
    package = "ffast-mol"

    def __init__(self, path):
        self.path = path

    def contents(self):
        return {"positions": True, "numbers": True, "energies": ["dft"],
                "units": {"energy": "eV"}}

    @staticmethod
    def find_frames(path):
        """One pass: where each frame starts (and where the last one ends),
        and how many atoms each has."""
        starts, counts = [0], []
        with open(path, "rb") as f:
            while (head := f.readline()).strip():
                count = int(head)
                for _ in range(count + 1):          # the energy line, then the atoms
                    f.readline()
                counts.append(count)
                starts.append(f.tell())
        return np.array(starts), np.array(counts)

    @staticmethod
    def parse(data, first_frame):
        """Whole frames, as bytes, to the same dict read() returns."""
        lines = [line.split() for line in data.decode().splitlines() if line.strip()]
        counts, energies, atoms, i = [], [], [], 0
        while i < len(lines):
            count = int(lines[i][0])
            counts.append(count)
            energies.append(float(lines[i + 1][0]))
            atoms += lines[i + 2:i + 2 + count]
            i += 2 + count
        return {
            "n_atoms": np.array(counts),
            "numbers": np.array([atomic_numbers[a[0]] for a in atoms]),
            "positions": np.array([a[1:4] for a in atoms], dtype=float),
            "energy:dft": np.array(energies),
        }

    @staticmethod
    def energies(path, starts):
        """Optional: each frame's energy, from its energy line only. FFAST
        asks for it when the user samples a file by energy."""
        values = []
        with open(path, "rb") as f:
            for start in starts:
                f.seek(start)
                f.readline()                        # the atom count
                values.append(float(f.readline()))
        return {"dft": np.array(values)}
```

`energies(path, starts)` is optional. With it, a user can load a sample of
the file spread over its energy range. It gets the start of each frame it
should read and returns one float per frame for each energy name, NaN where a
frame has none, the same values `parse` gives. Read as little as you can: it
runs over every frame of the file, in worker processes, the first time a user
samples it. FFAST keeps the result next to the frame list.

If `parse` cannot read a file, raise `ffast.io.extxyz_arrays.NotFast("why")` and
FFAST reads the file another way (the ASE reader).

## Writing the format too (optional)

Give your class `open_writer` and say what the format can hold. Export then
lists your format, shows the reference values and each prediction as tick boxes,
greys out what the format cannot hold with the reason, and writes in chunks:

```python
from ase.data import chemical_symbols

from tests.ffast.readers.example_mol import MolReader


class MolWriter:
    skipped = 0                  # frames left out; this format leaves none out

    def __init__(self, path):
        self.out = open(path, "w")

    def write(self, chunk):      # called again for each chunk, the dict read() returns
        energies = next((v for k, v in chunk.items() if k.startswith("energy:")), None)
        start = 0
        for k, count in enumerate(chunk["n_atoms"]):
            # float(): NumPy 2 writes repr(np.float64(1.5)) as "np.float64(1.5)".
            self.out.write(f"{count}\n{0.0 if energies is None else float(energies[k])!r}\n")
            for z, xyz in zip(chunk["numbers"][start:start + count], chunk["positions"][start:start + count]):
                self.out.write(f"{chemical_symbols[z]} {float(xyz[0])!r} {float(xyz[1])!r} {float(xyz[2])!r}\n")
            start += count

    def close(self):
        self.out.close()


class MolReaderThatWrites(MolReader):
    name = "mol-writer"
    write_formats = [{"format": "mol", "label": "Mol", "ext": ".mol", "can_write": {
        "energies": 1, "forces": 0, "cell": False, "frame_fields": False, "atom_fields": False}}]

    @staticmethod
    def open_writer(path, contents, format=None):
        return MolWriter(path)
```

`can_write` may have `frames` (the most structures a file holds), `energies` and
`forces` (the most sets), and `cell`, `frame_fields`, `atom_fields`,
`variable_atoms` (`False` when the format cannot hold them). A missing key means
no limit. With `"forces": 0` the energies are still written and the forces are
left out; the Export dialog says so. Predictions come in the chunk as extra
`energy:<name>` and `forces:<name>` keys.

## Check your reader

`check_reader` tells you whether your reader follows these rules. It checks the
attributes, that `contents()` matches what `read()` returns, shapes and types,
that frames asked in chunks equal frames asked at once, and, when you wrote a
writer, a write-then-read round trip. It raises an `AssertionError` that names
the rule broken. Put it in a test with a small sample file:

```python
from ffast.readers import check_reader


def test_my_reader():
    check_reader(MolReader, "tests/data/small.mol")
```

## Install it

Package your reader and name the class in the entry point group `ffast.readers`.
In `pyproject.toml`:

```toml
[project.entry-points."ffast.readers"]
mol = "ffast_mol:MolReader"
```

After `pip install`, restart FFAST. The reader's `name` is in the Type list, and
FFAST picks it for files with your endings. Where several readers claim an
ending, FFAST asks each one's `can_read` in turn and the first that says yes
wins; the user can always pick another reader in the Type list.

Your reader works with the server and the browser.
