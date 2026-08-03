"""Loading Coordinator for the Environment (ADR 0034).

``LoadingCoordinator`` is the single owner of turning a *what to load* request
(dataset / model / prediction, plus source path and stride/keys) into the
*where*: a local :class:`TaskManager` task, or a dispatch to ``ffast-server``
over the **Server Connection** (including the connect-window fallback from
ADR 0030).  It also owns the load implementations + validation, the one
ghost-model register/discover chokepoint, and all load-related server transport
(probe round-trips + ``LOAD_*`` dispatch).

Like ``ConnectionManager`` / ``SessionPersistence`` (ADR 0020) it takes the
owning ``Environment`` and reaches domain state through it (``self._env``); the
method bodies are the former ``Environment`` methods, relocated verbatim.  It is
deliberately **Qt-free** so it is the piece that can migrate into the Headless
Core (ADR 0034): the Desktop Client UI keeps the file/key/stride dialogs and
hands the coordinator dialog callbacks for its remote-load orchestration.
"""

import logging
import os

import numpy as np
from ase.calculators.calculator import PropertyNotImplementedError
from ase.io.trajectory import Trajectory

from ffast.core.data_types import AtomsList
from ffast.protocol import control
from ffast.loaders.ghost import GhostModelLoader
from ffast.cache.fingerprint import md5FromArraysAndStrings

logger = logging.getLogger("FFAST")


def _isUniformAtomsList(atomsList, sampleSize=60):
    """Decide whether every frame in ``atomsList`` is the same molecule.

    Picks which ASE loader flavour a prediction needs.  The uniform
    :class:`aseDatasetLoader` reads frame 0's atom count *and* atomic numbers
    and applies them to every frame, so "uniform" has to mean same
    *composition* — comparing atom counts alone would accept frames that share
    a count but not their elements, and silently stamp frame 0's ``z`` onto all
    of them.  Hence chemical formulas.

    Full scans are avoided: ``atomsList`` is routinely a lazy ``AtomsList`` /
    ``Trajectory`` where materialising every frame costs a re-read of the whole
    file.  Lists at or below ``sampleSize`` are checked exhaustively; larger
    ones on an evenly-spaced sample that always includes the first and last
    frame.  The spacing is deterministic on purpose — a random sample can
    classify the same file differently between runs, which changes the loader
    class and therefore the dataset's identity.
    """
    n = len(atomsList)
    if n < 2:
        return True

    if n <= sampleSize:
        indices = range(n)
    else:
        step = (n - 1) / (sampleSize - 1)
        indices = sorted({int(round(i * step)) for i in range(sampleSize)} | {0, n - 1})

    reference = None
    for i in indices:
        formula = atomsList[i].get_chemical_formula()
        if reference is None:
            reference = formula
        elif formula != reference:
            return False
    return True


