from ffast.loaders.dataset import framesShaped
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
        # Zeros in the shape cached predictions keep (ADR 0058 step 6).
        offsets = dataset.getOffsets()
        forces = framesShaped(np.zeros((int(offsets[-1]), 3)), offsets, dataset.isVariable)
        return np.zeros(len(offsets) - 1), forces

    def getFingerprint(self):
        return self.fingerprint

    def getDisplayName(self):
        return f"{self.getName()}"

    def initialise(self):
        pass
