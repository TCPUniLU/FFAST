from collections import Counter
from ase.calculators.calculator import PropertyNotImplementedError
import numpy as np
import os
import logging
from scipy.spatial.distance import pdist
from ffast.core.events import EventClass
from ffast.cache.fingerprint import md5FromArraysAndStrings
from ffast.core.util import removeExtension, hexToRGB, cleanBondIdxsArray
from ffast.chemistry import zIntToZStr, zStrToZInt
from ffast.chemistry import covalentBonds
from scipy.spatial import distance_matrix
# config.userConfig.getConfig is imported lazily inside the methods that use it
# (ADR 0047: userConfig relocation is deferred), so this module imports flat-free.

logger = logging.getLogger("FFAST")
GLOBAL_DATASETS_COUNTER = 0


def toDistance(R):
    shape = R.shape
    try:
        dim = shape[2]
    except:
        return
    if shape[1] < 2:
        return

    y = []

    for i in range(len(R)):  ##goes through samples
        y.append(pdist(R[i]))

    y = np.array(y)
    return y


def isOneFrame(indices):
    """True when ``indices`` names one frame (a plain number), not all frames
    (None) or several (a list or array). Every numpy array has ``__iter__``,
    so an array of any shape counts as several."""
    return indices is not None and not hasattr(indices, "__iter__")


def stackFrames(flat, offsets):
    """Flat per-atom values as ``(frames, atoms, ...)`` when every structure
    has the same size. Raises ``ValueError`` when sizes differ, since no single
    3D array holds them."""
    counts = np.diff(np.asarray(offsets))
    if counts.size == 0 or (counts != counts[0]).any():
        raise ValueError("structures differ in size, so they cannot be stacked into one array")
    flat = np.asarray(flat)
    return flat.reshape(counts.size, int(counts[0]), *flat.shape[1:])


def framesShaped(flat, offsets, variable):
    """Flat per-atom values in the shape cached predictions keep, which are
    read one frame at a time: ``(frames, atoms, ...)`` for a uniform dataset,
    a list of per-frame arrays for a variable one. A fresh copy: the cache
    owns it."""
    flat = np.array(flat)
    if not variable:
        return stackFrames(flat, offsets)
    offsets = np.asarray(offsets, dtype=np.intp)
    return [flat[offsets[i]:offsets[i + 1]] for i in range(offsets.size - 1)]


def _readOnly(array):
    """A view that refuses writes. All-frames arrays are kept and shared, so
    a caller that changed one in place would change it for everyone."""
    view = np.asarray(array).view()
    view.flags.writeable = False
    return view


def _flatAtoms(values):
    """Per-atom values of all frames as one ``(atoms, ...)`` array, from either
    shape the old all-frames calls answer in: a list of per-frame arrays or one
    ``(frames, atoms, ...)`` array."""
    if isinstance(values, list):
        return np.concatenate([np.asarray(v) for v in values], axis=0)
    values = np.asarray(values)
    return values.reshape(-1, *values.shape[2:])


def frameRanges(frames, limit=8):
    """Frame numbers as a person reads them: runs of consecutive frames as
    ``a-b``, in the given order, e.g. ``3, 5, 7-9``. Past ``limit`` runs the
    rest is cut, with the count."""
    frames = [int(f) for f in np.asarray(frames).reshape(-1)]
    runs = []
    for f in frames:
        if runs and f == runs[-1][1] + 1:
            runs[-1][1] = f
        else:
            runs.append([f, f])
    text = [str(a) if a == b else f"{a}-{b}" for a, b in runs[:limit]]
    if len(runs) > limit:
        return ", ".join(text) + f", … ({len(frames)} frames)"
    return ", ".join(text)