class LoadingCoordinator:
    """Owns dataset/model/prediction load routing, implementations, and ghosts (ADR 0034)."""

    def __init__(self, env):
        self._env = env

    #############
    ## ROUTING HELPERS
    #############

    def _remoteSession(self):
        """Resolve the active server session + its event loop, or ``(None, None)``.

        The connect-window fallback guard from ADR 0030: a load routes
        server-side only when both a ``serverConnection`` and the client's
        asyncio ``_event_loop`` exist; otherwise the caller falls back to an
        in-process TaskManager task. Delegates to
        :meth:`ConnectionManager.active_session`, the one place this guard
        lives.
        """
        return self._env.remote.active_session()

    def _queueLoad(self, fn, name, args=(), kwargs=None):
        """Queue an in-process load so disk I/O and setup stay off the main loop.

        Every ``taskLoad*`` entry point wants the same task shape — visual,
        threaded, named "Loading <thing>" — so that shape lives here rather than
        being spelled out at each of them.
        """
        return self._env.newTask(
            fn,
            args=args,
            kwargs=kwargs or {},
            visual=True,
            name=name,
            threaded=True,
        )

    def _progress(self, taskID, message, *, error=False):
        """Report one step of a remote load to the Tasks panel.

        The remote-load algorithms narrate themselves at every probe and every
        dialog wait; ``error=False`` matches the default both ``TASK_PROGRESS``
        consumers already apply.
        """
        self._env.eventPush("TASK_PROGRESS", taskID, message=message, error=error)

    def _resolveLoad(self, kind, path, typeName):
        """Validate a load request and resolve the plugin class that serves it.

        Both in-process load paths open with the same two checks — the file is
        there, the plugin type is registered — and both used to report every
        failure as a *dataset* problem, so a missing model file or an
        unrecognised model type logged "Tried to load dataset".  ``kind`` is
        ``"model"`` or ``"dataset"``; returns the loader class, or ``None`` when
        the request is not loadable (already logged).
        """
        if not os.path.exists(path):
            logger.error(f"Tried to load {kind}, but path `{path}` not found")
            return None

        registry = self._env.modelTypes if kind == "model" else self._env.datasetTypes
        loaderClass = registry.get(typeName)
        if loaderClass is None:
            logger.error(
                f"Tried to load {kind}, but {kind} type `{typeName}` not recognised"
            )
        return loaderClass

    #############
    ## MODELS
    #############

    def taskLoadModel(self, path, modelType):
        """Queue model loading so disk I/O and setup do not block the main loop."""
        self._queueLoad(self.loadModel, "Loading model", args=(path, modelType))

    def requestModelLoad(self, path, modelType):
        """Dispatch a model load through the server, or in-process as fallback.

        Stage 2 of server-owned loading: when a session exists the model loads
        *server-side* (it runs predictions there) and the client receives a
        ghost proxy via REMOTE_MODEL_META; the server generates predictions on
        demand.  No-server fallback loads the model in-process.
        """
        session, loop = self._remoteSession()
        if session is None:
            self.taskLoadModel(path, modelType)
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            self.dispatchModelLoad(session, path, modelType),
            loop,
        )

    def loadModel(self, path, modelType, taskID=None):
        """Validate and instantiate a concrete model from disk."""
        loaderClass = self._resolveLoad("model", path, modelType)
        if loaderClass is None:
            return None

        # Instantiating a concrete predicting ModelLoader triggers its heavy ML
        # backend (torch/mace/nequip/...), which is loaded lazily and runs
        # server-side (ADR 0030). Guard it: a missing/broken backend must warn
        # and abort this one load, not crash the server task (local or remote).
        try:
            model = loaderClass(self._env, path)
            if model is None:
                logger.warning(f"Model `{path}` did not load successfully")
                return
            model.initialise()
        except (ImportError, ModuleNotFoundError) as exc:
            logger.error(
                f"Cannot load model `{path}` of type `{modelType}`: its ML "
                f"backend is not available in this ffast-server environment "
                f"({exc}). Install the backend where the server runs "
                f"(local venv or cluster). Predictions require server-side "
                f"inference (ADR 0030)."
            )
            return None
        except Exception as exc:
            logger.error(
                f"Failed to load model `{path}` of type `{modelType}`: {exc}"
            )
            return None

        # Registry mutation serialized against concurrent loads/deletes from
        # other controllers (ADR 0044 Phase 3) — the heavy backend init above
        # runs unlocked.
        with self._env.mutation_lock:
            self._env.models.add(model)
        logging.info(f"Model `{path}` successfully loaded")

    #############
    ## PREDICTIONS
    #############

    def taskLoadPrepredictedDataset(self, path, datasetKey, selected_energy_key=None, selected_force_key=None):
        """Queue import of external predictions as cached model outputs for a dataset."""
        self._queueLoad(
            self.loadPrepredictedDataset,
            "Loading prepredicted dataset",
            args=(path, datasetKey),
            kwargs={
                'selected_energy_key': selected_energy_key,
                'selected_force_key': selected_force_key
            },
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
        session, loop = self._remoteSession()
        if session is None:
            self.taskLoadPrepredictedDataset(
                path, datasetKey,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
            )
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            self.dispatchPredictionLoad(
                session, path, datasetKey,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
            ),
            loop,
        )

    def loadPrepredictedDataset(self, path, datasetKey, taskID=None, selected_energy_key=None, selected_force_key=None):
        """Attach precomputed energies and forces to a dataset as a ghost model."""
        if "npz" in path:
            d = np.load(path, allow_pickle=True)
            E, F = d["E"], d["F"]
            aseObject = None
        else:
            atomsList = self._readPredictionAtomsList(path, datasetKey)
            aseObject = self._aseLoaderFor(
                path, atomsList,
                energy_key=selected_energy_key, force_key=selected_force_key,
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

        # mutation_lock again (see loadModel above) — the file parse above
        # runs unlocked.
        with self._env.mutation_lock:
            dataset = self._env.datasets.get(datasetKey)

            available = [x for x in (E, F) if x is not None]
            modelKey = (
                md5FromArraysAndStrings(*available)
                if available
                else md5FromArraysAndStrings(path)
            )

            name = os.path.basename(path)
            if not self._ingestPrediction(
                dataset, E, F, path=path, name=name, fingerprint=modelKey,
                source=aseObject,
            ):
                # A mismatch here means the whole load fails — unlike the
                # prediction-keys path, there is no other column to fall back
                # to, and the cause is almost always a mis-picked file.
                logger.error(
                    "Prediction load failed, you have probably selected the wrong prediction for the designated dataset. "
                    "Please try again and choose the correct prediction file according to the dataset selected "
                    "in the file filter dropdown."
                )
                return

            self.lookForGhosts()

    def _readPredictionAtomsList(self, path, datasetKey):
        """Read a prediction file's frames at the parent dataset's stride.

        A prediction has to be sampled exactly like the dataset it is being
        attached to, so the stride comes from the dataset registry rather than
        the caller.  ``slice_num == 0`` means "no stride": that is the lazy
        path, where frames are read on demand (``AtomsList``, or ``Trajectory``
        for ``.traj``) instead of held in RAM.
        """
        import ase.io

        slice_num = self._env.datasets.slice_numbers.get(datasetKey)
        if slice_num is not None and slice_num > 0:
            logger.info(f"Loading dataset with slice number of: {slice_num}")
            return ase.io.read(path, index=slice(0, None, slice_num))
        if slice_num is not None and slice_num == 0:
            logger.info("Loading prediction dataset with caching.")
            if path.endswith(".traj"):
                logger.info("Trajectory prediction dataset detected, loading with class ase.io.Trajectory")
                return Trajectory(path)
            return AtomsList(path)
        logger.info("Loading the dataset entirely on RAM.")
        return ase.io.read(path, index=':')

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
            self._env.data.predictionFields[(modelKey, dataset.fingerprint)] = store
            logger.info(
                "Prediction fields: extracted %d frame + %d atom field(s) for %s",
                len(store["info"]), len(store["atoms"]), os.path.basename(dataset.getName()),
            )

    #############
    ## PREDICTION INGEST (one body, two entry points)
    #############

    @staticmethod
    def _aseLoaderFor(path, atomsList, *, energy_key=None, force_key=None, uniform=None):
        """Build the ASE loader flavour ``atomsList``'s homogeneity calls for.

        ``uniform`` lets a caller reuse an already-made decision — it is
        loop-invariant when several prediction columns are read off one
        ``atomsList``, and deciding it walks a sample of the frames.
        """
        from ffast.loaders.ase import aseDatasetLoader, VariableASEDatasetLoader

        if uniform is None:
            uniform = _isUniformAtomsList(atomsList)
        loaderClass = aseDatasetLoader if uniform else VariableASEDatasetLoader
        logger.info(
            "Loading prediction as %s ASE dataset: %d molecules",
            "uniform" if uniform else "variable", len(atomsList),
        )
        return loaderClass(
            path,
            atomsList=atomsList,
            selected_energy_key=energy_key,
            selected_force_key=force_key,
        )

    @staticmethod
    def _predictionMatchesDataset(E, dataset, name):
        """Check a prediction's energies line up with the dataset's, frame for frame.

        Handles both dataset flavours: a variable dataset returns a list of
        per-frame scalars (compare lengths), a uniform one a numpy array
        (compare shapes).  The array-shape branch on its own — all the
        standalone-prediction-file path used to do — raises ``AttributeError``
        against a variable dataset.
        """
        if E is None:
            return True

        dataset_E = dataset.getEnergies()
        if isinstance(E, list) or isinstance(dataset_E, list):
            if len(E) != len(dataset_E):
                logger.error(
                    f"Shape mismatch for prediction '{name}'. "
                    f"Expected {len(dataset_E)} molecules, got {len(E)}."
                )
                return False
        elif hasattr(E, "shape") and hasattr(dataset_E, "shape"):
            if E.shape != dataset_E.shape:
                logger.error(
                    f"Shape mismatch for prediction '{name}'. "
                    f"Expected {dataset_E.shape}, got {E.shape}."
                )
                return False
        return True

    def _ingestPrediction(self, dataset, E, F, *, path, name, fingerprint,
                          source=None, **catalogExtra):
        """Register one prediction's arrays against ``dataset`` as a ghost model.

        The single body behind both prediction entry points — a standalone
        prediction file (:meth:`loadPrepredictedDataset`) and an extra
        energy/force column pair inside the dataset file itself
        (:meth:`_loadPredictionsFromKeys`).  Both validate the arrays against
        the dataset, cache energies, cache forces, extract the declared
        ADR 0023 prediction fields and register the ghost's catalog entry; they
        were written out twice and drifted (ADR 0034 addendum 4).

        ``source`` is the prediction's ASE loader where it has one (``None`` for
        npz, which carries nothing but E/F) and is read for prediction Dataset
        Fields before the caller discards it.  ``fingerprint`` stays the
        caller's to compute — the two paths hash different things, and a
        fingerprint *is* the ghost's identity in the cache and in saved
        sessions.

        Returns ``True`` when the prediction was ingested, ``False`` when its
        arrays do not match the dataset; the caller decides whether that aborts
        the whole load or skips this one column.

        Callers must hold ``mutation_lock``.
        """
        if not self._predictionMatchesDataset(E, dataset, name):
            return False

        if E is not None:
            energyDataType = self._env.data.getDataType("energy")
            self._env.data.setData(
                energyDataType.newDataEntity(energy=np.asarray(E).flatten()),
                "energy", model=fingerprint, dataset=dataset,
            )

        if F is not None:
            forcesDataType = self._env.data.getDataType("forces")
            self._env.data.setData(
                forcesDataType.newDataEntity(forces=F),
                "forces", model=fingerprint, dataset=dataset,
            )

        # Prediction Dataset Fields (ADR 0023): the loader's ASE source is
        # discarded once E/F are pulled, so any declared prediction.{info,
        # atoms}.<key> has to be read now.  Reaching this from the
        # prediction-keys path is new — that path skipped extraction entirely
        # while it carried its own copy of this body, so fields declared by a
        # metric silently resolved to None for in-file prediction columns.
        if source is not None:
            self._extractPredictionFields(source, fingerprint, dataset)

        self.registerGhostModel(fingerprint, path=path, name=name, **catalogExtra)
        return True

    #############
    ## DATASETS
    #############

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
        self._queueLoad(
            self.loadDataset,
            "Loading dataset",
            args=(path, datasetType),
            kwargs={
                'selected_energy_key': selected_energy_key,
                'selected_force_key': selected_force_key,
                'prediction_keys': prediction_keys,
                'slice_num': slice_num
            },
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
        remote menu (``onRemoteDatasetLoad``) reaches the same server-side
        ``LOAD_DATASET`` handler via :meth:`loadRemoteDataset` after its own
        cluster-path/key probing.
        """
        session, loop = self._remoteSession()
        if session is None:
            self.taskLoadDataset(
                path, datasetType,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
                prediction_keys=prediction_keys,
                slice_num=slice_num,
            )
            return

        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            self.dispatchDatasetLoad(
                session, path, datasetType,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
                prediction_keys=prediction_keys,
                slice_num=slice_num,
            ),
            loop,
        )

    def loadDataset(self, path, datasetType, taskID=None, selected_energy_key=None,
                   selected_force_key=None, prediction_keys=None, slice_num=0):
        """Load dataset and create ghost models for prediction keys."""
        loaderClass = self._resolveLoad("dataset", path, datasetType)
        if loaderClass is None:
            return None

        # Load dataset - pass selected keys to ASE loader
        if datasetType == "ase (auto)":
            try:
                result = loaderClass(
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
            result = loaderClass(path)

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

        # NOTE: in-process load is now the no-server FALLBACK only (routing goes
        # through requestDatasetLoad → server-side LOAD_DATASET when a session
        # exists).  No mirror-to-local-server here: if we reached this method a
        # server was unavailable, so there is nothing to mirror to.

        # Resolve the prediction-keys atomsList BEFORE the lock: a cache miss
        # here (dataset has no .atomsList) falls through to a full re-read of
        # the file inside _loadPredictionsFromKeys. That must not happen while
        # holding mutation_lock — deleteObject (ADR 0044 Phase 3) acquires the
        # same lock synchronously on the event loop thread (no to_thread), so
        # a slow read held under the lock would freeze the whole event loop —
        # every connection's dispatch — for its duration, not just serialize
        # the registry mutation.
        atomsList = dataset.atomsList if hasattr(dataset, 'atomsList') else None
        if prediction_keys and atomsList is None:
            import ase.io
            atomsList = ase.io.read(path, index=":")

        # mutation_lock again (see loadModel above).
        with self._env.mutation_lock:
            self._env.datasets.add(dataset, slice_num)
            logging.info(f"Dataset `{path}` successfully loaded")

            # Load predictions as ghost models if specified
            if prediction_keys:
                self._loadPredictionsFromKeys(dataset, path, prediction_keys, atomsList=atomsList)

            self.lookForGhosts()

    def _loadPredictionsFromKeys(self, dataset, path, prediction_keys, atomsList=None):
        """Materialize extra energy/force columns as ghost-model cache entries.

        One ghost model per ``(energy_key, force_key, model_name)`` triple, each
        ingested through the shared :meth:`_ingestPrediction` body — so these
        in-file prediction columns now get the same ADR 0023 prediction-field
        extraction the standalone-prediction-file path always did (ADR 0034
        addendum 4).  A column whose arrays do not line up with the dataset, or
        whose loader raises, is skipped; the remaining columns still load.

        Args:
            dataset: The loaded dataset
            path: Path to the file
            prediction_keys: List of (energy_key, force_key, model_name) tuples
            atomsList: Optional pre-loaded atoms list to avoid re-reading file
        """
        # Read file only if not provided
        if atomsList is None:
            import ase.io
            atomsList = ase.io.read(path, index=":")

        # Loop-invariant: every column is read off this one atomsList.
        is_uniform = _isUniformAtomsList(atomsList)

        for energy_key, force_key, model_name in prediction_keys:
            try:
                temp_loader = self._aseLoaderFor(
                    path, atomsList,
                    energy_key=energy_key, force_key=force_key,
                    uniform=is_uniform,
                )

                E = temp_loader.getEnergies()
                F = temp_loader.getForces()
                ghost_fp = md5FromArraysAndStrings(E, F, model_name)

                if not self._ingestPrediction(
                    dataset, E, F, path=path, name=model_name,
                    fingerprint=ghost_fp, source=temp_loader,
                    energy_key=energy_key, force_key=force_key,
                ):
                    continue

                logger.info(f"Loaded predictions for '{model_name}' from keys {energy_key}/{force_key}")

            except Exception as e:
                logger.error(f"Failed to load predictions for '{model_name}': {e}")
                import traceback
                logger.debug(traceback.format_exc())
                continue

    #############
    ## SERVER TRANSPORT (async, Qt-free)
    #############

    async def dispatchModelLoad(self, session, path, model_type):
        """Send the ``LOAD_MODEL`` control message to the server."""
        await session.push_event(control.LOAD_MODEL, path, model_type)

    async def dispatchPredictionLoad(self, session, path, dataset_fp, *,
                                     selected_energy_key=None, selected_force_key=None):
        """Send the ``LOAD_PREDICTION`` control message to the server.

        Unused (``None``) key kwargs are harmless — the server handler reads them
        with ``.get()`` — so NPZ (fixed E/F keys) and the probe-error fallback
        share this one path with the key-selected ASE case.
        """
        await session.push_event(
            control.LOAD_PREDICTION, path, dataset_fp,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
        )

    async def probeDatasetLength(self, session, path):
        """Count frames in a server-side dataset file (Dataset Length Probe)."""
        return await session.probe_dataset_length(path)

    async def probeDatasetKeys(self, session, path, dataset_type):
        """Probe available energy/force keys of a server-side ASE file."""
        return await session.probe_dataset_keys(path, dataset_type)

    async def dispatchDatasetLoad(self, session, path, dataset_type, *,
                                  selected_energy_key=None, selected_force_key=None,
                                  prediction_keys=None, slice_num=0):
        """Send the ``LOAD_DATASET`` control message to the server.

        Single owner of the dataset-load wire contract: the ``control.*``
        constant and the ``prediction_keys`` tuple→list coercion (msgpack cannot
        carry tuples).  Awaited directly by :meth:`loadRemoteDataset` (already on
        the loop) and scheduled via ``run_coroutine_threadsafe`` by
        :meth:`requestDatasetLoad` (off the loop).
        """
        pk = [list(k) for k in prediction_keys] if prediction_keys else None
        await session.push_event(
            control.LOAD_DATASET, path, dataset_type,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            prediction_keys=pk,
            slice_num=slice_num,
        )

    #############
    ## REMOTE ORCHESTRATION (async, Qt-free; dialogs come in as callbacks)
    #############

    async def loadRemoteDataset(self, session, path, dataset_type, *,
                                get_stride, get_keys, taskID=None):
        """Drive a cluster-side dataset load: probe → stride → probe → keys → dispatch.

        Owns the load *algorithm* while staying Qt-free: every user interaction
        is an awaited callback the Desktop Client supplies (ADR 0034).
        ``get_stride(n_total) -> slice_num | None`` and
        ``get_keys(probe) -> (energy_key, force_key, prediction_keys) | None``
        return fully-cooked values (``None`` == the user cancelled); the delicate
        ``QDialog.exec()`` bridging lives inside them, in the UI.
        """
        self._progress(taskID, "Probing dataset length on server…")
        n_total = None
        try:
            length_result = await self.probeDatasetLength(session, path)
            if not length_result.get("error"):
                n_total = length_result.get("n")
        except Exception as exc:
            logger.warning("Length probe failed (non-fatal): %s", exc)

        self._progress(taskID, "Waiting for stride selection…")
        slice_num = await get_stride(n_total)
        if slice_num is None:
            logger.info("Remote dataset loading cancelled by user")
            return
        logger.info(
            "Requesting remote load: path=%s type=%s slice_num=%d",
            path, dataset_type, slice_num,
        )

        if dataset_type != "ase (auto)":
            await self.dispatchDatasetLoad(
                session, path, dataset_type, slice_num=slice_num
            )
            return

        self._progress(taskID, "Probing dataset keys on server…")
        try:
            probe = await self.probeDatasetKeys(session, path, dataset_type)
        except Exception as exc:
            logger.error("Key probe failed: %s", exc)
            self._progress(taskID, f"Key probe failed: {exc}", error=True)
            return

        if probe.get("error"):
            logger.warning("Server probe error for %r: %s", path, probe["error"])
            # Fall back: load without explicit key selection.
            await self.dispatchDatasetLoad(
                session, path, dataset_type, slice_num=slice_num
            )
            return

        self._progress(taskID, "Waiting for key selection…")
        selection = await get_keys(probe)
        if selection is None:
            logger.info("Remote dataset loading cancelled by user")
            return
        selected_energy_key, selected_force_key, prediction_keys = selection

        logger.info(
            "Remote LOAD_DATASET: energy_key=%r force_key=%r prediction_keys=%r",
            selected_energy_key, selected_force_key, prediction_keys,
        )
        await self.dispatchDatasetLoad(
            session, path, dataset_type,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            prediction_keys=prediction_keys,
            slice_num=slice_num,
        )

    async def loadRemotePrediction(self, session, path, dataset_fp, *,
                                   get_keys, taskID=None):
        """Drive a cluster-side prediction load against an already-loaded dataset.

        NPZ files carry fixed ``E``/``F`` keys, so they dispatch immediately with
        no probe or dialog.  ASE files probe keys server-side, then ask the
        UI-supplied ``get_keys(probe) -> (energy_key, force_key) | None`` callback
        (``None`` == cancelled).  On a probe error, falls back to a keyless load.
        The server materializes a ghost model and announces it via
        ``REMOTE_MODEL_META`` (→ :meth:`registerGhostModel` + ghost discovery).
        """
        if path.lower().endswith(".npz"):
            await self.dispatchPredictionLoad(session, path, dataset_fp)
            return

        self._progress(taskID, "Probing prediction file keys on server…")
        try:
            probe = await self.probeDatasetKeys(session, path, "ase (auto)")
        except Exception as exc:
            logger.error("Key probe failed: %s", exc)
            self._progress(taskID, f"Key probe failed: {exc}", error=True)
            return

        if probe.get("error"):
            # Fall back: load without explicit key selection.
            await self.dispatchPredictionLoad(session, path, dataset_fp)
            return

        self._progress(taskID, "Waiting for key selection…")
        selection = await get_keys(probe)
        if selection is None:
            logger.info("Remote prediction load cancelled by user")
            return
        selected_energy_key, selected_force_key = selection

        logger.info(
            "Remote LOAD_PREDICTION: path=%r dataset=%r energy_key=%r force_key=%r",
            path, dataset_fp[:8], selected_energy_key, selected_force_key,
        )
        await self.dispatchPredictionLoad(
            session, path, dataset_fp,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
        )

    #############
    ## GHOSTS
    #############

    def registerGhostModel(self, fingerprint, path, name, **extra):
        """Record a ghost model's metadata in the Object Catalog (ADR 0034).

        Single chokepoint for the ``{"path","name","type":"ghost_model", ...}``
        registration that was copy-pasted across the load paths and the
        ConnectionManager's server→client metadata handlers.  ``initialise()`` on
        a :class:`GhostModelLoader` reads this back to recover its display name.
        """
        self._env.objects.register(fingerprint, {
            "path": path,
            "name": name,
            "type": "ghost_model",
            **extra,
        })

    def instantiateGhost(self, modelKey):
        """Construct, initialise, and register a :class:`GhostModelLoader`.

        The other half of the ghost-model chokepoint alongside
        :meth:`registerGhostModel`: both ``lookForGhosts`` and the
        ConnectionManager's ``REMOTE_MODEL_META`` handler create a ghost model
        the same way — this is that one body.
        """
        model = GhostModelLoader(self._env, modelKey)
        model.initialise()
        self._env.models.add(model)
        return model

    def lookForGhosts(self):
        """Recreate ghost model placeholders for cached prediction-only data.

        The zero baseline is intentionally NOT loaded here. Recovering ghost
        predictions must not pull in a model the user never requested — the zero
        model loads only on explicit request (File ▸ Load Zero Model / Ctrl+0).
        """
        from ffast.cache import CacheKey
        for cacheKey in list(self._env.cache.keys()):
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
                    and (modelKey not in self._env.models)
                    and self._env.datasets.exists(datasetKey)
            ):
                self.instantiateGhost(modelKey)
