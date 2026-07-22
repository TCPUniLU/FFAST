import ase.io.formats
from events import EventClass
from datasetLoaders.loader import (
    SubDataset,
    FrozenSubDataset,
    AtomFilteredDataset,
)
from modelLoaders.zeroModel import ZeroModelLoader
from tasks import TaskManager
from client.dataType import DataEntity
from client.dataType import SubDataEntity
from client.data_cache import DataCache
from client.model_registry import ModelRegistry
from client.dataset_registry import DatasetRegistry
from client.data_service import DataService
from client.connection_manager import ConnectionManager
from client.session_persistence import SessionPersistence
from client.object_catalog import ObjectCatalog
from client.loading_coordinator import LoadingCoordinator
from ffast.protocol import control
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
        # Serializes dataset/model registry mutations across the threaded load
        # tasks (ADR 0044 Phase 3): two controllers loading/deleting at once
        # would otherwise run their loadDataset/loadModel bodies concurrently
        # on separate worker threads (``asyncio.to_thread``), racing on
        # self.datasets/self.models/self.cache. A coarse per-Environment lock
        # orders them instead — correctness over the (small, I/O-already-
        # dominated) loss of true load parallelism.
        self.mutation_lock = threading.Lock()
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

        # Loading Coordinator (ADR 0034): single owner of dataset/model/prediction
        # load routing (local task vs server dispatch), the load implementations,
        # and the ghost register/discover chokepoint. Built after remote/data/
        # persistence since it reaches all of them through this Environment.
        self.loading = LoadingCoordinator(self)

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
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.taskLoadModel(path, modelType)

    def requestModelLoad(self, path, modelType):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.requestModelLoad(path, modelType)

    def loadModel(self, path, modelType, taskID=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.loadModel(path, modelType, taskID=taskID)

    def taskLoadPrepredictedDataset(self, path, datasetKey, selected_energy_key=None, selected_force_key=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.taskLoadPrepredictedDataset(
            path, datasetKey,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
        )

    def requestPredictionLoad(self, path, datasetKey, selected_energy_key=None,
                              selected_force_key=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.requestPredictionLoad(
            path, datasetKey,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
        )

    def loadPrepredictedDataset(self, path, datasetKey, taskID=None, selected_energy_key=None, selected_force_key=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.loadPrepredictedDataset(
            path, datasetKey, taskID=taskID,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
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
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.taskLoadDataset(
            path, datasetType,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            prediction_keys=prediction_keys,
            slice_num=slice_num,
        )

    def requestDatasetLoad(self, path, datasetType, selected_energy_key=None,
                           selected_force_key=None, prediction_keys=None,
                           slice_num=0):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.requestDatasetLoad(
            path, datasetType,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            prediction_keys=prediction_keys,
            slice_num=slice_num,
        )

    def loadDataset(self, path, datasetType, taskID=None, selected_energy_key=None,
                   selected_force_key=None, prediction_keys=None, slice_num=0):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.loadDataset(
            path, datasetType, taskID=taskID,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            prediction_keys=prediction_keys,
            slice_num=slice_num,
        )

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
        with self.mutation_lock:
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
                session.push_event(control.DELETE_OBJECT, key),
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
            session.push_event(control.SAVE_SESSION, path), self.remote._event_loop
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
            session.push_event(control.LOAD_SESSION, path), self.remote._event_loop
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
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.lookForGhosts()

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
