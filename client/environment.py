import ase.io.formats
from ase.calculators.calculator import PropertyNotImplementedError
from ase.io.trajectory import Trajectory
from events import EventClass
from datasetLoaders.loader import (
    SubDataset,
    FrozenSubDataset,
    AtomFilteredDataset,
)
from modelLoaders.ghost import GhostModelLoader
from modelLoaders.zeroModel import ZeroModelLoader
from tasks import TaskManager
from client.dataType import DataEntity, AtomsList
from utils import md5FromArraysAndStrings
from client.dataType import SubDataEntity
from client.data_cache import DataCache
from client.model_registry import ModelRegistry
from client.dataset_registry import DatasetRegistry
from client.data_service import DataService
from client.connection_manager import ConnectionManager
from client.session_persistence import SessionPersistence
from client.object_catalog import ObjectCatalog
import logging
import os, glob
import numpy as np
import asyncio
from utils import loadModules, mixColors
import json
import threading, time
from modules.loaders.aseDataset import aseDatasetLoader

logger = logging.getLogger("FFAST")


class Environment(EventClass):
    """
    Environment class, responsible for loading and handling all datasets,
    models and saving intermediate and final data where necessary (e.g.
    model predictions, extra dataset descriptors).
    """

    def __init__(self, headless=True):
        """Build the session registries, caches, and event wiring for one environment."""
        super().__init__()
        self.headless = headless

        if headless:
            self.quitReady = False

        # Note: might have multiple environments at some point
        self.cache = DataCache()
        self.models = ModelRegistry(self.cache, self)
        self.datasets = DatasetRegistry(self.cache, self)
        self.modelTypes = {}
        self.datasetTypes = {}
        # Single owner of per-object session metadata (path/name/type). Was a
        # raw ``self.info['objects']`` dict mutated across 6 files; now behind
        # ObjectCatalog's register/prune/get/snapshot/load interface.
        self.objects = ObjectCatalog()
        self.tm = TaskManager()

        # Remote/local server session manager (ADR 0020): owns the connection
        # lifecycle, server→client metadata handlers, and array/metric fetch
        # channels.  Created before DataService so the RemoteSource can wrap it.
        self.remote = ConnectionManager(self)

        # Data coordinator (ADR 0020): owns the datatype registry, cache-key
        # resolution, the data-generation queue, and the in-process metric spine.
        # The RemoteSource degrades to in-process when no session exists, so the
        # same wiring works on the client, the server, and headless.
        from client.prediction_source import RemoteSource
        self.data = DataService(
            cache=self.cache,
            models=self.models,
            datasets=self.datasets,
            tm=self.tm,
            events=self,
            source=RemoteSource(self.remote),
            headless=headless,
        )

        # Session save/load + dataset export (ADR 0020).
        self.persistence = SessionPersistence(self)

        # self.eventSubscribe("DATA_UPDATED", self.handleGenerationQueue)
        # self.eventSubscribe("GENERATION_QUEUE_CHANGED", self.handleGenerationQueue)
        self.eventSubscribe("TASK_CANCEL", self.onTaskCancel)
        self.eventSubscribe("TASK_FAILED", self.onTaskFailed)
        self.eventSubscribe("TASK_DONE", self.onTaskDone)
        self.eventSubscribe(
            "SUBDATASET_INDICES_CHANGED", self.data.deleteCacheByDataset
        )
        self.eventSubscribe(
            "QUIT_EVENT", self.remote._disconnectServerConnection, asynchronous=True
        )
        # Server→client metadata: handlers live on the ConnectionManager and
        # build proxy datasets / ghost models when the server announces them.
        self.eventSubscribe("REMOTE_DATASET_META", self.remote._onRemoteDatasetMeta)
        self.eventSubscribe("REMOTE_MODEL_META", self.remote._onRemoteModelMeta)
        self.eventSubscribe("METRIC_CATALOG", self.remote._onMetricCatalog)

    # Datatypes, the cache/generation/metric coordinator (self.data), the
    # registries (self.models / self.datasets), the session manager (self.remote)
    # and persistence (self.persistence) are composed in __init__ (ADR 0020).
    # Callers reach them directly, e.g. env.models.get(fp), env.data.getData(...),
    # env.remote.serverConnection.

    #############
    ## MODELS
    #############

    def initialiseModelType(self, modelType):
        """Register a model loader class discovered during module loading."""
        self.modelTypes[modelType.modelName] = modelType


    def getModelFromPath(self, path):
        """Resolve a loaded model through its source path."""
        return self.models.get(self.getKeyFromPath(path))


    def taskLoadModel(self, path, modelType):
        """Queue model loading so disk I/O and setup do not block the main loop."""
        self.newTask(
            self.loadModel,
            args=(path, modelType),
            visual=True,
            name="Loading model",
            threaded=True,
        )

    def requestModelLoad(self, path, modelType):
        """Dispatch a model load through the server, or in-process as fallback.

        Stage 2 of server-owned loading: when a session exists the model loads
        *server-side* (it runs predictions there) and the client receives a
        ghost proxy via REMOTE_MODEL_META; the server generates predictions on
        demand.  No-server fallback loads the model in-process.
        """
        session = self.remote.serverConnection
        if session is None or self.remote._event_loop is None:
            self.taskLoadModel(path, modelType)
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            session.push_event("LOAD_MODEL", path, modelType),
            self.remote._event_loop,
        )

    def loadModel(self, path, modelType, taskID=None):
        """Validate and instantiate a concrete model from disk."""
        if not os.path.exists(path):
            logger.error(f"Tried to load dataset, but path `{path}` not found")
            return None

        if modelType not in self.modelTypes:
            logger.error(
                f"Tried to load dataset, but dataset type {modelType} not recognised"
            )
            return None

        model = self.modelTypes[modelType](self, path)
        if model is None:
            logging.warn(f"Model `{path}` did not load successfully")
            return
        model.initialise()

        self.models.add(model)
        logging.info(f"Model `{path}` successfully loaded")

    def taskLoadPrepredictedDataset(self, path, datasetKey, selected_energy_key=None, selected_force_key=None):
        """Queue import of external predictions as cached model outputs for a dataset."""
        self.newTask(
            self.loadPrepredictedDataset,
            args=(path, datasetKey),
            kwargs={
                'selected_energy_key': selected_energy_key,
                'selected_force_key': selected_force_key
            },
            visual=True,
            name="Loading prepredicted dataset",
            threaded=True,
        )

    def requestPredictionLoad(self, path, datasetKey, selected_energy_key=None,
                              selected_force_key=None):
        """Dispatch a prediction-file load through the server, or in-process (Stage 3).

        When a session exists the prediction file is loaded *server-side* against
        the server's dataset; the server materializes a ghost model and announces
        it via REMOTE_MODEL_META, and its prediction arrays stay server-side
        (consumed by the server-owned metric channel — Stage 4a). Only when no
        server is reachable does the client fall back to the in-process
        :meth:`loadPrepredictedDataset`.
        """
        session = self.remote.serverConnection
        if session is None or self.remote._event_loop is None:
            self.taskLoadPrepredictedDataset(
                path, datasetKey,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
            )
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            session.push_event(
                "LOAD_PREDICTION", path, datasetKey,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
            ),
            self.remote._event_loop,
        )

    def loadPrepredictedDataset(self, path, datasetKey, taskID=None, selected_energy_key=None, selected_force_key=None):
        """Attach precomputed energies and forces to a dataset as a ghost model."""
        if "npz" in path:
            d = np.load(path, allow_pickle=True)
            E, F = d["E"], d["F"]
        else:
            # Use smart loader to detect uniform vs variable datasets
            import ase.io
            from modules.loaders.aseDataset import aseDatasetLoader, VariableASEDatasetLoader
            def check_homogeneity(atoms_list):
                for i in range(20):
                    temp_atoms_list = []
                    for j in np.random.choice(len(atoms_list), size=3, replace=False):
                        temp_atoms_list.append(atoms_list[j].get_chemical_formula())
                    if len(set(temp_atoms_list)) != 1:
                        return False

                return True

            # Read once to detect type
            slice_num = self.datasets.slice_numbers.get(datasetKey)
            if slice_num is not None and slice_num > 0:
                logger.info(f"Loading dataset with slice number of: {slice_num}")
                atomsList = ase.io.read(path, index=slice(0, None, slice_num))
            elif slice_num is not None and slice_num == 0:
                logger.info("Loading prediction dataset with caching.")
                if path.endswith(".traj"):
                    logger.info("Trajectory prediction dataset detected, loading with class ase.io.Trajectory")
                    atomsList = Trajectory(path)
                else:
                    atomsList = AtomsList(path)
            else:
                logger.info("Loading the dataset entirely on RAM.")
                atomsList = ase.io.read(path, index=':')

            # atom_counts = [len(atoms) for atoms in atomsList] --> inefficient for large datasets because it
            # literally creates a copy of the entire dataset on RAM, just to check whether the dataset is variable or
            # fixed. Instead, the following probabilistic method:
            fixed_or_variable = check_homogeneity(atomsList)
            if fixed_or_variable:
                # Uniform dataset
                logger.info(
                    f"Loading prepredicted data as uniform ASE dataset: {len(atomsList)} molecules, {len(atomsList[0])} atoms each")
                aseObject = aseDatasetLoader(
                    path,
                    atomsList=atomsList,
                    selected_energy_key=selected_energy_key,
                    selected_force_key=selected_force_key
                )
            else:
                # Variable dataset
                logger.info(f"Loading prepredicted data as variable ASE dataset: {len(atomsList)} molecules")
                aseObject = VariableASEDatasetLoader(
                    path,
                    atomsList=atomsList,
                    selected_energy_key=selected_energy_key,
                    selected_force_key=selected_force_key
                )

            try:
                E = aseObject.getEnergies()
            except (PropertyNotImplementedError, RuntimeError):
                logger.warning(
                    "Energy not available in prediction file. "
                    "Loading forces only."
                )
                E = None
            try:
                F = aseObject.getForces()
            except (PropertyNotImplementedError, RuntimeError):
                logger.warning(
                    "Forces not available in prediction file. "
                    "Loading energies only."
                )
                F = None

        dataset = self.datasets.get(datasetKey)

        if E is not None:
            eDataset = dataset.getEnergies()
            if E.shape != eDataset.shape:
                logger.error(
                    f"Shape mismatch when loading prepredicted model. Model energy shape: {E.shape}, dataset energy shape: {eDataset.shape}"
                )
                logger.error(
                    "Prediction load failed, you have probably selected the wrong prediction for the designated dataset. "
                    "Please try again and choose the correct prediction file according to the dataset selected "
                    "in the file filter dropdown."
                )
                return

        available = [x for x in (E, F) if x is not None]
        modelKey = (
            md5FromArraysAndStrings(*available)
            if available
            else md5FromArraysAndStrings(path)
        )

        if E is not None:
            energyDataType = self.data.getDataType("energy")
            energyDataEntity = energyDataType.newDataEntity(energy=E.flatten())
            self.data.setData(
                energyDataEntity, "energy", model=modelKey, dataset=dataset
            )

        if F is not None:
            forcesDataType = self.data.getDataType("forces")
            forcesDataEntity = forcesDataType.newDataEntity(forces=F)
            self.data.setData(
                forcesDataEntity, "forces", model=modelKey, dataset=dataset
            )

        # Prediction Dataset Fields (ADR 0023): eagerly extract the declared set
        # of prediction.{info,atoms}.<key> from the prediction's loader (its ASE
        # source is discarded below). Only ASE files carry extra keys; npz holds
        # only E/F, so 'aseObject' is absent there and this is skipped.
        if "npz" not in path:
            self._extractPredictionFields(aseObject, modelKey, dataset)

        # Register this ghost model's info so it survives session save/restore.
        self.objects.register(modelKey, {
            "path": path,
            "name": os.path.basename(path),
            "type": "ghost_model",
        })

        self.lookForGhosts()

        # NOTE: in-process prediction load is now the no-server FALLBACK only
        # (routing goes through requestPredictionLoad → server-side
        # LOAD_PREDICTION when a session exists). No mirror here: reaching this
        # method means no server was available, so there is nothing to mirror to.

    def _extractPredictionFields(self, aseObject, modelKey, dataset):
        """Eagerly extract the declared prediction Dataset Fields (ADR 0023).

        The prediction's ASE loader is discarded once E/F are pulled, so any
        prediction.{info,atoms}.<key> a registered metric declares must be read
        now and stashed in ``DataService.predictionFields`` keyed by
        ``(modelKey, dataset_fp)`` (modelKey == the GhostModel fingerprint, so
        the resolver finds it from the model object). Strict per-field: an
        unavailable key just stays absent (resolves to None later).
        """
        try:
            from ffast.metrics.fields import declared_field_keys
            wanted = declared_field_keys("prediction")
        except Exception:
            logger.exception("Prediction fields: failed to read declared keys")
            return
        if not wanted["info"] and not wanted["atoms"]:
            return
        store = {"info": {}, "atoms": {}}
        for key in wanted["info"]:
            v = aseObject.getFrameField(key)
            if v is not None:
                store["info"][key] = v
        for key in wanted["atoms"]:
            v = aseObject.getAtomField(key)
            if v is not None:
                store["atoms"][key] = v
        if store["info"] or store["atoms"]:
            self.data.predictionFields[(modelKey, dataset.fingerprint)] = store
            logger.info(
                "Prediction fields: extracted %d frame + %d atom field(s) for %s",
                len(store["info"]), len(store["atoms"]), os.path.basename(dataset.getName()),
            )

    #############
    ## DATASETS
    #############

    def initialiseDatasetType(self, datasetType):
        """Register a dataset loader class discovered during module loading."""
        self.datasetTypes[datasetType.datasetName] = datasetType


    def getDatasetFromPath(self, path):
        """Resolve a loaded dataset through its source path."""
        return self.datasets.get(self.getKeyFromPath(path))


    def taskLoadDataset(self, path, datasetType, selected_energy_key=None,
                       selected_force_key=None, prediction_keys=None, slice_num=0):
        """Load dataset and optionally load predictions from same file.

        Args:
            path: Path to dataset file
            datasetType: Type of dataset loader to use
            selected_energy_key: Pre-selected energy key for reference (ASE only)
            selected_force_key: Pre-selected force key for reference (ASE only)
            prediction_keys: List of (energy_key, force_key, model_name) tuples
            :param slice_num: slicing number for sampled load of datasets.
        """
        self.newTask(
            self.loadDataset,
            args=(path, datasetType),
            kwargs={
                'selected_energy_key': selected_energy_key,
                'selected_force_key': selected_force_key,
                'prediction_keys': prediction_keys,
                'slice_num': slice_num
            },
            visual=True,
            name="Loading dataset",
            threaded=True,
        )

    def requestDatasetLoad(self, path, datasetType, selected_energy_key=None,
                           selected_force_key=None, prediction_keys=None,
                           slice_num=0):
        """Dispatch a dataset load through the server, or in-process as fallback.

        Stage 1 of the server-owned-loading migration.  When a session exists
        (managed local server or remote cluster) the file is loaded *server-side*
        and the client receives a ``CachedRemoteDataset`` proxy via
        ``REMOTE_DATASET_META`` (then eager-populated for a local server — see
        :meth:`_onRemoteDatasetMeta`).  Only when no server is reachable does the
        client fall back to the legacy in-process :meth:`loadDataset`.

        Shared entry point: the local File→Load Dataset menu routes here; the
        remote menu (``onRemoteDatasetLoad``) hits the same server-side
        ``LOAD_DATASET`` handler after its own cluster-path/key probing.
        """
        session = self.remote.serverConnection
        if session is None or self.remote._event_loop is None:
            self.taskLoadDataset(
                path, datasetType,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
                prediction_keys=prediction_keys,
                slice_num=slice_num,
            )
            return

        import asyncio as _asyncio
        # msgpack can't carry tuples; send prediction_keys as plain lists.
        pk = [list(k) for k in prediction_keys] if prediction_keys else None
        _asyncio.run_coroutine_threadsafe(
            session.push_event(
                "LOAD_DATASET", path, datasetType,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
                prediction_keys=pk,
                slice_num=slice_num,
            ),
            self.remote._event_loop,
        )

    def loadDataset(self, path, datasetType, taskID=None, selected_energy_key=None,
                   selected_force_key=None, prediction_keys=None, slice_num=0):
        """Load dataset and create ghost models for prediction keys."""
        #logger.info(f"self.datasetTypes:\n{self.datasetTypes}\narg datasetType:\n{datasetType}")
        if not os.path.exists(path):
            logger.error(f"Tried to load dataset, but path `{path}` not found")
            return None

        if datasetType not in self.datasetTypes:  # This if statement seems to be useless
            logger.error(
                f"Tried to load dataset, but dataset type {datasetType} not recognised"
            )
            return None

        # Load dataset - pass selected keys to ASE loader
        if datasetType == "ase (auto)":
            try:
                result = self.datasetTypes[datasetType](
                    path,
                    selected_energy_key=selected_energy_key,
                    selected_force_key=selected_force_key,
                    prediction_keys=prediction_keys,
                    show_dialog=False,  # Dialog already shown on main thread
                    slice_num=slice_num
                )
            except Exception as e:
                logger.error(f"Failed to load dataset {path} in method 'loadDataset'")
                return None
        else:
            result = self.datasetTypes[datasetType](path)

        # Handle SmartASELoader return value (tuple) or regular loader (dataset object)
        if isinstance(result, tuple):
            dataset, pred_keys = result
            if dataset is None:
                logger.info("Dataset loading cancelled by user")
                return

            # Merge prediction keys from dialog with any passed in.
            # SmartASELoader echoes the caller's prediction_keys unchanged
            # when show_dialog=False, so naïve concatenation would double
            # every entry.  Deduplicate to prevent loading predictions twice.
            if pred_keys:
                seen = {tuple(k) for k in (prediction_keys or [])}
                extra = [k for k in pred_keys if tuple(k) not in seen]
                prediction_keys = (prediction_keys or []) + extra
        else:
            dataset = result

        if dataset is None:
            logging.warn(f"Dataset `{path}` did not load successfully")
            return

        dataset.initialise()
        self.datasets.add(dataset, slice_num)
        logging.info(f"Dataset `{path}` successfully loaded")

        # NOTE: in-process load is now the no-server FALLBACK only (routing goes
        # through requestDatasetLoad → server-side LOAD_DATASET when a session
        # exists).  No mirror-to-local-server here: if we reached this method a
        # server was unavailable, so there is nothing to mirror to.

        # Load predictions as ghost models if specified
        if prediction_keys:
            # Reuse atomsList from dataset to avoid re-reading file
            atomsList = dataset.atomsList if hasattr(dataset, 'atomsList') else None
            self._loadPredictionsFromKeys(dataset, path, prediction_keys, atomsList=atomsList)

        self.lookForGhosts()

    def _loadPredictionsFromKeys(self, dataset, path, prediction_keys, atomsList=None):
        """Materialize extra energy/force columns as ghost-model cache entries.

        Args:
            dataset: The loaded dataset
            path: Path to the file
            prediction_keys: List of (energy_key, force_key, model_name) tuples
            atomsList: Optional pre-loaded atoms list to avoid re-reading file
        """
        from modules.loaders.aseDataset import aseDatasetLoader, VariableASEDatasetLoader
        import ase.io
        from utils import md5FromArraysAndStrings

        # Read file only if not provided
        if atomsList is None:
            atomsList = ase.io.read(path, index=":")

        atom_counts = [len(atoms) for atoms in atomsList]
        is_uniform = len(set(atom_counts)) == 1

        for energy_key, force_key, model_name in prediction_keys:
            try:
                # Create temporary loader with selected keys and pre-loaded atomsList
                if is_uniform:
                    temp_loader = aseDatasetLoader(
                        path,
                        atomsList=atomsList,
                        selected_energy_key=energy_key,
                        selected_force_key=force_key
                    )
                else:
                    temp_loader = VariableASEDatasetLoader(
                        path,
                        atomsList=atomsList,
                        selected_energy_key=energy_key,
                        selected_force_key=force_key
                    )

                # Extract predictions
                E = temp_loader.getEnergies()
                F = temp_loader.getForces()

                # Verify shape matches dataset
                dataset_E = dataset.getEnergies()
                if isinstance(E, list) and isinstance(dataset_E, list):
                    # Variable dataset - check list lengths
                    if len(E) != len(dataset_E):
                        logger.error(
                            f"Shape mismatch for prediction '{model_name}'. "
                            f"Expected {len(dataset_E)} molecules, got {len(E)}. Skipping."
                        )
                        continue
                elif hasattr(E, 'shape') and hasattr(dataset_E, 'shape'):
                    # Uniform dataset - check array shapes
                    if E.shape != dataset_E.shape:
                        logger.error(
                            f"Shape mismatch for prediction '{model_name}'. "
                            f"Expected {dataset_E.shape}, got {E.shape}. Skipping."
                        )
                        continue

                # Create ghost model fingerprint
                ghost_fp = md5FromArraysAndStrings(E, F, model_name)

                # Cache predictions
                energy_dt = self.data.getDataType("energy")
                if isinstance(E, list):
                    # Variable dataset - E is list of scalars, need to convert to array
                    import numpy as np
                    E_array = np.array(E)
                    energy_de = energy_dt.newDataEntity(energy=E_array.flatten())
                else:
                    energy_de = energy_dt.newDataEntity(energy=E.flatten())
                self.data.setData(energy_de, "energy", model=ghost_fp, dataset=dataset)

                forces_dt = self.data.getDataType("forces")
                forces_de = forces_dt.newDataEntity(forces=F)
                self.data.setData(forces_de, "forces", model=ghost_fp, dataset=dataset)

                # Register ghost model info
                self.objects.register(ghost_fp, {
                    'path': path,
                    'name': model_name,
                    'type': 'ghost_model',
                    'energy_key': energy_key,
                    'force_key': force_key
                })

                logger.info(f"Loaded predictions for '{model_name}' from keys {energy_key}/{force_key}")

            except Exception as e:
                logger.error(f"Failed to load predictions for '{model_name}': {e}")
                import traceback
                logger.debug(traceback.format_exc())
                continue

    def declareSubDataset(self, parent, model, idx, subName):
        """Create or refresh a logical subset view over a parent dataset."""

        # check if already exists
        fp = SubDataset.getFingerprint(SubDataset, parent, model, subName)
        sub = self.datasets.get(fp)

        # if doesnt exist yet
        if sub is None:  # and (idx is not None):
            sub = SubDataset(parent, model, idx, subName)
            sub.initialise()
            self.datasets.add(sub)
        # elif sub is None:
        #     pass
        # elif idx is None:
        #     sub.setActive(False)
        else:
            sub.setIndices(idx)
            sub.setActive(True)

    def freezeSubDataset(self, fingerprint):
        """Persist the current subdataset selection as its own frozen dataset object."""
        dataset = self.datasets.get(fingerprint)
        if (dataset is None) or (not dataset.isSubDataset):
            return

        fp = FrozenSubDataset.getFingerprint(
            FrozenSubDataset,
            parent=dataset.parent,
            model=dataset.modelDep,
            indices=dataset.indices,
            subName=dataset.subName,
        )
        if self.datasets.get(fp) is not None:
            return

        sub = FrozenSubDataset(
            dataset.parent, dataset.modelDep, dataset.indices, dataset.subName
        )
        sub.initialise()
        self.datasets.add(sub)

    def createAtomFilteredDataset(self, dataset, idxs):
        """Build a per-atom filtered dataset view for atom-level analyses."""
        fp = AtomFilteredDataset.getFingerprint(
            AtomFilteredDataset, dataset, idxs
        )
        sub = self.datasets.get(fp)

        if sub is not None:
            return

        sub = AtomFilteredDataset(dataset, idxs)
        sub.initialise()
        self.datasets.add(sub)

    #############
    ## OBJECTS (MODELS & DATASETS)
    #############

    def getModelOrDataset(self, key):
        """Resolve an object key without the caller needing to know its type."""
        model = self.models.get(key)
        if model is None:
            return self.datasets.get(key)
        else:
            return model

    def getObject(self, *args):
        """Keep a short alias for generic object lookup call sites."""
        return self.getModelOrDataset(*args)

    def getKeyFromPath(self, path):
        """Map a known filesystem path back to the loaded object fingerprint."""
        # check dataset
        for dataset in self.datasets.all(excludeSubs=True):
            if dataset.path == path:
                return dataset.fingerprint

        for model in self.models.all():
            if model.path == path:
                return model.fingerprint

        return None

    def deleteObject(self, key):
        """Route generic delete requests to the appropriate registry."""
        if self.datasets.exists(key):
            self.datasets.delete(key)
        elif self.models.exists(key):
            self.models.delete(key)
        else:
            return

        session = self.remote.serverConnection
        if session is not None and self.remote._event_loop is not None:
            import asyncio as _asyncio
            _asyncio.run_coroutine_threadsafe(
                session.push_event("DELETE_OBJECT", key),
                self.remote._event_loop,
            )

    #############
    ## TASKS
    #############

    def newTask(self, *args, **kwargs):
        """Send environment work through the shared task queue."""
        return self.tm.queueTask(*args, **kwargs)

    def getTask(self, *args, **kwargs):
        """Expose task state for progress displays and cancellation logic."""
        return self.tm.getTask(*args, **kwargs)

    def onTaskCancel(self, taskID):
        """Keep parent generation requests consistent when a child task is cancelled."""
        task = self.tm.getTask(taskID)
        if task is None:
            return

        if task["componentParent"] is not None:
            queue = self.data.generationQueue
            cacheKey = task["componentParent"]

            if cacheKey in queue:
                queue.discard(cacheKey)

            logger.info(
                f"Removed {cacheKey} from data generation queue because child task got cancelled."
            )

    def onTaskFailed(self, taskID):
        """Reuse cancellation cleanup for failed tasks as well."""
        self.onTaskCancel(taskID)

    def onTaskDone(self, taskID):
        """Clear queue bookkeeping once a scheduled task has finished."""
        if taskID in self.data.queuedTasks:
            self.data.queuedTasks.remove(taskID)

        # if the task was also in the generation queue, that means it crashed
        #  gotta remove it then
        if taskID in self.data.generationQueue:
            self.data.generationQueue.discard(taskID)

    #############
    ## SAVE/LOAD
    #############

    def requestSessionSave(self, path):
        """Save the session SERVER-SIDE so the server's datasets + prediction
        cache (the real data) are persisted (Stage 5). Falls back to an
        in-process save when no server is connected.
        """
        session = self.remote.serverConnection
        if session is None or self.remote._event_loop is None:
            self.newTask(
                self.persistence.save, args=(path,), visual=True,
                name="Saving session", threaded=True,
            )
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            session.push_event("SAVE_SESSION", path), self.remote._event_loop
        )

    def requestSessionLoad(self, path):
        """Load the session SERVER-SIDE; the server restores its Environment
        (datasets + prediction cache) and announces them to the client via
        REMOTE_DATASET_META / REMOTE_MODEL_META. Falls back to in-process load.
        """
        session = self.remote.serverConnection
        if session is None or self.remote._event_loop is None:
            self.persistence.taskLoad(path)
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            session.push_event("LOAD_SESSION", path), self.remote._event_loop
        )


    #############
    ## MISC
    #############

    def getColorMix(self, dataset=None, model=None):
        """Provide a stable display color for combined model/dataset views."""
        if dataset is None and model is None:
            return (255, 255, 255)
        elif dataset is None:
            return model.color
        elif model is None:
            return dataset.color
        else:
            return mixColors(model.color, dataset.color)

    def lookForGhosts(self):
        """Recreate ghost model placeholders for cached prediction-only data.

        The zero baseline is intentionally NOT loaded here. Recovering ghost
        predictions must not pull in a model the user never requested — the zero
        model loads only on explicit request (File ▸ Load Zero Model / Ctrl+0).
        """
        from ffast.cache import CacheKey
        for cacheKey in list(self.cache.keys()):
            # Only raw prediction-data keys (forces/energy) carry ghost models.
            # The dtype discriminator is sound regardless of segment count, so
            # metric-result keys (identity = a metric id, not energy/forces) are
            # skipped — they derive from a prediction key recovered here.
            ck = CacheKey.try_parse(cacheKey)
            if ck is None or ck.dtype not in ("forces", "energy"):
                continue
            modelKey, datasetKey = ck.model_fp, ck.dataset_fp
            if (
                    modelKey is not None
                    and datasetKey is not None
                    and (modelKey not in self.models)
                    and self.datasets.exists(datasetKey)
            ):
                model = GhostModelLoader(self, modelKey)
                model.initialise()
                self.models.add(model)

    def taskLoadZeroModel(self):
        """Queue loading of the built-in zero baseline model."""
        self.newTask(
            self.loadZeroModel,
            args=(),
            visual=True,
            name="Loading model",
            threaded=True,
        )

    def loadZeroModel(self, taskID=None):
        """Ensure the singleton zero baseline model exists in the session."""
        fp = ZeroModelLoader.fingerprint
        if self.models.exists(fp):
            return
        model = ZeroModelLoader(self)
        model.initialise()
        self.models.add(model)

    def startInteract(self, **kwargs):
        """Drop into a REPL seeded with useful locals for manual debugging."""
        import code

        code.interact(local=kwargs)

    # ── remote session (facade; owned by self.remote — ADR 0020) ────────


