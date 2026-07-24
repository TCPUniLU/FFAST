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

from client.dataType import AtomsList
from ffast.protocol import control
from modelLoaders.ghost import GhostModelLoader
from utils import md5FromArraysAndStrings

logger = logging.getLogger("FFAST")


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

    #############
    ## MODELS
    #############

    def taskLoadModel(self, path, modelType):
        """Queue model loading so disk I/O and setup do not block the main loop."""
        self._env.newTask(
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
        if not os.path.exists(path):
            logger.error(f"Tried to load dataset, but path `{path}` not found")
            return None

        if modelType not in self._env.modelTypes:
            logger.error(
                f"Tried to load dataset, but dataset type {modelType} not recognised"
            )
            return None

        # Instantiating a concrete predicting ModelLoader triggers its heavy ML
        # backend (torch/mace/nequip/...), which is loaded lazily and runs
        # server-side (ADR 0030). Guard it: a missing/broken backend must warn
        # and abort this one load, not crash the server task (local or remote).
        try:
            model = self._env.modelTypes[modelType](self._env, path)
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
        self._env.newTask(
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
            slice_num = self._env.datasets.slice_numbers.get(datasetKey)
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

        # mutation_lock again (see loadModel above) — the file parse above
        # runs unlocked.
        with self._env.mutation_lock:
            dataset = self._env.datasets.get(datasetKey)

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
                energyDataType = self._env.data.getDataType("energy")
                energyDataEntity = energyDataType.newDataEntity(energy=E.flatten())
                self._env.data.setData(
                    energyDataEntity, "energy", model=modelKey, dataset=dataset
                )

            if F is not None:
                forcesDataType = self._env.data.getDataType("forces")
                forcesDataEntity = forcesDataType.newDataEntity(forces=F)
                self._env.data.setData(
                    forcesDataEntity, "forces", model=modelKey, dataset=dataset
                )

            # Prediction Dataset Fields (ADR 0023): eagerly extract the declared set
            # of prediction.{info,atoms}.<key> from the prediction's loader (its ASE
            # source is discarded below). Only ASE files carry extra keys; npz holds
            # only E/F, so 'aseObject' is absent there and this is skipped.
            if "npz" not in path:
                self._extractPredictionFields(aseObject, modelKey, dataset)

            # Register this ghost model's info so it survives session save/restore.
            self.registerGhostModel(modelKey, path=path, name=os.path.basename(path))

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
            self._env.data.predictionFields[(modelKey, dataset.fingerprint)] = store
            logger.info(
                "Prediction fields: extracted %d frame + %d atom field(s) for %s",
                len(store["info"]), len(store["atoms"]), os.path.basename(dataset.getName()),
            )

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
        self._env.newTask(
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
        #logger.info(f"self.datasetTypes:\n{self.datasetTypes}\narg datasetType:\n{datasetType}")
        if not os.path.exists(path):
            logger.error(f"Tried to load dataset, but path `{path}` not found")
            return None

        if datasetType not in self._env.datasetTypes:  # This if statement seems to be useless
            logger.error(
                f"Tried to load dataset, but dataset type {datasetType} not recognised"
            )
            return None

        # Load dataset - pass selected keys to ASE loader
        if datasetType == "ase (auto)":
            try:
                result = self._env.datasetTypes[datasetType](
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
            result = self._env.datasetTypes[datasetType](path)

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
                energy_dt = self._env.data.getDataType("energy")
                if isinstance(E, list):
                    # Variable dataset - E is list of scalars, need to convert to array
                    import numpy as np
                    E_array = np.array(E)
                    energy_de = energy_dt.newDataEntity(energy=E_array.flatten())
                else:
                    energy_de = energy_dt.newDataEntity(energy=E.flatten())
                self._env.data.setData(energy_de, "energy", model=ghost_fp, dataset=dataset)

                forces_dt = self._env.data.getDataType("forces")
                forces_de = forces_dt.newDataEntity(forces=F)
                self._env.data.setData(forces_de, "forces", model=ghost_fp, dataset=dataset)

                # Register ghost model info
                self.registerGhostModel(
                    ghost_fp, path=path, name=model_name,
                    energy_key=energy_key, force_key=force_key,
                )

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
        self._env.eventPush(
            "TASK_PROGRESS", taskID, message="Probing dataset length on server…"
        )
        n_total = None
        try:
            length_result = await self.probeDatasetLength(session, path)
            if not length_result.get("error"):
                n_total = length_result.get("n")
        except Exception as exc:
            logger.warning("Length probe failed (non-fatal): %s", exc)

        self._env.eventPush(
            "TASK_PROGRESS", taskID, message="Waiting for stride selection…"
        )
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

        self._env.eventPush(
            "TASK_PROGRESS", taskID, message="Probing dataset keys on server…"
        )
        try:
            probe = await self.probeDatasetKeys(session, path, dataset_type)
        except Exception as exc:
            logger.error("Key probe failed: %s", exc)
            self._env.eventPush(
                "TASK_PROGRESS", taskID,
                message=f"Key probe failed: {exc}", error=True,
            )
            return

        if probe.get("error"):
            logger.warning("Server probe error for %r: %s", path, probe["error"])
            # Fall back: load without explicit key selection.
            await self.dispatchDatasetLoad(
                session, path, dataset_type, slice_num=slice_num
            )
            return

        self._env.eventPush(
            "TASK_PROGRESS", taskID, message="Waiting for key selection…"
        )
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

        self._env.eventPush(
            "TASK_PROGRESS", taskID,
            message="Probing prediction file keys on server…",
        )
        try:
            probe = await self.probeDatasetKeys(session, path, "ase (auto)")
        except Exception as exc:
            logger.error("Key probe failed: %s", exc)
            self._env.eventPush(
                "TASK_PROGRESS", taskID,
                message=f"Key probe failed: {exc}", error=True,
            )
            return

        if probe.get("error"):
            # Fall back: load without explicit key selection.
            await self.dispatchPredictionLoad(session, path, dataset_fp)
            return

        self._env.eventPush(
            "TASK_PROGRESS", taskID, message="Waiting for key selection…"
        )
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