class DatasetLoader(EventClass):
    """
    Base class for any dataset. Contains all dataset-agnostic methods.

    Every dataset-dependent method, e.g. loading the dataset, getting energies
    or forces, etc... are instead found in the specific DatasetLoader
    subclasses.
    """

    isSubDataset = False
    isAtomFiltered = False
    isGhost = False
    frozen = False
    isVariable = False  # Flag for uniform (False) vs variable-sized (True) datasets

    @property
    def canExtractAtoms(self):
        """Whether Extract may cut an atom subset from this dataset: a whole
        dataset or a frozen subset, whose frames stay put. Not a live subset,
        which changes with the zoom, nor an atom subset, which chose its atoms."""
        return not self.isSubDataset or self.frozen

    def __init__(self, path):
        self.path = path

        global GLOBAL_DATASETS_COUNTER

        from ffast.config.user import getConfig  # ADR 0047: lazy (userConfig deferred)
        colors = getConfig("datasetColors")
        nColors = len(colors)
        self.color = hexToRGB(colors[GLOBAL_DATASETS_COUNTER % nColors])

        GLOBAL_DATASETS_COUNTER += 1

    loadeeType = "dataset"

    zIntToZStr = zIntToZStr
    zStrToZInt = zStrToZInt

    datasetName = "N/A"
    datasetFileExtension = "*"
    saveFormats = [None]

    name = "?"
    loaded = False
    active = True

    # The physical unit the user gave each dimension of this dataset's numbers
    # ("energy" → "eV"). A dataset file declares none, so this starts empty and
    # a window fills it in. It belongs to the loaded dataset: a view cut from
    # it (a subset) has the same numbers, so it reads and writes its root's.
    UNIT_DIMENSIONS = ("energy", "force")
    units = None

    def rootDataset(self):
        """The loaded dataset this one was cut from, or itself."""
        dataset = self
        while getattr(dataset, "parent", None) is not None:
            dataset = dataset.parent
        return dataset

    def getUnits(self) -> dict:
        """{dimension: unit} for the dimensions that have one."""
        return dict(self.rootDataset().units or {})

    def setUnits(self, units: dict):
        """Replace the units (an empty unit removes it) and announce the change."""
        clean = {}
        for dimension, unit in (units or {}).items():
            if dimension not in self.UNIT_DIMENSIONS:
                raise ValueError(f"Unknown unit dimension: {dimension!r}")
            unit = str(unit or "").strip()
            if unit:
                clean[dimension] = unit
        root = self.rootDataset()
        if clean == (root.units or {}):
            return
        root.units = clean
        if root.loaded:
            root.eventPush("DATASET_STATE_CHANGED", root.fingerprint)

    def setUnit(self, dimension, unit):
        """Set one dimension's unit; an empty one removes it."""
        units = self.getUnits()
        units[dimension] = unit
        self.setUnits(units)

    def getElementsName(self):
        return [zIntToZStr[x] for x in self.getElements()]

    # ── Dataset Fields (ADR 0023) ───────────────────────────────────────────
    # Default: a loader carries no extra fields. ASE loaders override these to
    # read atoms.info / atoms.arrays keys lazily.
    def getFrameField(self, key, indices=None):
        return None

    def getAtomField(self, key, indices=None):
        return None

    def availableFieldKeys(self):
        """(frame_keys, atom_keys) exposable as Dataset Fields."""
        return [], []

    def zToChemicalFormula(self, z):
        """
        Converts a list of atomic numbers to a chemical formula (using organic
        chemistry conventions).

        Args:
            z (array): Array of integers representing atomic numbers.

        Returns:
            Formula (str): Chemical formula.
        """

        z = [zIntToZStr[x] for x in z]
        c = Counter(z)
        s = ""

        if "C" in c:
            s += f'C{c["C"]}'

        if "H" in c:
            s += f'H{c["H"]}'

        for atom, n in sorted(c.items()):
            if atom == "H" or atom == "C":
                continue

            if n < 2:
                n = ""
            s += f"{atom}{n}"

        return s

    def getFingerprint(self):
        z = self.getElements()
        r = self.getCoordinates()
        try:
            e = self.getEnergies()
        except (PropertyNotImplementedError, RuntimeError):
            logger.warning("Energy not available for fingerprint. Using coordinates only.")
            e = None
        try:
            f = self.getForces()
        except (PropertyNotImplementedError, RuntimeError):
            logger.warning("Forces not available for fingerprint. Using coordinates only.")
            f = None
        fp = md5FromArraysAndStrings(*(x for x in (z, r, e, f) if x is not None))

        return fp

    def getKey(self):
        return self.getFingerprint()

    def setName(self, name):
        if name == "":
            return self.setName(self.name)
        self.name = name
        if self.loaded:
            self.eventPush("OBJECT_NAME_CHANGED", self.fingerprint)

    def getName(self):
        return self.name

    def initialise(self):
        self.fingerprint = self.getFingerprint()

        name = removeExtension(os.path.basename(self.path))
        self.setName(name)

    def getPDist(self, indices=None):
        R = self.getCoordinates(indices=indices)
        return toDistance(R)

    def getDisplayName(self):
        tag = ""
        if self.isSubDataset:
            tag = "*"

        return f"{tag}{self.getName()}"

    def setActive(self, state):
        if self.active == state:
            return
        self.active = state
        self.eventPush("DATASET_STATE_CHANGED", self.fingerprint)

    def onDelete(self):
        pass

    def getBaseInfo(self):
        return [
            ("N. conf.", f"{self.getN()}"),
            ("N. atoms", f"{self.getNAtoms()}"),
            ("Chem. form.", self.getChemicalFormula()),
        ]

    def getInfo(self):
        # specific info to be overwritten by specific dataset types
        return []

    def setColor(self, r, g, b):
        self.color = [r, g, b]
        self.eventPush("OBJECT_COLOR_CHANGED", self.fingerprint)

    def getBondMatrix(self, index):
        """Which atoms of one structure are bonded. Bond sizes come from that
        structure's own elements, on every kind (ADR 0058, decision 1)."""
        r = self.getCoordinates(index)
        z = self.getElements(index)
        from ffast.config.user import getConfig  # ADR 0047: lazy (userConfig deferred)
        bondSizes = covalentBonds[z][:, z] * getConfig("loupeBondsLenience")
        d = distance_matrix(r, r)
        return d < bondSizes

    def getBondIndices(self, index):
        idxs = np.argwhere(self.getBondMatrix(index))
        _, idxs = cleanBondIdxsArray(idxs)

        return idxs

    def isDependentOn(self, fp):
        # base datasets cant depend on other things, thats for subdatasets
        return False

    def isUniform(self):
        """Check if this dataset has uniform molecular structures."""
        return not self.isVariable

    # All frames (ADR 0058, decision B). One meaning on every kind: flat arrays
    # with one row per atom, frame after frame, and offsets saying where each
    # frame's atoms start. The arrays are read-only and may be shared. These
    # defaults translate the older kind-shaped calls; loaders that hold the
    # flat arrays answer directly.

    def getOffsets(self):
        """Where each frame's atoms start in the flat arrays: ``(frames + 1,)``."""
        counts = np.atleast_1d(np.asarray(self.getNAtoms())).astype(np.int64)
        if counts.size == 1:
            counts = np.full(self.getN(), counts[0], dtype=np.int64)
        return _readOnly(np.concatenate([[0], np.cumsum(counts)]))

    def getAllElements(self):
        """Every atom's element, frame after frame: ``(atoms,)``."""
        z = np.asarray(self.getElements()).reshape(-1)
        offsets = self.getOffsets()
        if len(z) != offsets[-1]:
            z = np.tile(z, len(offsets) - 1)  # one structure's elements, shared by all
        return _readOnly(z)

    def getAllCoordinates(self):
        """Every atom's position, frame after frame: ``(atoms, 3)``."""
        return _readOnly(_flatAtoms(self.getCoordinates()))

    def getAllForces(self):
        """Every atom's force: ``(atoms, 3)``. Raises ``RuntimeError`` when the
        dataset has none."""
        forces = self.getForces()
        if forces is None:
            raise RuntimeError("Forces are not available for this dataset.")
        return _readOnly(_flatAtoms(forces))

    def getAllEnergies(self):
        """One energy per frame: ``(frames,)``. Raises ``RuntimeError`` when the
        dataset has none."""
        energies = self.getEnergies()
        if energies is None:
            raise RuntimeError("Energies are not available for this dataset.")
        return _readOnly(energies)

    def hasForces(self):
        """Whether the dataset has forces. This default asks for all of them;
        a loader that knows without reading them overrides it."""
        try:
            self.getForces()
            return True
        except Exception:
            return False

    def toMetaDict(self) -> dict:
        """Return lightweight metadata for RPC transfer (REMOTE_DATASET_META).

        Used by ffast-server to announce loaded datasets to connected clients
        without transferring coordinate arrays.

        Returns
        -------
        dict with keys: name, n, has_forces, is_sub, variable, elements, offsets
        """
        has_forces = self.hasForces()
        # Stage 4c: carry cheap per-atom elements so the client proxy serves
        # element labels / atom-filter without fetching the big R/F arrays.
        elements = None
        try:
            elements = np.asarray(self.getElements()).reshape(-1).astype(int).tolist()
        except Exception:
            pass
        return {
            "name": self.getName(),
            "n": self.getN(),
            "has_forces": has_forces,
            "is_sub": bool(self.isSubDataset),
            "variable": False,
            "elements": elements,
            "offsets": None,
            "path": self.path,
            "source_type": getattr(type(self), "datasetName", None),
            **self.lineageMeta(),
            "active": bool(getattr(self, "active", True)),
            "frozen": bool(getattr(self, "frozen", False)),
            "units": dict(self.units or {}),
        }

    def lineageMeta(self) -> dict:
        """Which configurations this dataset shares with another (ADR 0056).

        ``parent`` is the fingerprint of the dataset this one was cut from;
        ``parent_frames[i]`` is the parent frame that frame ``i`` is, or None
        when frame ``i`` is the parent's frame ``i``. A loaded dataset has
        neither. The browser uses this to show the same structure, not the
        same frame number, in two related views."""
        return {"parent": None, "parent_frames": None}

    def to_transfer_arrays(self) -> dict:
        """Serialize geometry arrays for SubDataset transfer over the RPC channel.

        Returns a dict ready to be passed to ``ffast.protocol.rpc.pack_arrays``.
        Handles uniform datasets (R shape Nxnatomsx3).

        Keys always present: ``n``, ``variable``, ``R``, ``E``.
        Optional keys (None when unavailable): ``F``, ``z``.

        See Also
        --------
        FlatDatasetLoader.to_transfer_arrays : override for variable datasets.
        """
        arrays: dict = {
            "n": np.array([self.getN()]),
            "variable": np.array([0]),
            "R": self.getCoordinates(),
        }
        try:
            arrays["F"] = self.getForces()
        except Exception:
            arrays["F"] = None
        try:
            arrays["z"] = self.getElements()
        except Exception:
            arrays["z"] = None
        try:
            arrays["E"] = np.asarray(
                self.getEnergies(), dtype=np.float64
            ).reshape(-1)
        except Exception:
            arrays["E"] = None
        return arrays


