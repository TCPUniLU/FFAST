import ase.io.formats
from ffast.core.events import EventClass
from ffast.core.tasks import TaskManager
from ffast.cache.store import DataCache
from ffast.core.model_registry import ModelRegistry
from ffast.core.dataset_registry import DatasetRegistry
from ffast.core.data_service import DataService
from ffast.core.connection_manager import ConnectionManager
from ffast.session.persistence import SessionPersistence
from ffast.core.object_catalog import ObjectCatalog
from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.core.work_gate import WorkGate
from ffast.protocol import control
import logging
import os, glob
import numpy as np
import asyncio
import json
import threading

# NOTE (ADR 0047 Phase 4): the Desktop-Client loaders this module uses
# (datasetLoaders.loader SubDataset/FrozenSubDataset/AtomFilteredDataset,
# modelLoaders.zeroModel.ZeroModelLoader) are imported LAZILY inside the
# methods that use them, so importing ffast.core.environment stays flat-free
# at module load (a headless install does not need them until a
# subset/zero-model/subclass code path actually runs). Plugin discovery
# (loadModules) moved into ffast.core.plugin_discovery and the color helper
# (mixColors) is imported from its relocated ffast.core.util home (ADR 0048);
# this module no longer imports anything from flat utils.

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
        from ffast.core.prediction_source import RemoteSource
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

    def _registerPluginType(self, registry, name, value, kind):
        """Choke point shared by initialiseModelType/initialiseDatasetType.

        Raises if ``name`` is already in ``registry`` (ADR 0048): duplicate
        plugin names are an error, not a shadow, regardless of which of the
        three discovery roots (bundled/entry-point/``modules/``) found them.
        """
        if name in registry:
            raise ValueError(f"{kind} '{name}' is already registered")
        registry[name] = value

    #############
    ## MODELS
    #############

    def initialiseModelType(self, modelType):
        """Register a model loader class discovered during module loading."""
        self._registerPluginType(
            self.modelTypes, modelType.modelName, modelType, "Model loader"
        )


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
        self._registerPluginType(
            self.datasetTypes, datasetType.datasetName, datasetType, "Dataset loader"
        )


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
        from ffast.loaders.dataset import SubDataset  # ADR 0047 Phase 5c

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
        from ffast.loaders.dataset import FrozenSubDataset  # ADR 0047 Phase 5c
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
        from ffast.loaders.dataset import AtomFilteredDataset  # ADR 0047 Phase 5c
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

        session, loop = self.remote.active_session()
        if session is not None:
            import asyncio as _asyncio
            _asyncio.run_coroutine_threadsafe(
                session.push_event(control.DELETE_OBJECT, key),
                loop,
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
        session, loop = self.remote.active_session()
        if session is None:
            self.newTask(
                self.persistence.save, args=(path,), visual=True,
                name="Saving session", threaded=True,
            )
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            session.push_event(control.SAVE_SESSION, path), loop
        )

    def requestSessionLoad(self, path):
        """Load the session SERVER-SIDE; the server restores its Environment
        (datasets + prediction cache) and announces them to the client via
        REMOTE_DATASET_META / REMOTE_MODEL_META. Falls back to in-process load.
        """
        session, loop = self.remote.active_session()
        if session is None:
            self.persistence.taskLoad(path)
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            session.push_event(control.LOAD_SESSION, path), loop
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
            from ffast.core.util import mixColors
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
        from ffast.loaders.zero import ZeroModelLoader  # ADR 0047 Phase 5b
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

        # Scripts have no event loop to react in, so waitForTasks waits on this
        # gate instead.  headlessEventLoop signals it once an iteration ends with
        # nothing outstanding — see there for why that, and not a TASK_DONE
        # subscription, is what wakes the waiter.
        self._workGate = WorkGate(
            self._hasPendingWork,
            fingerprint=self._workFingerprint,
            describe=self._describePendingWork,
        )

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

            # Wake anyone in waitForTasks.  Subscribing to TASK_DONE instead
            # does not work: every EventClass drains its own queue, and this
            # loop drains the environment's before the TaskManager's, so the
            # finished task is still in runningTasks when the handler runs and
            # the waiter goes straight back to sleep.  Checking here — after
            # both queues have been drained — sees the settled state, and it
            # also covers completion paths that raise no event at all, such as
            # a generation queue served straight from cache.  Costs at most one
            # iteration of latency, once, at the end of a wait.
            if not self._hasPendingWork():
                self._workGate.notify()

            await asyncio.sleep(0.1)

        await self.eventHandle()
        await taskManager.eventHandle()

    def headlessQuit(self):
        """Signal the headless loop to stop cleanly."""
        self.quitReady = True

    def _hasPendingWork(self) -> bool:
        """True while anything is queued, running, or waiting to be generated."""
        if self.quitReady:
            return False
        tm = self.tm
        return bool(
            tm.taskQueue.qsize()
            or len(tm.runningTasks)
            or len(self.data.generationQueue)
        )

    def _workFingerprint(self):
        """Snapshot of work state; changes whenever anything moves forward.

        Includes each running task's progress and message, so a single long task
        that reports progress reads as alive rather than stalled.
        """
        tm = self.tm
        # Copy before reading: the loop thread inserts and deletes entries.
        # Sort by str(taskID) — remote tasks carry string IDs (phantom tasks),
        # local ones ints, and the two do not compare.
        running = tuple(
            sorted(
                (str(taskID), task["progress"], task["progressMessage"])
                for taskID, task in list(tm.runningTasks.items())
            )
        )
        return (tm.taskQueue.qsize(), running, len(self.data.generationQueue))

    def _describePendingWork(self) -> str:
        tm = self.tm
        parts = []
        if tm.runningTasks:
            names = ", ".join(
                str(task["name"]) for task in list(tm.runningTasks.values())
            )
            parts.append(f"{len(tm.runningTasks)} task(s) running: {names}")
        if tm.taskQueue.qsize():
            parts.append(f"{tm.taskQueue.qsize()} task(s) queued")
        if self.data.generationQueue:
            parts.append(f"{len(self.data.generationQueue)} metric(s) to generate")
        return "; ".join(parts) if parts else "nothing"

    def waitForTasks(self, verbose=False, stall_timeout_s=None):
        """Block scripted callers until queued, running, and deferred work has settled.

        Returns as soon as the work finishes rather than on a poll interval; the
        gate's watchdog only decides how often ``verbose`` reprints progress.

        ``stall_timeout_s`` bounds a script against work that never completes.
        It measures progress, not elapsed time: a load that keeps reporting runs
        as long as it needs, while work that stops moving for that many seconds
        raises TimeoutError naming what is still outstanding.

        Progress is only visible for work that publishes TASK_PROGRESS.  Loads
        narrate themselves (per file, per batch of frames); metric computation
        does NOT — a metric is opaque between the task starting and its result
        arriving, so a single metric slower than the window reads as stalled.
        Set the window above the slowest metric you expect, or leave it unset
        (the default) and rely on the worker pool's own per-metric hard limit
        (PoolPolicy.max_runtime_s) to bound a genuinely stuck computation.
        """
        self._workGate.wait(
            on_tick=self._printProgress if verbose else None,
            stall_timeout_s=stall_timeout_s,
        )

    def _printProgress(self) -> None:
        tm = self.tm
        print("-" * 20)
        lTaskQueue = tm.taskQueue.qsize()
        if lTaskQueue > 0:
            print(f"{lTaskQueue} tasks queued.\n")

        running = list(tm.runningTasks.items())  # Copy: the loop thread mutates it
        if running:
            print(f"{len(running)} tasks running:")
            for taskID, task in running:
                prog = "?%"
                if task is not None and task["progress"] is not None:
                    prog = f'{task["progress"] * 100:.0f}%'

                name = task["name"] if task is not None else "?"
                message = task["progressMessage"] if task is not None else ""
                print(f'{prog:<4} {name:<20}  {message}')
            print()

        generating = list(self.data.generationQueue)
        if generating:
            print(f"{len(generating)} tasks in generation queue:")
            for i in generating:
                print(i)

        print(flush=True)


def startHeadlessEnvironment():
    """Bootstrap modules, logging, and the headless event thread for scripts."""
    from ffast.core.plugin_discovery import loadModules

    thread = HeadlessEnvironment()
    # Minimal stdlib logging config for CLI/script use — deliberately not
    # utils.setupLogger (Desktop-only: anchors debug.log next to the flat
    # repo root, which a real headless install has no business writing into).
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", force=True)

    loadModules(None, thread, headless=True)
    thread.start()

    return thread
