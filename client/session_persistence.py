"""Session save/load and dataset export for the Environment (ADR 0020).

``SessionPersistence`` serializes the session — the cache entities plus object
metadata (``info.json`` + ``cache/*.npz``) — and restores it, and exports a
single dataset through its loader-specific serializer.  It depends only on the
owning ``Environment``, reached through the same-named delegators below so the
method bodies are the former ``Environment`` methods unchanged.

The server-vs-in-process *routing* (``requestSessionSave`` / ``requestSessionLoad``)
stays on ``Environment``; this class is the in-process worker those dispatch to.
"""

import os
import glob
import json
import logging

import numpy as np

from client.dataType import SubDataEntity

logger = logging.getLogger("FFAST")


class SessionPersistence:
    """Save/load a session and export datasets (ADR 0020)."""

    def __init__(self, env):
        self._env = env

    # ── env-domain delegators: keep the moved method bodies verbatim ──────
    def eventPush(self, *args, **kwargs):
        return self._env.eventPush(*args, **kwargs)

    def newTask(self, *args, **kwargs):
        return self._env.newTask(*args, **kwargs)

    def getDataType(self, dataTypeKey):
        return self._env.data.getDataType(dataTypeKey)

    def getAllDatasets(self, *args, **kwargs):
        return self._env.datasets.all(*args, **kwargs)

    def getAllModels(self, *args, **kwargs):
        return self._env.models.all(*args, **kwargs)

    def loadDataset(self, *args, **kwargs):
        return self._env.loadDataset(*args, **kwargs)

    def lookForGhosts(self):
        return self._env.lookForGhosts()

    @property
    def cache(self):
        return self._env.cache

    @property
    def objects(self):
        return self._env.objects

    @property
    def datasetTypes(self):
        return self._env.datasetTypes

    # ── save / load ───────────────────────────────────────────────────────
    def save(self, path, taskID=None):
        """Persist the session cache and object metadata so it can be restored later."""
        if not os.path.exists(path):
            os.mkdir(path)

        ## SAVE CACHE
        cacheDir = os.path.join(path, "cache")
        if not os.path.exists(cacheDir):
            os.mkdir(cacheDir)

        from ffast.metrics.models import MetricResult
        for key, entity in self.cache.items():
            if isinstance(entity, SubDataEntity):
                continue

            # Metric results (client spine / Stage 4a server-computed) are
            # recomputable caches, not DataEntity objects — skip them; on reload
            # the plots re-request them from the server metric channel.
            if isinstance(entity, MetricResult) or not hasattr(entity, "data"):
                continue

            # Convert any inhomogeneous lists (e.g. variable-sized
            # dataset forces) to object arrays so numpy can save them.
            saveData = {}
            for k, v in entity.data.items():
                if isinstance(v, list):
                    saveData[k] = np.array(v, dtype=object)
                else:
                    saveData[k] = v
            np.savez_compressed(
                os.path.join(cacheDir, key),
                entityDataTypeKey=entity.dataType.key,
                cacheKey=key,
                **saveData,
            )

        ## GENERATE INFO
        info = {"objects": {}}
        for o in self.getAllDatasets(excludeSubs=True):
            obj_info = {
                "name": o.getName(),
                "path": o.path,
                "type": "dataset",
                "datasetType": getattr(o, "_source_type", None),
            }

            # Store ASE key selections if present
            if hasattr(o, 'selected_energy_key') and o.selected_energy_key:
                obj_info["ase_energy_key"] = o.selected_energy_key
            if hasattr(o, 'selected_force_key') and o.selected_force_key:
                obj_info["ase_force_key"] = o.selected_force_key

            info["objects"][o.fingerprint] = obj_info

        for o in self.getAllModels():
            info["objects"][o.fingerprint] = {
                "name": o.getName(),
                "path": o.path,
                "type": "model",
            }

        # Merge any additional catalog entries (including ghost models) the live
        # dataset/model registries above didn't already cover.
        for fp, obj_info in self.objects.snapshot().items():
            if fp not in info['objects']:
                info['objects'][fp] = obj_info

        # dataset/model names and paths

        ## SAVE INFO
        infoFile = os.path.join(path, "info.json")
        with open(infoFile, "w") as f:
            json.dump(info, f, indent=4)

    def taskLoad(self, path):
        """Queue restoration of a previously saved session."""
        self.newTask(
            self.load,
            args=(path,),
            visual=True,
            name="Loading save",
            threaded=True,
        )

    def load(self, path, taskID=None):
        """Rebuild datasets, ghost models, and cached entities from a saved session."""
        # LOAD INFO (names etc)
        infoFile = os.path.join(path, "info.json")
        info = None
        if os.path.exists(infoFile):
            with open(infoFile, "r") as f:
                info = json.load(f)
            self.loadInfo(info)

        ## LOAD DATASETS AND MODELS FROM INFO
        if info is not None and "objects" in info:
            for fingerprint, obj_info in info["objects"].items():
                obj_path = obj_info.get("path")
                obj_name = obj_info.get("name", "Unknown")
                obj_type = obj_info.get("type")

                # Models are recreated as ghosts from cached data below
                if obj_type == "model":
                    continue

                if obj_path is None or not os.path.exists(obj_path):
                    logger.warning(f"Skipping {obj_name}: path not found at {obj_path}")
                    continue

                # Stage 5: session save/load runs SERVER-SIDE (see SAVE_SESSION /
                # LOAD_SESSION). This method therefore executes on the server's
                # Environment, where loading in-process is correct — it owns the
                # real datasets + prediction cache. Datasets load synchronously
                # here; the server's DATASET_LOADED / MODEL_LOADED subscribers
                # announce them to the client (REMOTE_DATASET_META / _MODEL_META).
                if obj_type == "dataset":
                    ase_energy_key = obj_info.get("ase_energy_key")
                    ase_force_key = obj_info.get("ase_force_key")

                    # Reconstruct embedded prediction (energy/force column) keys.
                    prediction_keys = []
                    for ghost_fp, ghost_info in info.get('objects', {}).items():
                        if (ghost_info.get('type') == 'ghost_model' and
                                ghost_info.get('path') == obj_path):
                            prediction_keys.append((
                                ghost_info['energy_key'],
                                ghost_info['force_key'],
                                ghost_info['name'],
                            ))

                    # The saved datasetType may be a dispatch-result subtype
                    # (e.g. "ase (variable)") that isn't a registered loader key —
                    # only "ase (auto)" is — so fall back to it. The auto loader
                    # auto-detects variable vs uniform and applies the saved keys.
                    dataset_type = obj_info.get("datasetType")
                    if not dataset_type or dataset_type not in self.datasetTypes:
                        dataset_type = "ase (auto)"

                    self.loadDataset(
                        obj_path, dataset_type,
                        selected_energy_key=ase_energy_key,
                        selected_force_key=ase_force_key,
                        prediction_keys=prediction_keys or None,
                    )
                    logger.info(
                        "Session restore: loaded %s (%s)", obj_name, dataset_type,
                    )
                else:
                    # Legacy info.json without a proper type: guess ASE by extension.
                    ext = os.path.splitext(obj_path)[1].lower()
                    if ext in ('.xyz', '.extxyz', '.db', '.traj', '.npz'):
                        self.loadDataset(obj_path, "ase (auto)")

        ## LOAD CACHE
        cacheDir = os.path.join(path, "cache")
        for npzPath in glob.glob(os.path.join(cacheDir, "*.npz")):
            d = dict(np.load(npzPath, allow_pickle=True))
            dataTypeKey = str(d.pop("entityDataTypeKey"))
            cacheKey = str(d.pop("cacheKey"))

            # Convert numpy object arrays back to Python lists.
            # Variable-sized data (e.g. forces for molecules with
            # different atom counts) is saved as np.array(list,
            # dtype=object). The rest of the code expects these
            # as Python lists.
            for k, v in d.items():
                if isinstance(v, np.ndarray) and v.dtype == object:
                    d[k] = list(v)

            dataType = self.getDataType(dataTypeKey)

            if dataType is None:
                raise ValueError(
                    f"Tried to load data of type `{dataTypeKey}`, but no such type registered."
                )

            de = dataType.newDataEntity(**d)
            self.cache[cacheKey] = de
            self.eventPush("DATA_UPDATED", cacheKey)

        self.lookForGhosts()

    def loadInfo(self, info):
        """Merge persisted object metadata into the catalog so ghost loaders
        resolve their saved names/paths."""
        self.objects.load(info.get("objects", {}))

    def saveDataset(self, dataset, datasetType, form, path, taskID=None):
        """Export a dataset through its loader-specific serializer."""
        self.eventPush(
            "TASK_PROGRESS",
            taskID,
            message=f"Saving {dataset.getDisplayName()} as {datasetType} dataset at z`{path}`",
            quiet=True,
            percent=False,
        )

        datasetClass = self.datasetTypes.get(datasetType, None)
        if datasetClass is None:
            logger.error(
                f"Tried saving dataset {dataset.getDisplayName()} as {datasetType} dataset, but type is not recognised"
            )
            return

        if not hasattr(datasetClass, "saveDataset"):
            logger.error(
                f"Tried saving dataset {dataset.getDisplayName()} as {datasetType} dataset, but no saveDataset method defined"
            )
            return

        datasetClass.saveDataset(dataset, path, format=form, taskID=taskID)

    def taskSaveDataset(self, dataset, datasetType, form, path):
        """Queue dataset export so serialization does not block other work."""
        self.newTask(
            self.saveDataset,
            args=(dataset, datasetType, form, path),
            visual=True,
            name="Saving dataset",
            threaded=True,
        )