class FlatDatasetLoader(DatasetLoader):
    """
    The one storage form for file datasets (ADR 0058, decision 1).

    Every structure's atoms sit in one flat run, and offsets say where each
    structure starts. A uniform dataset is the case where every structure
    lists the same atoms in the same order.

    Data structure:
        R_flat: (total_atoms, 3)        - All positions concatenated
        F_flat: (total_atoms, 3)        - All forces concatenated
        E: (N,)                         - Energies
        z_flat: (total_atoms,)          - Atomic numbers concatenated
        molecule_offsets: (N+1,)        - Start indices [0, n1, n1+n2, ..., total]

    ``isVariable`` is a fact about the data, set by the subclass that reads
    it. Two things still depend on it (ADR 0058 step 6): the shape of the
    older no-argument calls, which serve the wire to the Qt proxy and the
    cached predictions, and the capability checks of models that need the
    same atoms in every frame. Uniform data answers those calls as
    (frames, atoms, ...) arrays and one structure's elements, variable data
    as per-frame lists and every atom's elements.

    Identity, naming, colour and events come from DatasetLoader.
    """

    isVariable = True
    datasetName = "Variable Dataset"

    def __init__(self, path):
        super().__init__(path)

        # To be set by subclasses. R_flat (total_atoms, 3), F_flat
        # (total_atoms, 3) or None, E (N,) or None and z_flat (total_atoms,)
        # are not preset: a subclass may provide them as attributes or as
        # properties built on request (ffast.loaders.ase.aseDatasetLoader).
        self.molecule_offsets = None  # (N+1,)
        self.N = None               # Number of molecules

    def hasForces(self):
        """Whether every frame has forces, without reading them all."""
        return self.F_flat is not None

    def toMetaDict(self) -> dict:
        """Return lightweight metadata for RPC transfer (REMOTE_DATASET_META).

        Same interface as DatasetLoader.toMetaDict — used by ffast-server
        to announce loaded datasets without transferring coordinate arrays.

        Returns
        -------
        dict with keys: name, n, has_forces, is_sub, variable, elements, offsets
        """
        if not self.isVariable:
            return DatasetLoader.toMetaDict(self)
        has_forces = self.hasForces()
        # Stage 4c: carry cheap flat elements + molecule offsets so the client
        # proxy serves labels / atom-filter / scatter sub-indexing without
        # fetching the big R_flat/F_flat arrays.
        elements = offsets = None
        try:
            elements = np.asarray(self.z_flat).reshape(-1).astype(int).tolist()
        except Exception:
            pass
        try:
            offsets = np.asarray(self.molecule_offsets).astype(int).tolist()
        except Exception:
            pass
        return {
            "name": self.getName(),
            "n": self.getN(),
            "has_forces": has_forces,
            "is_sub": bool(self.isSubDataset),
            "variable": True,
            "elements": elements,
            "offsets": offsets,
            "path": self.path,
            "source_type": getattr(type(self), "datasetName", None),
            "parent": None,
            "parent_frames": None,
            "active": bool(getattr(self, "active", True)),
            "frozen": False,
            "units": dict(self.units or {}),
        }

    def to_transfer_arrays(self) -> dict:
        """Serialize geometry arrays for SubDataset transfer over the RPC channel.

        Returns a dict ready to be passed to ``ffast.protocol.rpc.pack_arrays``.
        Variable data uses the flat array format (R_flat, offsets, F_flat,
        z_flat); uniform data the stacked one of DatasetLoader.

        Keys always present: ``n``, ``variable``, ``R_flat``, ``offsets``.
        Optional keys (None when unavailable): ``F_flat``, ``z_flat``, ``E``.

        See Also
        --------
        DatasetLoader.to_transfer_arrays : base implementation for uniform datasets.
        """
        if not self.isVariable:
            return DatasetLoader.to_transfer_arrays(self)
        arrays: dict = {
            "n": np.array([self.getN()]),
            "variable": np.array([1]),
            "R_flat": self.R_flat,
            "offsets": self.molecule_offsets,
            "F_flat": self.F_flat,
            "z_flat": self.z_flat,
        }
        try:
            arrays["E"] = np.asarray(
                self.getEnergies(), dtype=np.float64
            ).reshape(-1)
        except Exception:
            arrays["E"] = None
        return arrays

    def getOffsets(self):
        return _readOnly(self.molecule_offsets)

    def getAllElements(self):
        return _readOnly(self.z_flat)

    def getAllCoordinates(self):
        return _readOnly(self.R_flat)

    def getAllForces(self):
        if self.F_flat is None:
            raise RuntimeError("Forces are not available for this dataset.")
        return _readOnly(self.F_flat)

    def getAllEnergies(self):
        if self.E is None:
            raise RuntimeError("Energies are not available for this dataset.")
        return _readOnly(self.E)

    def getNAtoms(self, index=None):
        """
        Return atom count(s).

        Args:
            index: If int, the count for that molecule. If None, one count on
                uniform data, shared by all frames, and an array of every
                frame's count on variable data.

        Returns:
            int or ndarray: Atom count(s)
        """
        if index is None:
            if not self.isVariable:
                return int(self.molecule_offsets[1] - self.molecule_offsets[0])
            return np.diff(self.molecule_offsets)
        else:
            return int(self.molecule_offsets[index+1] - self.molecule_offsets[index])

    def getN(self):
        """Return number of molecules/configurations."""
        return self.N

    def _frame(self, flat, index):
        """One frame's rows of a flat per-atom array."""
        return flat[self.molecule_offsets[index]:self.molecule_offsets[index + 1]]

    def _frames(self, flat, indices):
        """Several frames (all when None) of a flat per-atom array, in the
        kind's shape: (frames, atoms, ...) on uniform data, a fresh array
        callers may change in place; a list of per-frame arrays otherwise."""
        if not self.isVariable:
            frames = flat.reshape(self.N, -1, *flat.shape[1:])
            if indices is None:
                return frames.copy()
            return frames[np.asarray(indices)]
        if indices is None:
            indices = range(self.N)
        return [self._frame(flat, idx) for idx in indices]

    def getCoordinates(self, indices=None):
        """
        Get atomic coordinates for molecule(s).

        Args:
            indices: None (all), int (single), or array (multiple)

        Returns:
            Single molecule: ndarray (n_atoms_i, 3)
            Multiple molecules: see _frames
        """
        if isOneFrame(indices):
            return self._frame(self.R_flat, indices)
        return self._frames(self.R_flat, indices)

    def getEnergies(self, indices=None):
        """Get energies for molecule(s)."""
        if self.E is None:
            raise RuntimeError("Energies are not available for this dataset.")
        if indices is None:
            return self.E
        else:
            return self.E[indices]

    def getForces(self, indices=None):
        """
        Get forces for molecule(s).

        Returns:
            Single molecule: ndarray (n_atoms_i, 3)
            Multiple molecules: see _frames
        """
        if self.F_flat is None:
            raise RuntimeError("Forces are not available for this dataset.")
        if isOneFrame(indices):
            return self._frame(self.F_flat, indices)
        return self._frames(self.F_flat, indices)

    def getElements(self, index=None):
        """
        Get atomic numbers.

        Args:
            index: If int, z for that molecule. If None, one structure's
                elements on uniform data, shared by all frames, and z_flat
                (every atom) on variable data.

        Returns:
            ndarray: Atomic numbers
        """
        if index is None:
            if not self.isVariable:
                return self.getElements(0)
            return self.z_flat
        return self._frame(self.z_flat, index)

    # ── Dataset Fields (ADR 0023) — default no fields; ASE subclass overrides ──
    def getFrameField(self, key, indices=None):
        return None

    def getAtomField(self, key, indices=None):
        return None

    def availableFieldKeys(self):
        return [], []

    def getChemicalFormula(self):
        """The formula on uniform data, the range of atom counts otherwise."""
        if not self.isVariable:
            return self.zToChemicalFormula(self.getElements())
        atom_counts = self.getNAtoms()
        return f"Variable ({atom_counts.min()}-{atom_counts.max()} atoms)"

    def getFingerprint(self):
        """Generate fingerprint from all data."""
        # Use all flat arrays for fingerprint
        arrays = (self.z_flat, self.R_flat, self.E, self.F_flat)
        fp = md5FromArraysAndStrings(*(i for i in arrays if i is not None))

        return fp

    def getPDist(self, indices=None):
        """Get pairwise distances - handle variable sizes."""
        if not self.isVariable:
            return DatasetLoader.getPDist(self, indices)
        coords = self.getCoordinates(indices=indices)

        if isinstance(coords, list):
            # Multiple molecules
            result = []
            for r in coords:
                if len(r) >= 2:
                    result.append(pdist(r))
                else:
                    result.append(None)
            return result
        else:
            # Single molecule
            if len(coords) >= 2:
                return pdist(coords)
            else:
                return None

    def getBaseInfo(self):
        if not self.isVariable:
            return DatasetLoader.getBaseInfo(self)
        atom_counts = self.getNAtoms()
        return [
            ("N. conf.", f"{self.getN()}"),
            ("N. atoms", f"{atom_counts.min()}-{atom_counts.max()}"),
            ("Chem. form.", self.getChemicalFormula()),
        ]


