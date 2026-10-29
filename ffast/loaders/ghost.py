from ffast.loaders.model import ModelLoader
import numpy as np
from ffast.cache.fingerprint import md5FromArraysAndStrings


class GhostModelLoader(ModelLoader):
    isGhost = True
    modelName = "Ghost Model"

    def __init__(self, env, fingerprint, *args, **kwargs):
        self.fingerprint = fingerprint
        super().__init__(env, "N/A", *args, **kwargs)

    def predict(self, dataset, indices=None, batchSize=50, taskID=None):
        return None

    def getFingerprint(self):
        return self.fingerprint

    def getDisplayName(self):
        return f"*{self.getName()}"

    def initialise(self):
        # search for path and name in the object catalog
        info = self.env.objects.get(self.fingerprint)
        if info is not None:
            self.path = info["path"]
            self.setName(info["name"])
        else:
            self.path = "?"
            self.setName("?")
