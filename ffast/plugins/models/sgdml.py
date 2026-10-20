from ffast.loaders.model import ModelLoader, uniformFrames
import numpy as np
from ffast.cache.fingerprint import md5FromArraysAndStrings
from ffast.loaders.dataset import FlatDatasetLoader


class sGDMLModelLoader(ModelLoader):
    """
    ModelLoader subclass specific to sGDML models.
    """

    # when True, implements just one predict function for forces and energies
    # when False, it's separate
    singlePredict = True
    modelName = "sGDML"
    modelFileExtension = "*.npz"

    def __init__(self, env, path, *args, **kwargs):
        """
        Initialises the ModelLoader given a path to the model. All methods
        that are only called once (i.e. loading the model) are performed here.

        Args:
            path (str): path to the model
        """
        super().__init__(env, path, *args, **kwargs)

        from sgdml.predict import GDMLPredict

        model = np.load(path, allow_pickle=True)
        self.modelInfo = model
        self.fpArrays = (model["perms"], model["R_d_desc_alpha"])
        self.model = GDMLPredict(model)

    def predict(self, dataset, indices=None, batchSize=50, taskID=None):
        """
        Prediction function for energies and forces using the loaded sGDML
        model.

        Args:
            dataset (DatasetLoader): DatasetLoader object from which to
                calculate predictions.
            indices (array, optional): Array of indices at which to predict
                energies and forces. Defaults to None.

        Returns:
            E (array): Nx1 array containing calculated energies.
            F (array): NxMx3 array containing calculated forces.
        """

        # sGDML requires uniform molecular structures
        if hasattr(dataset, 'isVariable') and dataset.isVariable:
            raise ValueError(
                "sGDML models require uniform molecular structures. "
                "This dataset contains variable-sized molecules (different number of atoms per configuration)."
            )

        R, _ = uniformFrames(dataset, indices)

        E, F = [], []
        nBatches = len(R) // batchSize + 1
        rBatches = np.array_split(R, nBatches)
        nBatches = len(rBatches)

        for i in range(nBatches):
            r = rBatches[i]
            (e, f) = self.model.predict(r.reshape((r.shape[0], -1)))
            E.append(e)
            F.append(f)

            if taskID is not None:
                self.eventPush(
                    "TASK_PROGRESS",
                    taskID,
                    progMax=nBatches,
                    prog=i,
                    message=f"sGDML batch predictions",
                    quiet=True,
                    percent=True,
                )

                if not self.env.tm.isTaskRunning(taskID):
                    return None

        E, F = np.concatenate(E), np.concatenate(F)

        return E, F.reshape(F.shape[0], -1, 3)

    def getFingerprint(self):
        """
        Mandatory method which should output a string acting as a unique
        identifier for this model.

        Returns:
            s (str): Unique fingerprint
        """

        fp = md5FromArraysAndStrings(*self.fpArrays)

        return fp

    def getInfo(self):
        return [
            ("N. perms", f"{len(self.modelInfo['perms'])}"),
            ("Sigma", f"{self.modelInfo['sig']}"),
            ("N. atoms", f"{self.model.n_atoms}"),
            ("N. train", f"{len(self.modelInfo['idxs_train'])}"),
            ("Code ver.", f"{self.modelInfo['code_version']}"),
        ]


class sGDMLDatasetLoader(FlatDatasetLoader):
    """An sGDML ``.npz`` file, in the flat storage every file dataset uses
    (ADR 0058, step 7). Every frame holds the same atoms in the same order,
    so the data is uniform and the file's (frames, atoms, 3) arrays are read
    as flat rows without copying."""

    datasetName = "sGDML"
    datasetFileExtension = "*.npz"
    isVariable = False

    def __init__(self, path, *args, **kwargs):
        super().__init__(path)
        self.data = np.load(path, allow_pickle=True)

        data = {key.lower(): value for key, value in self.data.items()}

        R = np.asarray(data["r"])
        self.N, nAtoms = R.shape[0], R.shape[1]
        self.z = np.asarray(data["z"])
        self.R_flat = R.reshape(-1, 3)
        self.molecule_offsets = np.arange(self.N + 1) * nAtoms
        # Missing results are absent, never zeros (ADR 0058, decision 1).
        self.E = np.asarray(data["e"]).reshape(-1) if "e" in data else None
        self.F_flat = np.asarray(data["f"]).reshape(-1, 3) if "f" in data else None
        self._z_flat = None

        if "lattice" in data:
            self.lattice = data["lattice"]
        else:
            self.lattice = None

        self.chem = self.zToChemicalFormula(self.z)

    @property
    def z_flat(self):
        """Every atom's element, frame after frame, built on request: the
        file holds one structure's elements."""
        if self._z_flat is None:
            self._z_flat = np.tile(self.z, self.N)
        return self._z_flat

    def getFingerprint(self):
        """The formula of a uniform file, unchanged (ADR 0058, section 3):
        one structure's elements, then positions, energies, forces."""
        arrays = (self.z, self.R_flat, self.E, self.F_flat)
        return md5FromArraysAndStrings(*(a for a in arrays if a is not None))

    def getChemicalFormula(self):
        return self.chem

    def getElements(self, index=None):
        return self.z  # every frame of an sGDML file has the same elements

    def getLattice(self, indices=None):
        return self.lattice

    @staticmethod
    def saveDataset(dataset, path, format=None, taskID=None):
        data = {
            "R": dataset.getCoordinates(),
            "E": dataset.getEnergies(),
            "F": dataset.getForces(),
            "z": dataset.getElements(),
            "lattice": dataset.getLattice(),  # not yet used at all
            "name": path,
        }

        md5 = md5FromArraysAndStrings(data["R"], data["E"], data["F"])
        data["md5"] = md5

        np.savez_compressed(path, **data)


def loadData(env):
    env.initialiseModelType(sGDMLModelLoader)
    env.initialiseDatasetType(sGDMLDatasetLoader)