class DatasetView(DatasetLoader):
    """Everything cut from another dataset (ADR 0058, decision 2).

    A view is a parent, a frame map and an atom mask. Frame ``i`` of the view
    is frame ``frames[i]`` of its parent (every frame when ``frames`` is None);
    its atoms are the parent's atoms at ``atoms`` (every atom when None). That
    translation is written once, here, and holds for any parent, a view
    included: a subset of a subset is a view whose parent is a view.

    The subset, frozen subset and atom subset users see are states of a view:

    - a subset follows a plot's zoom (``live``): ``setIndices`` moves its frames;
    - a frozen subset keeps the frames it was frozen with;
    - an atom subset keeps the same atoms in every frame. It is refused when a
      structure lacks one of them, so it never holds a frame it cannot answer.

    Each state keeps the fingerprint formula it had as a class of its own.
    Answers follow the parent's kind: (frames, atoms, ...) arrays below a
    uniform parent, lists below a variable one, until ADR 0058 step 6.
    """

    loadeeType = "dataset"
    isSubDataset = True

    def __init__(self, parent, frames=None, atoms=None, model=None, subName=None, live=False):
        super().__init__("")
        self.parent = parent
        self.model = model
        self.subName = subName
        self.live = live
        self.loaded = parent.loaded
        # Kept as given (np.asarray only), since the fingerprint hashes them.
        self.frames = None if frames is None else np.asarray(frames)
        self.atoms = None if atoms is None else np.asarray(atoms)
        if self.atoms is not None:
            self._checkEveryStructureHas(self.atoms)
        self.updatePath()

    @classmethod
    def subset(cls, parent, model, frames, subName):
        """A subset that follows a plot's zoom, named after the plot."""
        return cls(parent, frames=np.array([0]) if frames is None else frames,
                   model=model, subName=subName, live=True)

    @classmethod
    def frozenSubset(cls, parent, model, frames, subName):
        """A subset that keeps its frames."""
        return cls(parent, frames=frames, model=model, subName=subName)

    @classmethod
    def atomSubset(cls, parent, atoms):
        """The same atoms of every frame."""
        return cls(parent, atoms=atoms)

    # ── fingerprints: one formula per state, unchanged from the old classes ──

    @staticmethod
    def subsetFingerprint(parent, model, subName):
        """Parent, model and name, not frames: the same subset is refreshed as
        the zoom changes."""
        if model is None:
            return md5FromArraysAndStrings(parent.fingerprint, subName)
        return md5FromArraysAndStrings(parent.fingerprint, model.fingerprint, subName)

    @staticmethod
    def frozenFingerprint(parent, model, subName, frames):
        if model is None:
            return md5FromArraysAndStrings(parent.fingerprint, subName, frames)
        return md5FromArraysAndStrings(parent.fingerprint, model.fingerprint, subName, frames)

    @staticmethod
    def atomSubsetFingerprint(parent, atoms):
        return md5FromArraysAndStrings(parent.fingerprint, atoms)

    def getFingerprint(self):
        if self.atoms is not None:
            return self.atomSubsetFingerprint(self.parent, self.atoms)
        if self.live:
            return self.subsetFingerprint(self.parent, self.model, self.subName)
        return self.frozenFingerprint(self.parent, self.model, self.subName, self.frames)

    # ── state, as the old class attributes read it ──

    @property
    def isAtomFiltered(self):
        return self.atoms is not None

    @property
    def frozen(self):
        return self.atoms is None and not self.live

    @property
    def isVariable(self):
        return bool(getattr(self.parent, "isVariable", False))

    @property
    def datasetName(self):
        if self.atoms is not None:
            return "Atom-Filtered Dataset"
        return "SubDataset" if self.live else "SubDataset (frozen)"

    def setIndices(self, indices):
        """Move a live subset to new parent frames. Other states keep theirs."""
        if not self.live:
            return
        self.frames = np.asarray([0] if indices is None else indices)
        self.eventPush("SUBDATASET_INDICES_CHANGED", self.fingerprint)
        self.eventPush("DATASET_UPDATED", self.fingerprint)

    def updatePath(self):
        if self.atoms is not None:
            self.path = f"{self.parent.getName()},atomFilter"
        elif self.model is None:
            self.path = f"{self.subName},{self.parent.getName()}"
        else:
            self.path = f"{self.subName},{self.parent.getName()},{self.model.getName()}"

    def initialise(self):
        self.fingerprint = self.getFingerprint()
        if self.fingerprint == self.parent.fingerprint:
            raise ValueError(f"A view of {self.parent} has the same fingerprint. Aborted.")
        self.setName(self.path)

    def lineageMeta(self) -> dict:
        frames = None if self.frames is None else self._frameIdx().tolist()
        return {"parent": self.parent.fingerprint, "parent_frames": frames}

    def isDependentOn(self, obj):
        return obj is not None and (obj is self.parent or obj is self.model)

    # ── the translation: view frame -> parent frame, parent atoms -> view atoms ──

    def _frameIdx(self):
        return self.frames.astype(np.int64, copy=False).reshape(-1)

    def _atomIdx(self):
        return self.atoms.astype(np.int64, copy=False).reshape(-1)

    def _checkEveryStructureHas(self, atoms):
        counts = np.diff(np.asarray(self.parent.getOffsets()))
        if self.frames is not None:
            counts = counts[self._frameIdx()]
        highest = int(np.max(atoms)) if np.size(atoms) else -1
        if counts.size and highest >= int(counts.min()):
            short = int(np.sum(counts <= highest))
            smallest = int(counts.min())
            size = (f"has {smallest} atoms, numbered 0 to {smallest - 1}" if smallest
                    else "has no atoms")
            raise ValueError(
                f"Atom {highest} is missing from {short} of {counts.size} structures.\n\n"
                f"Every structure must have the atoms you extract. The smallest {size}.")

    def _parentFrames(self):
        """Every parent frame this view holds, in the view's order."""
        if self.frames is None:
            return np.arange(self.parent.getN())
        return self._frameIdx()

    def _parentIndex(self, indices):
        """The parent's frame number(s) for one frame, several, or all (None)."""
        if self.frames is None:
            return indices
        frames = self._frameIdx()
        if indices is None:
            return frames
        if isOneFrame(indices):
            return int(frames[indices])
        return frames[np.asarray(indices, dtype=np.int64)]

    def _maskAtoms(self, values, oneFrame):
        """Keep the view's atoms of a per-atom answer, in either kind's shape."""
        if self.atoms is None or values is None:
            return values
        atoms = self._atomIdx()
        if oneFrame:
            return np.asarray(values)[atoms]
        if isinstance(values, list):
            return [np.asarray(v)[atoms] for v in values]
        return np.asarray(values)[:, atoms]

    def _flatIndex(self):
        """Where each of the view's atoms sits in the parent's flat arrays."""
        offsets = np.asarray(self.parent.getOffsets())
        frames = self._parentFrames()
        starts = offsets[frames]
        if self.atoms is not None:
            return (starts[:, None] + self._atomIdx()[None, :]).reshape(-1)
        counts = offsets[frames + 1] - starts
        within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
        return np.repeat(starts, counts) + within

    # ── per frame ──

    def getN(self):
        return self.parent.getN() if self.frames is None else len(self._frameIdx())

    def getCoordinates(self, indices=None):
        values = self.parent.getCoordinates(indices=self._parentIndex(indices))
        return self._maskAtoms(values, isOneFrame(indices))

    def getForces(self, indices=None):
        values = self.parent.getForces(indices=self._parentIndex(indices))
        return self._maskAtoms(values, isOneFrame(indices))

    def getEnergies(self, indices=None):
        return self.parent.getEnergies(indices=self._parentIndex(indices))

    def getLattice(self, indices=None):
        return self.parent.getLattice(self._parentIndex(indices))

    def getElements(self, index=None):
        if isOneFrame(index):
            return self._maskAtoms(self.parent.getElements(self._parentIndex(index)), True)
        if self.isVariable:
            return self.getAllElements()
        # Uniform parent: one structure's elements, shared by all frames.
        return self._maskAtoms(self.parent.getElements(), True)

    def getNAtoms(self, index=None):
        if isOneFrame(index):
            if self.atoms is not None:
                return len(self._atomIdx())
            return self.parent.getNAtoms(self._parentIndex(index))
        if self.isVariable:
            return np.diff(np.asarray(self.getOffsets()))
        return len(self._atomIdx()) if self.atoms is not None else self.parent.getNAtoms()

    def getBondMatrix(self, index):
        """The parent frame's bonds between the view's atoms."""
        bonds = self.parent.getBondMatrix(self._parentIndex(index))
        if self.atoms is None:
            return bonds
        atoms = self._atomIdx()
        return np.asarray(bonds)[np.ix_(atoms, atoms)]

    def getPDist(self, indices=None):
        if self.atoms is None:
            return self.parent.getPDist(indices=self._parentIndex(indices))
        if self.isVariable:
            return FlatDatasetLoader.getPDist(self, indices)
        return super().getPDist(indices)

    def getFrameField(self, key, indices=None):
        return self.parent.getFrameField(key, indices=self._parentIndex(indices))

    def getAtomField(self, key, indices=None):
        values = self.parent.getAtomField(key, indices=self._parentIndex(indices))
        return self._maskAtoms(values, isOneFrame(indices))

    def availableFieldKeys(self):
        return self.parent.availableFieldKeys()

    def hasForces(self):
        return self.parent.hasForces()

    # ── all frames (ADR 0058, decision B): the parent's flat arrays, cut ──

    def getOffsets(self):
        if self.atoms is not None:
            counts = np.full(self.getN(), len(self._atomIdx()), dtype=np.int64)
        else:
            offsets = np.asarray(self.parent.getOffsets())
            frames = self._parentFrames()
            counts = offsets[frames + 1] - offsets[frames]
        return _readOnly(np.concatenate([[0], np.cumsum(counts)]).astype(np.int64))

    def getAllElements(self):
        return _readOnly(np.asarray(self.parent.getAllElements())[self._flatIndex()])

    def getAllCoordinates(self):
        return _readOnly(np.asarray(self.parent.getAllCoordinates())[self._flatIndex()])

    def getAllForces(self):
        return _readOnly(np.asarray(self.parent.getAllForces())[self._flatIndex()])

    def getAllEnergies(self):
        return _readOnly(np.asarray(self.parent.getAllEnergies())[self._parentFrames()])

    @property
    def molecule_offsets(self):
        """Where each frame's atoms start, below a variable parent."""
        if not self.isVariable:
            raise AttributeError("molecule_offsets")
        return self.getOffsets()

    # ── reporting ──

    def getChemicalFormula(self):
        if self.isVariable:
            counts = np.diff(np.asarray(self.getOffsets()))
            if counts.size == 0:
                return "0 atoms"
            if self.atoms is not None:
                # Same atoms in every frame: name them when their elements agree.
                z = np.asarray(self.getAllElements()).reshape(counts.size, -1)
                if (z == z[0]).all():
                    return self.zToChemicalFormula(z[0])
            return f"Variable ({counts.min()}-{counts.max()} atoms)"
        if self.atoms is None:
            return self.parent.getChemicalFormula()
        return self.zToChemicalFormula(self.getElements())

    def getBaseInfo(self):
        if self.isVariable:
            return FlatDatasetLoader.getBaseInfo(self)
        return super().getBaseInfo()

    def getInfo(self):
        if self.atoms is not None:
            return [
                ("Parent", self.parent.getDisplayName()),
                ("Viewed Elements", ",".join(set(self.getElementsName()))),
                ("Atoms", ", ".join(str(a) for a in self._atomIdx())),
            ]
        model = "None" if self.model is None else self.model.getDisplayName()
        frames = self._frameIdx()
        return [
            ("Parent", self.parent.getDisplayName()),
            ("Model", model),
            ("Plots", self.subName),
            ("Frames", f"{frameRanges(frames)} ({len(frames)} of {self.parent.getN()})"),
        ]

    def toMetaDict(self) -> dict:
        meta = super().toMetaDict()
        meta["source_type"] = self.datasetName
        if self.isVariable:
            meta["variable"] = True
            meta["offsets"] = np.asarray(self.getOffsets()).astype(int).tolist()
        return meta

    def to_transfer_arrays(self) -> dict:
        """As the parent's kind sends them; below a variable parent, the flat
        arrays and offsets, which pack whatever the structures' sizes."""
        if not self.isVariable:
            return super().to_transfer_arrays()
        try:
            forces = self.getAllForces()
        except Exception:
            forces = None
        try:
            energies = np.asarray(self.getEnergies(), dtype=np.float64).reshape(-1)
        except Exception:
            energies = None
        return {
            "n": np.array([self.getN()]),
            "variable": np.array([1]),
            "R_flat": self.getAllCoordinates(),
            "offsets": self.getOffsets(),
            "F_flat": forces,
            "z_flat": self.getAllElements(),
            "E": energies,
        }