class HeadlessEnvironment(Environment, threading.Thread):
    def __init__(self):
        """Combine environment state with a dedicated thread for headless execution."""
        Environment.__init__(self, headless=True)
        threading.Thread.__init__(self)
        self.loop = None

    def run(self):
        """Own the asyncio event loop inside the headless worker thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.headlessEventLoop())

    async def headlessEventLoop(self):
        """Drive events, generation, and task execution until shutdown is requested."""
        taskManager = self.tm
        while not self.quitReady:
            # One iteration's error (a bad queue key, a task failure) must never
            # tear down the loop — on the server this loop IS the process, so an
            # unhandled exception here kills the server and drops every client
            # ("no close frame"). Log and keep serving.
            try:
                await self.eventHandle()
                await self.data.handleGenerationQueue()
                await taskManager.eventHandle()
                await taskManager.handleTaskQueue()
            except Exception:
                logger.exception("headlessEventLoop: iteration error (continuing)")
            await asyncio.sleep(0.1)

        await self.eventHandle()
        await taskManager.eventHandle()

    def headlessQuit(self):
        """Signal the headless loop to stop cleanly."""
        self.quitReady = True

    def waitForTasks(self, verbose=False, dt=5):
        """Block scripted callers until queued, running, and deferred work has settled."""
        tm = self.tm
        while (
                (tm.taskQueue.qsize() > 0)
                or (len(tm.runningTasks) > 0)
                or (len(self.data.generationQueue) > 0)
        ) and not self.quitReady:
            if verbose:
                print("-" * 20)
                lTaskQueue = tm.taskQueue.qsize()
                if lTaskQueue > 0:
                    print(f"{lTaskQueue} tasks queued.\n")

                lRunningTasks = len(tm.runningTasks)
                if lRunningTasks > 0:
                    print(f"{lRunningTasks} tasks running:")
                    for taskID in tm.runningTasks:
                        task = tm.getTask(taskID)
                        prog = "?%"
                        if task["progress"] is not None:
                            prog = f'{task["progress"] * 100:.0f}%'

                        print(
                            f'{prog:<4} {task["name"]:<20}  {task["progressMessage"]}'
                        )
                    print()

                lGenQueue = len(self.data.generationQueue)
                if lGenQueue > 0:
                    print(f"{lGenQueue} tasks in generation queue:")
                    for i in self.data.generationQueue:
                        print(i)

                print(flush=True)

            time.sleep(dt)


def startHeadlessEnvironment():
    """Bootstrap modules, logging, and the headless event thread for scripts."""
    from utils import setupLogger

    thread = HeadlessEnvironment()
    setupLogger()

    loadModules(None, thread, headless=True)
    thread.start()

    return thread
