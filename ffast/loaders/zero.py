from ffast.loaders.model import ModelLoader
import numpy as np


class ZeroModelLoader(ModelLoader):
    modelName = "Zero Model"
    fingerprint = "zeroModel"
    singlePredict = True

    def __init__(self, env, *args, **kwargs):
        super().__init__(env, "N/A", *args, **kwargs)
        self.name = "Zero Model"

    def predict(self, dataset, indices=None, batchSize=50, taskID=None):
        R = dataset.getCoordinates()

        # Handle variable-sized datasets (R is list of arrays)
        if isinstance(R, list):
            n_molecules = len(R)
            energies = np.zeros(n_molecules)
            forces = [np.zeros_like(r) for r in R]
            return energies, forces

        # Handle uniform datasets (R is numpy array)
        return np.zeros(R.shape[0]), np.zeros_like(R)

    def getFingerprint(self):
        return self.fingerprint

    def getDisplayName(self):
        return f"{self.getName()}"

    def initialise(self):
        pass
