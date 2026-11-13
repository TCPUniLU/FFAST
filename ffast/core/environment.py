import ase.io.formats
from ffast.core.events import EventClass
from ffast.core.tasks import TaskManager
from ffast.core.work_gate import WorkGate
from ffast.cache.store import DataCache
from ffast.core.model_registry import ModelRegistry
from ffast.core.dataset_registry import DatasetRegistry
from ffast.core.data_service import DataService
from ffast.core.connection_manager import ConnectionManager
from ffast.session.persistence import SessionPersistence
from ffast.core.object_catalog import ObjectCatalog
from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.protocol import control
import logging
import os, glob
import numpy as np
import asyncio
import json
import threading, time
import warnings

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
        # Prediction fields (ADR 0023): one store, written by prediction loads
        # and released by both registries when either side is deleted.
        from ffast.core.prediction_fields import PredictionFieldStore
        predictionFields = PredictionFieldStore()
        self.models = ModelRegistry(self.cache, self, predictionFields)
        self.datasets = DatasetRegistry(self.cache, self, predictionFields)
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
            prediction_fields=predictionFields,
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


    def taskLoadModel(self, path, modelType, on_error=None, on_done=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.taskLoadModel(path, modelType, on_error=on_error, on_done=on_done)

    def requestModelLoad(self, path, modelType):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.requestModelLoad(path, modelType)

    def loadModel(self, path, modelType, taskID=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.loadModel(path, modelType, taskID=taskID)

    def taskLoadPrepredictedDataset(self, path, datasetKey, selected_energy_key=None,
                                    selected_force_key=None, on_error=None, reader=None,
                                    on_done=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.taskLoadPrepredictedDataset(
            path, datasetKey,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            on_error=on_error,
            reader=reader,
            on_done=on_done,
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
                       selected_force_key=None, prediction_keys=None, slice_num=0,
                       on_error=None, sampling=None, on_done=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.taskLoadDataset(
            path, datasetType,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            prediction_keys=prediction_keys,
            slice_num=slice_num,
            on_error=on_error,
            sampling=sampling,
            on_done=on_done,
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
                   selected_force_key=None, prediction_keys=None, slice_num=0,
                   sampling=None, frames=None):
        """Delegate to the Loading Coordinator (ADR 0034)."""
        return self.loading.loadDataset(
            path, datasetType, taskID=taskID,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            prediction_keys=prediction_keys,
            slice_num=slice_num,
            sampling=sampling,
            frames=frames,
        )

    def declareSubDataset(self, parent, model, idx, subName):
        """Create or refresh a logical subset view over a parent dataset."""
        from ffast.loaders.dataset import DatasetView  # ADR 0047 Phase 5c

        # The server builds the same subset, so its 3D view can draw it. Told
        # first: moving the subset below makes the 3D view ask the server for
        # it, and the server must have the new frames by then. With no frames
        # the server keeps the ones it had, so there is nothing to say.
        if idx is not None and len(idx):
            self.remote.tellServer(
                control.DECLARE_SUBSET,
                parent_fingerprint=parent.fingerprint,
                indices=[int(i) for i in idx],
                model_fp=None if model is None else model.fingerprint,
                name=subName,
            )

        # check if already exists
        fp = DatasetView.subsetFingerprint(parent, model, subName)
        sub = self.datasets.get(fp)

        # if doesnt exist yet
        if sub is None:  # and (idx is not None):
            sub = DatasetView.subset(parent, model, idx, subName)
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
        """Keep a live subset's current frames as a frozen subset of their own.
        Only a subset that follows a zoom can be frozen; anything else is left
        alone."""
        from ffast.loaders.dataset import DatasetView  # ADR 0047 Phase 5c
        dataset = self.datasets.get(fingerprint)
        if not getattr(dataset, "live", False):
            return

        fp = DatasetView.frozenFingerprint(
            dataset.parent, dataset.model, dataset.subName, dataset.frames
        )
        if self.datasets.get(fp) is not None:
            return

        sub = DatasetView.frozenSubset(
            dataset.parent, dataset.model, dataset.frames, dataset.subName
        )
        sub.initialise()
        self.datasets.add(sub)
        self.remote.tellServer(control.FREEZE_SUBSET, fingerprint=fingerprint)

    def createAtomFilteredDataset(self, dataset, idxs):
        """Build a per-atom filtered dataset view for atom-level analyses."""
        from ffast.loaders.dataset import DatasetView  # ADR 0047 Phase 5c
        # Sorted, as the server sorts them: the same atoms, the same fingerprint.
        idxs = sorted(int(i) for i in idxs)
        fp = DatasetView.atomSubsetFingerprint(dataset, idxs)
        sub = self.datasets.get(fp)

        if sub is not None:
            return

        sub = DatasetView.atomSubset(dataset, idxs)
        sub.initialise()
        self.datasets.add(sub)
        self.remote.tellServer(
            control.CREATE_SUBSET, parent_fingerprint=dataset.fingerprint, indices=idxs
        )

    def createElementSubset(self, dataset, elements):
        """The atoms of these atomic numbers in every frame of ``dataset``
        (ADR 0067); the one already made when the same elements were kept."""
        from ffast.loaders.dataset import DatasetView  # ADR 0047 Phase 5c
        sub = self.datasets.get(DatasetView.elementSubsetFingerprint(dataset, elements))
        if sub is None:
            sub = DatasetView.elementSubset(dataset, elements)
            sub.initialise()
            self.datasets.add(sub)
        return sub

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

    def deletedWith(self, key):
        """What deleting ``key`` deletes: ``key``; for a dataset, every loaded
        dataset cut from it, at any depth, and every prediction read from a
        file whose numbers are only for those datasets."""
        dataset = self.datasets.get(key)
        if dataset is None:
            return [key]
        gone = [key, *self.datasets.cutFrom(dataset)]
        return gone + self.predictionsOnlyFor(gone)

    def predictionsOnlyFor(self, datasetKeys):
        """Predictions read from a file (ghost models) that hold numbers only
        for ``datasetKeys``. Their numbers go when those datasets go, and a
        file's numbers cannot be moved to another dataset, so nothing would be
        left of them. A model that computes predictions is never listed: it can
        predict any dataset."""
        from ffast.cache import CacheKey
        ghosts = {fp for fp, model in self.models.items() if getattr(model, "isGhost", False)}
        if not ghosts:
            return []
        datasetsOf = {fp: set() for fp in ghosts}
        for cacheKey in self.cache.keys():
            ck = CacheKey.try_parse(cacheKey)
            if ck is not None and ck.model_fp in datasetsOf and ck.dataset_fp:
                datasetsOf[ck.model_fp].add(ck.dataset_fp)
        gone = set(datasetKeys)
        return [fp for fp, fps in datasetsOf.items() if fps and fps <= gone]

    def storedMetricResults(self, key):
        """The metric results deleting ``key`` with ``delete_results`` would
        delete: count, bytes in memory, bytes on disk."""
        return self.data.storedMetricResults(self.deletedWith(key))

    def deleteObject(self, key, delete_results=False):
        """Route generic delete requests to the appropriate registry.

        ``delete_results``: also delete, from memory and disk, the computed
        metric results only this object (and its subsets) used. Otherwise they
        stay in the metric cache, and a reload of the same data reuses them."""
        with self.mutation_lock:
            gone = self.deletedWith(key)   # before the delete clears the cache it reads
            if self.datasets.exists(key):
                self.datasets.delete(key)
                for fp in gone:            # predictions left with no numbers
                    if fp in self.models:
                        self.models.delete(fp)
            elif self.models.exists(key):
                self.models.delete(key)
            else:
                return
            if delete_results:
                freed = self.data.forgetMetricResults(gone)
                logger.info("Deleted %d metric result(s) of %s: %d bytes in memory, %d on disk",
                            freed["count"], key, freed["memory_bytes"], freed["disk_bytes"])

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

    def taskLoadZeroModel(self, on_error=None, on_done=None):
        """Queue loading of the built-in zero baseline model. ``on_error`` /
        ``on_done`` as for the other loads (LoadingCoordinator._queueLoad)."""
        load = self.loadZeroModel

        def run(taskID=None):
            try:
                load(taskID=taskID)
            except Exception as exc:
                if on_error is not None:
                    on_error(exc)
                    exc.ffast_reported = True
                raise
            if on_done is not None:
                on_done()

        self.newTask(
            run,
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
        # Scripts have no event loop to react in; waitForTasks waits on this
        # gate, which the loop marks once per iteration (ADR 0094).
        self._workGate = WorkGate()

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
            # Checked after everything above has run, so it sees the settled state.
            self._workGate.mark_iteration(self._loopIsIdle())
            await asyncio.sleep(0.1)

        await self.eventHandle()
        await taskManager.eventHandle()
        self._workGate.mark_iteration(self._loopIsIdle())

    def _loopIsIdle(self) -> bool:
        """True when no task, generation or queued event is outstanding.

        Cheap on purpose (``len``/``qsize`` only): the server runs it too.
        """
        tm = self.tm
        return not (
            tm.taskQueue.qsize()
            or len(tm.runningTasks)
            or len(self.data.generationQueue)
            or len(self.eventQueue)
            or len(tm.eventQueue)
            or any(len(child.eventQueue) for child in list(self.eventChildren))
        )

    def headlessQuit(self):
        """Signal the headless loop to stop cleanly."""
        self.quitReady = True
        self._workGate.wake()

    def _workFingerprint(self):
        """Snapshot of work state; changes whenever anything moves forward."""
        tm = self.tm
        # Copy before reading: the loop thread inserts and deletes entries.
        # Sort by str(taskID): remote tasks carry string IDs, local ones ints.
        running = tuple(
            sorted(
                (str(taskID), task["progress"], task["progressMessage"])
                for taskID, task in list(tm.runningTasks.items())
            )
        )
        return (tm.taskQueue.qsize(), running, len(self.data.generationQueue))

    def _describePendingWork(self) -> str:
        tm = self.tm
        running = list(tm.runningTasks.values())
        parts = []
        if running:
            names = ", ".join(str(task["name"]) for task in running)
            parts.append(f"{len(running)} task(s) running: {names}")
        if tm.taskQueue.qsize():
            parts.append(f"{tm.taskQueue.qsize()} task(s) queued")
        if len(self.data.generationQueue):
            parts.append(f"{len(self.data.generationQueue)} metric(s) to generate")
        return "; ".join(parts) if parts else "nothing"

    def waitForTasks(self, verbose=False, dt=None, stall_timeout_s=None):
        """Block until queued, running and deferred work has settled.

        Returns as soon as the loop reports an idle iteration, or once
        ``headlessQuit()`` was called.

        ``stall_timeout_s`` (off by default): raise TimeoutError naming the
        outstanding work once nothing has progressed for that many seconds. A
        task that keeps reporting progress never trips it.

        ``dt`` is ignored and only kept so old scripts still run.
        """
        if dt is not None:
            warnings.warn(
                "waitForTasks(dt=...) is ignored: it no longer polls.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._workGate.wait(
            should_stop=lambda: self.quitReady,
            on_tick=self._printProgress if verbose else None,
            stall_timeout_s=stall_timeout_s,
            fingerprint=self._workFingerprint,
            describe=self._describePendingWork,
        )

    def _printProgress(self) -> None:
        tm = self.tm
        print("-" * 20)
        lTaskQueue = tm.taskQueue.qsize()
        if lTaskQueue > 0:
            print(f"{lTaskQueue} tasks queued.\n")

        running = list(tm.runningTasks.values())  # copy: the loop thread mutates it
        if running:
            print(f"{len(running)} tasks running:")
            for task in running:
                prog = "?%"
                if task["progress"] is not None:
                    prog = f'{task["progress"] * 100:.0f}%'
                print(f'{prog:<4} {task["name"]:<20}  {task["progressMessage"]}')
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

    loadModules(thread)
    thread.start()

    return thread
