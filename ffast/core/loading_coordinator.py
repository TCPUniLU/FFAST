"""Loading Coordinator for the Environment (ADR 0034).

``LoadingCoordinator`` is the single owner of turning a *what to load* request
(dataset / model / prediction, plus source path and stride/keys) into a local
:class:`TaskManager` task.  It also owns the load implementations + validation
and the one ghost-model register/discover chokepoint.

Like ``SessionPersistence`` (ADR 0020) it takes the owning ``Environment`` and
reaches domain state through it (``self._env``).  It is deliberately
**Qt-free**, part of the Headless Core (ADR 0034).
"""

import logging
import os
import time

import numpy as np
from ffast.io.batched import BatchedArray, asarray
from ase.calculators.calculator import PropertyNotImplementedError

from ffast.core.flat_frames import FlatFrames
from ffast.io.extxyz_arrays import NotEnoughMemory
from ffast.loaders.ghost import GhostModelLoader
from ffast.readers import ReaderError
from ffast.readers.match import Facts, PredictionRefused, check_prediction
from ffast.readers.pairing import pair_names
from ffast.cache.fingerprint import md5FromArraysAndStrings

logger = logging.getLogger("FFAST")


def _resultOrNone(read):
    """``read()``, or ``None`` when the file has no such result (ADR 0058)."""
    try:
        return read()
    except (PropertyNotImplementedError, RuntimeError):
        return None



def _flatForcesOrNone(loader):
    """The loader's forces as one FlatFrames, made without a copy (ADR 0080),
    or ``None`` when the file has none."""
    values = _resultOrNone(loader.getAllForces)
    return None if values is None else FlatFrames(values, loader.getOffsets())


def _samplingSettings(sampling, energy):
    """The settings of a sample by energy (ADR 0096), with the defaults filled
    in: "even" across energies, automatic bins, seed 42."""
    from ffast.readers.sampling import DEFAULT_SEED
    sampling = dict(sampling or {})
    if sampling.get("n") is None:
        raise ReaderError("A sample by energy needs N, the number of frames to keep.")
    bins = sampling.get("bins")
    seed = sampling.get("seed")
    return {
        "method": sampling.get("method") or "even",
        "n": int(sampling["n"]),
        "bins": None if bins in (None, "") else int(bins),
        "seed": DEFAULT_SEED if seed in (None, "") else int(seed),
        "energy": energy,
    }


class LoadingCoordinator:
    """Owns dataset/model/prediction load routing, implementations, and ghosts (ADR 0034)."""

    def __init__(self, env):
        self._env = env

    #############
    ## ROUTING HELPERS
    #############

    def _queueLoad(self, fn, name, args=(), kwargs=None, on_error=None, on_done=None):
        """Queue an in-process load so disk I/O and setup stay off the main loop.

        Every ``taskLoad*`` entry point wants the same task shape — visual,
        threaded, named "Loading <thing>" — so that shape lives here rather than
        being spelled out at each of them.

        ``on_error(exc)`` lets the caller report a failed load (the server tells
        the window that asked); an error that is raised is still raised, so the
        task fails as before. The ``load*`` methods also take ``on_error``, for
        the failures they log and return from.

        ``on_done()`` runs once the load returned without raising, in the
        task's thread, also after a failure the load reported and returned
        from: the caller tells those apart (ADR 0097, the server's final
        reply to the window that asked).
        """
        if on_error is not None or on_done is not None:
            load = fn
            if on_error is not None:
                kwargs = {**(kwargs or {}), "on_error": on_error}

            def fn(*a, **kw):
                try:
                    out = load(*a, **kw)
                except Exception as exc:
                    from ffast.metrics.stop import Stopped
                    from ffast.io.frame_index import FrameIndexCancelled
                    if isinstance(exc, (Stopped, FrameIndexCancelled)):
                        raise
                    if on_error is not None:
                        on_error(exc)
                        exc.ffast_reported = True   # no second pop-up (ADR 0066)
                    raise
                if on_done is not None:
                    on_done()
                return out

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

    def prepareFrameIndex(self, path, taskID=None):
        """Make (or reuse) an .xyz file's frame index, telling the task how far
        the scan is; joins a scan the Load dialog already started. None for
        other formats."""
        from ffast.io import frame_index
        if not frame_index.is_indexable(path):
            return None
        name = os.path.basename(path)
        task = getattr(getattr(self._env, "tm", None), "getTask", lambda _: None)(taskID)
        return frame_index.get_frame_index(
            path, progress=lambda n: self._progress(taskID, f"Indexing {name}: {n:,} frames…"),
            cancel=task.get("cancel_event") if task else None)

    def _readProgress(self, path, taskID):
        """Report the one read of a load (ADR 0076) on its task: at most once
        a second, and always the end. Workers finish a batch every few
        milliseconds; a message each filled the page's queue on MeluXina."""
        name = os.path.basename(path)
        last = [None]
        lead = [f"Reading {name}"]

        def report(done, total):
            from ffast.metrics.stop import check
            check()
            now = time.monotonic()
            if done < total and last[0] is not None and now - last[0] < 1.0:
                return
            last[0] = now
            self._progress(taskID, f"{lead[0]}: {done:,} of {total:,} frames…")

        def handed_over(note):
            """The fast parser gave the file up (ADR 0100): say so, and keep
            saying it in the progress text that follows."""
            lead[0] = note
            self._progress(taskID, f"{note}…")
        def bytes_read(done, total, frames):
            from ffast.metrics.stop import check
            check()
            now = time.monotonic()
            if done < total and last[0] is not None and now - last[0] < 1.0:
                return
            last[0] = now
            self._progress(taskID, f"{lead[0]}: {frames:,} frames, {done / max(1, total):.0%}…")
        report.handed_over = handed_over
        report.bytes_read = bytes_read
        return report

    def _resolveLoad(self, kind, path, typeName, popup=True):
        """Validate a load request and resolve the plugin class that serves it.

        Both in-process load paths open with the same two checks — the file is
        there, the plugin type is registered — and both used to report every
        failure as a *dataset* problem, so a missing model file or an
        unrecognised model type logged "Tried to load dataset".  ``kind`` is
        ``"model"`` or ``"dataset"``; returns the loader class, or ``None`` when
        the request is not loadable (already logged). ``popup=False`` keeps the log
        line out of the pop-ups when the caller reports the failure itself.
        """
        if not os.path.exists(path):
            logger.error(f"Tried to load {kind}, but path `{path}` not found",
                         extra={"popup": popup})
            return None

        registry = self._env.modelTypes if kind == "model" else self._env.datasetTypes
        loaderClass = registry.get(typeName)
        if loaderClass is None and kind == "dataset":
            from ffast.readers import registry as readers   # ADR 0095: a reader's type
            loaderClass = readers.get(typeName)
        if loaderClass is None:
            logger.error(
                f"Tried to load {kind}, but {kind} type `{typeName}` not recognised",
                extra={"popup": popup},
            )
        return loaderClass

    #############
    ## MODELS
    #############

    def taskLoadModel(self, path, modelType, on_error=None, on_done=None):
        """Queue model loading so disk I/O and setup do not block the main loop."""
        self._queueLoad(self.loadModel, "Loading model", args=(path, modelType),
                        on_error=on_error, on_done=on_done)

    def requestModelLoad(self, path, modelType):
        """Queue a model load in-process."""
        self.taskLoadModel(path, modelType)

    def loadModel(self, path, modelType, taskID=None, on_error=None):
        """Validate and instantiate a concrete model from disk."""
        loaderClass = self._resolveLoad("model", path, modelType, popup=on_error is None)
        if loaderClass is None:
            if on_error:
                on_error(f"no file at {path}" if not os.path.exists(path)
                         else f"this server has no model type {modelType!r}")
            return None

        # Instantiating a concrete predicting ModelLoader triggers its heavy ML
        # backend (torch/mace/nequip/...), which is loaded lazily and runs
        # server-side (ADR 0030). Guard it: a missing/broken backend must warn
        # and abort this one load, not crash the server task (local or remote).
        try:
            model = loaderClass(self._env, path)
            if model is None:
                logger.warning(f"Model `{path}` did not load successfully",
                               extra={"popup": on_error is None})
                if on_error:
                    on_error("the model loader returned nothing")
                return
            model.initialise()
        except (ImportError, ModuleNotFoundError) as exc:
            logger.error(
                f"Cannot load model `{path}` of type `{modelType}`: its ML "
                f"backend is not available in this ffast-server environment "
                f"({exc}). Install the backend where the server runs "
                f"(local venv or cluster). Predictions require server-side "
                f"inference (ADR 0030).",
                extra={"popup": on_error is None},
            )
            if on_error:
                on_error(exc)
            return None
        except Exception as exc:
            logger.error(
                f"Failed to load model `{path}` of type `{modelType}`: {exc}",
                extra={"popup": on_error is None},
            )
            if on_error:
                on_error(exc)
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

    def taskLoadPrepredictedDataset(self, path, datasetKey, selected_energy_key=None,
                                    selected_force_key=None, on_error=None, reader=None,
                                    on_done=None):
        """Queue import of external predictions as cached model outputs for a dataset."""
        self._queueLoad(
            self.loadPrepredictedDataset,
            "Loading prepredicted dataset",
            args=(path, datasetKey),
            kwargs={
                'selected_energy_key': selected_energy_key,
                'selected_force_key': selected_force_key,
                'reader': reader,
            },
            on_error=on_error,
            on_done=on_done,
        )

    def requestPredictionLoad(self, path, datasetKey, selected_energy_key=None,
                              selected_force_key=None):
        """Queue a prediction-file load in-process."""
        self.taskLoadPrepredictedDataset(
            path, datasetKey,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
        )

    def loadPrepredictedDataset(self, path, datasetKey, taskID=None, selected_energy_key=None,
                                selected_force_key=None, on_error=None, reader=None):
        """Attach precomputed energies and forces to a dataset as a ghost model.

        ``reader`` names the reader (ADR 0095) that reads the file; without one
        the readers for the file's ending choose."""
        readerClass = self._predictionReader(path, reader, on_error)
        if readerClass is False:
            return None
        if readerClass is not None:
            return self._loadReaderPrediction(
                readerClass, path, datasetKey, taskID, selected_energy_key,
                selected_force_key, on_error)
        # Every file a prediction can come from has a reader (ASE's takes the
        # formats no other does): nothing is left to read another way.
        text = f"No reader reads {os.path.basename(path)}"
        logger.error(text, extra={"popup": on_error is None})
        if on_error:
            on_error(text)
        return None

    @staticmethod
    def _predictionReader(path, name, on_error):
        """The reader class that reads a prediction file (ADR 0095): the one
        named, else the first the file's ending and ``can_read`` pick (ASE's
        catch-all reader takes what no other does), else None. False when the
        named reader is not installed (already reported)."""
        from ffast.readers import registry
        if name:
            found = registry.get(name)
            if found is None:
                text = f"{os.path.basename(path)} needs the {name} reader, which is not installed"
                logger.error(text, extra={"popup": on_error is None})
                if on_error:
                    on_error(text)
                return False
            return found
        candidates = registry.readers_for(path)
        return candidates[0] if candidates else None

    def _loadReaderPrediction(self, readerClass, path, datasetKey, taskID,
                              energyKey, forceKey, on_error):
        """A prediction from a reader (ADR 0095): the frames the dataset kept,
        read in chunks, paired by name, checked, then ingested."""
        from ffast.readers import chunks
        from ffast.readers.dataset import ReaderFieldSource

        name = os.path.basename(path)
        try:
            dataset = self._env.datasets.get(datasetKey)
            slice_num = self._env.datasets.slice_numbers.get(datasetKey)
            sample = getattr(self._env.datasets, "kept_frames", {}).get(datasetKey)

            def select(nFile):
                """The file's frames the dataset kept: every stride-th, or
                the ones an energy sample chose (ADR 0096)."""
                source = getattr(dataset, "sourceFrameCount", None)
                if source is not None and nFile != source:
                    raise PredictionRefused(
                        f"Prediction '{name}' has {nFile:,} frames, but the dataset's file "
                        f"has {source:,}.")
                if sample is not None:
                    kept = [int(f) for f in sample]
                else:
                    step = slice_num if slice_num and slice_num > 1 else 1
                    kept = range(0, nFile if source is None else source, step)
                expected = dataset.getN() if hasattr(dataset, "getN") else None
                if expected is not None and len(kept) != expected:
                    raise PredictionRefused(
                        f"Frame count mismatch for prediction '{name}'. "
                        f"Expected {expected} frames, got {len(kept)}.")
                return kept

            storage = getattr(dataset, "storage", None)
            if (storage is not None and readerClass is storage.reader
                    and os.path.realpath(path) == os.path.realpath(storage.path)):
                # All columns and fingerprints were established together.
                # Reuse this exact source version and frame selection.
                from ffast.readers.indexed import IndexedArrays
                storage.check_source()
                loaded = chunks.Loaded(IndexedArrays(storage), dataset.contents,
                                       storage.frames, dataset.sourceFrameCount,
                                       dataset.reader_name)
            else:
                readerClass, loaded = self._readArrays(readerClass, path, taskID, select=select)
            arrays, contents = loaded.arrays, loaded.contents
            energies, forces = contents.get("energies") or [], contents.get("forces") or []
            if not energies and not forces:
                raise ReaderError(f"{name} has no energies or forces, so it cannot load as a prediction")
            ek, fk = pair_names(energies, forces, energyKey, forceKey, file=name)
            if ek is None and fk is None:
                raise ReaderError(f"Nothing to load from {name}: no energy and no force chosen")
            E = arrays.get(f"energy:{ek}") if ek else None
            E = None if E is None else asarray(E, dtype=np.float64).reshape(-1)
            Fv = arrays.get(f"forces:{fk}") if fk else None
            counts = np.asarray(arrays["n_atoms"], dtype=np.int64)
            F = None if Fv is None else FlatFrames(Fv, np.concatenate([[0], np.cumsum(counts)]))
            facts = Facts(
                name=name, n_kept=len(loaded.frames), n_file_frames=loaded.n_file_frames,
                counts=counts, numbers=arrays.get("numbers"), positions=arrays.get("positions"),
                units=contents.get("units") or None)
            available = [x for x in (E, Fv) if x is not None]
            fingerprint = md5FromArraysAndStrings(*available)
        except (ReaderError, NotEnoughMemory) as e:
            # Its text as it is, no traceback (ADR 0095, decision 8).
            logger.error(str(e), extra={"popup": on_error is None})
            if on_error:
                on_error(str(e))
            return None

        with self._env.mutation_lock:
            if not self._ingestPrediction(
                self._env.datasets.get(datasetKey), E, F, path=path, name=name,
                fingerprint=fingerprint, source=ReaderFieldSource(arrays),
                facts=facts, popup=False,
            ):
                logger.error(self._lastRefusal, extra={"popup": on_error is None})
                if on_error:
                    on_error(self._lastRefusal)
                return None
            self.lookForGhosts()

    def _extractPredictionFields(self, aseObject, modelKey, dataset):
        """Eagerly extract the prediction Dataset Fields (ADR 0023, ADR 0061).

        The prediction's ASE loader is discarded once E/F are pulled, so every
        prediction.{info,atoms}.<key> a registered metric declares, plus every
        numeric scalar the file itself holds (``availableFieldKeys``: one number
        per frame or per atom), must be read now, so a metric saved later over a
        new field finds it. They are stashed in ``DataService.predictionFields`` keyed by
        ``(modelKey, dataset_fp)`` (modelKey == the GhostModel fingerprint, so
        the resolver finds it from the model object). Strict per-field: an
        unavailable key just stays absent (resolves to None later).

        Every exit replaces the entry: a source with no fields (or one whose
        keys cannot be read) removes what an earlier load with the same
        fingerprints stored, so no old value survives a reload.
        """
        fields = self._env.data.predictionFields
        try:
            from ffast.metrics.fields import declared_field_keys
            wanted = declared_field_keys("prediction")
            frame_keys, atom_keys = aseObject.availableFieldKeys()
        except Exception:
            logger.exception("Prediction fields: failed to read declared keys")
            fields.replace(modelKey, dataset.fingerprint, None)
            return
        wanted = {"info": set(wanted["info"]) | set(frame_keys),
                  "atoms": set(wanted["atoms"]) | set(atom_keys)}
        if not wanted["info"] and not wanted["atoms"]:
            fields.replace(modelKey, dataset.fingerprint, None)
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
                # Where a frame lacks the column (ADR 0076); absent when none does.
                ask = getattr(aseObject, "getAtomFieldMissing", None)
                missing = ask(key) if ask else None
                if missing is not None:
                    store.setdefault("missing", {})[key] = missing
        fields.replace(modelKey, dataset.fingerprint, store)
        if store["info"] or store["atoms"]:
            logger.info(
                "Prediction fields: extracted %d frame + %d atom field(s) for %s",
                len(store["info"]), len(store["atoms"]), os.path.basename(dataset.getName()),
            )

    #############
    ## PREDICTION INGEST (one body, two entry points)
    #############

    @staticmethod
    def _aseLoaderFor(path, atomsList, *, energy_key=None, force_key=None, progress=None):
        """Build the ASE loader for ``atomsList``. There is one; it decides
        uniform or variable from the frames itself (ADR 0058)."""
        from ffast.loaders.ase import aseDatasetLoader

        loader = aseDatasetLoader(
            path,
            atomsList=atomsList,
            selected_energy_key=energy_key,
            selected_force_key=force_key,
            progress=progress,
        )
        logger.info(
            "Loading prediction as %s ASE dataset: %d molecules",
            "variable" if loader.isVariable else "uniform", len(atomsList),
        )
        return loader

    @staticmethod
    def _checkPrediction(E, dataset, name, F=None, facts=None):
        """The one check every prediction path passes (ADR 0095, decision 6;
        ``ffast.readers.match``). Raises PredictionRefused, else returns.

        ``facts`` is what the path knows of the file; where it does not say
        how many frames the prediction has, they come from the energies, else
        the forces. A prediction with neither has nothing to count.
        """
        import dataclasses

        facts = facts or Facts(name=name, n_kept=None)
        n_kept, counts = facts.n_kept, facts.counts
        if n_kept is None:
            if E is not None:
                n_kept = np.asarray(E).reshape(-1).size
            elif isinstance(F, FlatFrames):
                n_kept = len(F.offsets) - 1
            elif F is not None:
                n_kept = len(F)
        if counts is None and isinstance(F, FlatFrames):
            counts = np.diff(F.offsets)
        check_prediction(dataset, dataclasses.replace(facts, n_kept=n_kept, counts=counts))

    @staticmethod
    def _predictionMatchesDataset(E, dataset, name, F=None, facts=None):
        """Whether a prediction fits its dataset; the reason is logged."""
        try:
            LoadingCoordinator._checkPrediction(E, dataset, name, F, facts)
        except PredictionRefused as e:
            logger.error(str(e))
            return False
        return True

    def _ingestPrediction(self, dataset, E, F, *, path, name, fingerprint,
                          source=None, facts=None, popup=True, **catalogExtra):
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

        ``facts`` says more of the file than E and F do (ADR 0095): atoms per
        frame, elements, positions, units, the file's length. Every path passes
        the same check (``_checkPrediction``).

        Returns ``True`` when the prediction was ingested, ``False`` when its
        arrays do not match the dataset (the reason is logged, as a pop-up
        unless ``popup=False``, and kept in ``_lastRefusal``); the caller
        decides whether that aborts the whole load or skips this one column.

        Callers must hold ``mutation_lock``.
        """
        try:
            self._checkPrediction(E, dataset, name, F, facts)
        except PredictionRefused as e:
            self._lastRefusal = str(e)
            logger.error(self._lastRefusal, extra={"popup": popup})
            return False

        if E is not None:
            energyDataType = self._env.data.getDataType("energy")
            self._env.data.setData(
                energyDataType.newDataEntity(energy=asarray(E).reshape(-1)),
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
        # The same prediction loaded again: its fingerprint covers only energies
        # and forces, so its fields may differ, and what was computed from the
        # old ones is stale (as for a dataset, DatasetRegistry.add).
        known = fingerprint in self._env.models
        if known:
            self._env.eventPush("OBJECT_REPLACED", fingerprint)

        # npz carries no fields: clear any an earlier load stored here.
        if source is not None:
            self._extractPredictionFields(source, fingerprint, dataset)
        else:
            self._env.data.predictionFields.replace(
                fingerprint, dataset.fingerprint, None)

        self.registerGhostModel(fingerprint, path=path, name=name, **catalogExtra)
        # A known prediction loaded for another dataset gets no MODEL_LOADED
        # (it is already loaded), yet it now has numbers for one more dataset:
        # every window must hear the new list, or it greys the prediction out
        # on that dataset.
        if known:
            self._env.eventPush("MODEL_DATASETS_CHANGED", fingerprint)
        return True

    #############
    ## DATASETS
    #############

    def taskLoadDataset(self, path, datasetType, selected_energy_key=None,
                       selected_force_key=None, prediction_keys=None, slice_num=0,
                       on_error=None, sampling=None, on_done=None, memory_mode="auto"):
        """Load dataset and optionally load predictions from same file.

        Args:
            path: Path to dataset file
            datasetType: Type of dataset loader to use
            selected_energy_key: Pre-selected energy key for reference (ASE only)
            selected_force_key: Pre-selected force key for reference (ASE only)
            prediction_keys: List of (energy_key, force_key, model_name) tuples
            :param slice_num: slicing number for sampled load of datasets.
            sampling: a sample by energy instead (ADR 0096), see ``loadDataset``.
        """
        kwargs = {
            'selected_energy_key': selected_energy_key,
            'selected_force_key': selected_force_key,
            'prediction_keys': prediction_keys,
            'slice_num': slice_num
        }
        if memory_mode != "auto":
            kwargs['memory_mode'] = memory_mode
        if sampling is not None:
            kwargs['sampling'] = sampling
        self._queueLoad(
            self.loadDataset,
            "Loading dataset",
            args=(path, datasetType),
            kwargs=kwargs,
            on_error=on_error,
            on_done=on_done,
        )

    def requestDatasetLoad(self, path, datasetType, selected_energy_key=None,
                           selected_force_key=None, prediction_keys=None,
                           slice_num=0, memory_mode="auto"):
        """Queue a dataset load in-process."""
        self.taskLoadDataset(
            path, datasetType,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            prediction_keys=prediction_keys,
            slice_num=slice_num,
            memory_mode=memory_mode,
        )

    def loadDataset(self, path, datasetType, taskID=None, selected_energy_key=None,
                   selected_force_key=None, prediction_keys=None, slice_num=0,
                   on_error=None, sampling=None, frames=None, memory_mode="auto"):
        """Load dataset and create ghost models for prediction keys.

        A reader (ADR 0095) serves its type for every file it takes (the xyz
        reader takes ``.xyz`` and ``.extxyz`` for "ase (auto)"); a dataset type
        without a reader is a plugin loader, called as ``loaderClass(path)``.

        ``sampling`` loads a sample by energy instead (ADR 0096): ``{method, n,
        bins, seed, energy}``. ``frames`` with it are the file frames a sample
        already chose (a saved session): they are read as they are."""
        loaderClass = self._datasetReader(datasetType, path)
        isReader = loaderClass is not None
        if isReader and not os.path.exists(path):
            logger.error(f"Tried to load dataset, but path `{path}` not found",
                         extra={"popup": True})
            return None
        if not isReader:
            if sampling is not None:
                text = (f"{os.path.basename(path)} cannot be sampled by energy: "
                        f"the {datasetType} type has no reader that lists energies")
                logger.error(text, extra={"popup": on_error is None})
                if on_error:
                    on_error(ReaderError(text))
                return None
            loaderClass = self._resolveLoad("dataset", path, datasetType)
            if loaderClass is None:
                return None

        # Load dataset - pass selected keys to ASE loader
        if isReader:
            try:
                result = self._loadWithReader(
                    loaderClass, path, slice_num, selected_energy_key,
                    selected_force_key, taskID, sampling=sampling, frames=frames, memory_mode=memory_mode)
            except (ReaderError, NotEnoughMemory) as e:
                # ReaderError's text is for the user as it stands (ADR 0095,
                # decision 8): no traceback, no "Failed to load" around it.
                logger.error(str(e), extra={"popup": on_error is None})
                if on_error:
                    on_error(e)
                return None
        else:
            result = loaderClass(path)

        dataset = result

        if dataset is None:
            logger.warning(f"Dataset `{path}` did not load successfully",
                           extra={"popup": on_error is None})
            if on_error:
                on_error("no dataset could be read from it")
            return

        from ffast.metrics.stop import check
        check()
        dataset.initialise()

        # Resolve the prediction-keys atomsList BEFORE the lock: a cache miss
        # here (dataset has no .atomsList) falls through to a full re-read of
        # the file inside _loadPredictionsFromKeys. That must not happen while
        # holding mutation_lock — deleteObject (ADR 0044 Phase 3) acquires the
        # same lock synchronously on the event loop thread (no to_thread), so
        # a slow read held under the lock would freeze the whole event loop —
        # every connection's dispatch — for its duration, not just serialize
        # the registry mutation.
        atomsList = dataset.atomsList if hasattr(dataset, 'atomsList') else None
        if prediction_keys and atomsList is None and not isReader:
            import ase.io
            atomsList = ase.io.read(path, index=":")

        # mutation_lock again (see loadModel above).
        with self._env.mutation_lock:
            settings = getattr(dataset, "sampling", None)
            if settings is not None:
                self._env.datasets.add(dataset, 0, kept_frames=np.asarray(dataset.sourceFrames),
                                       sampling=settings)
            else:
                self._env.datasets.add(dataset, slice_num)
            logging.info(f"Dataset `{path}` successfully loaded")

            # Load predictions as ghost models if specified
            if prediction_keys:
                self._loadPredictionsFromKeys(dataset, path, prediction_keys, atomsList=atomsList)

            self.lookForGhosts()

        if isReader:
            dataset.arrays = None   # the file's extra sets are not kept in memory (its fields are)

    @staticmethod
    def _datasetReader(typeName, path):
        """The reader that serves the dataset type ``typeName`` for ``path``,
        or None: no reader of that name, or one that does not take this file
        (its ``can_read`` says no)."""
        from ffast.readers import registry
        found = registry.get(typeName)
        if found is None:
            return None
        can_read = getattr(found, "can_read", None)
        try:
            if can_read is None or can_read(path):
                return found
        except Exception:
            return None
        # The type's reader leaves this file to another (ASE's does not take
        # .xyz and .extxyz: the xyz reader does): the first that takes it.
        others = registry.readers_for(path)
        return others[0] if others else None

    def _readArrays(self, readerClass, path, taskID, **kwargs):
        """``(reader class that read it, Loaded)``: the arrays of ``path``
        through ``readerClass``, with progress on the task. A Level 2 reader
        that cannot parse the file (NotFast) hands it to its ``fallback`` reader,
        as the fast .xyz parser always handed it to ASE."""
        from ffast.io.extxyz_arrays import NotFast, PropertiesDiffer
        from ffast.readers import chunks, registry

        from ffast.readers.text import can_scan_and_read
        if not can_scan_and_read(readerClass, path, **kwargs):
            self.prepareFrameIndex(path, taskID)    # shares the Load dialog's scan, and reports it
        progress = self._readProgress(path, taskID) if taskID is not None else None
        handed = getattr(progress, "handed_over", None)
        try:
            return readerClass, chunks.read_arrays(readerClass, path, progress=progress,
                                                   on_handover=handed, **kwargs)
        except NotFast as exc:
            other = registry.get(getattr(readerClass, "fallback", None) or "")
            if other is None:
                raise ReaderError(f"{os.path.basename(path)} could not be read: {exc}") from exc
            name = os.path.basename(path)
            if isinstance(exc, PropertiesDiffer):
                logger.warning("%s: %s Reading it with ASE, which is slower.", name, exc)
            else:
                logger.info("%s is read with ASE: %s", name, exc)
            return other, chunks.read_arrays(other, path, progress=progress, **kwargs)

    def _loadWithReader(self, readerClass, path, slice_num, energyKey, forceKey, taskID,
                        sampling=None, frames=None, memory_mode="auto"):
        """A dataset from a reader (ADR 0095): FFAST asks for the frames the
        stride keeps, or the energy sample (ADR 0096), in chunks, with progress
        and the memory check, then builds its own dataset from the arrays."""
        from ffast.readers import chunks
        from ffast.readers.dataset import ReaderDataset

        settings = None
        kwargs = {"stride": slice_num}
        if sampling is not None:
            if slice_num not in (None, 0, 1):
                raise ReaderError("Load either a sample by energy or every n-th frame, not both.")
            kept, settings = self._energySample(readerClass, path, sampling, energyKey, frames, taskID)
            kwargs = {"select": self._keptFrames(path, kept)}
        kwargs["memory_mode"] = memory_mode
        readerClass, loaded = self._readArrays(readerClass, path, taskID, **kwargs)
        with chunks.reader_errors(readerClass.name, path):
            dataset = ReaderDataset(path, loaded, readerClass.name,
                                    energy_key=energyKey, force_key=forceKey)
        dataset.requested_memory_mode = memory_mode
        if settings is not None:
            dataset.sampling = settings
        return dataset

    @staticmethod
    def _keptFrames(path, kept):
        """``select`` for the frames a sample chose: refused when the file no
        longer has them (it changed since a saved session made the sample)."""
        def select(nFile):
            if kept and (kept[0] < 0 or kept[-1] >= nFile):
                raise ReaderError(
                    f"{os.path.basename(path)} has changed since its sample was made: it has "
                    f"{nFile:,} frames, the sample has frame {kept[-1]:,}")
            return kept
        return select

    def _energySample(self, readerClass, path, sampling, energyKey, frames, taskID):
        """``(kept file frames, settings)`` of a sample by energy (ADR 0096).
        Given ``frames`` (a saved session) are kept as they are; else the
        reader's energies are read (once per file, then kept) and sampled."""
        from ffast.readers import sampling as energy_sampling
        from ffast.readers import warn
        from ffast.readers.energy_sample import choose_energy, file_energies

        name = os.path.basename(path)
        if frames is not None:
            return sorted({int(f) for f in frames}), dict(sampling)
        if taskID is not None and callable(getattr(readerClass, "energies", None)):
            self._progress(taskID, f"Reading the energies of {name}…")
        energies, counts = file_energies(readerClass, path)
        chosen = choose_energy(readerClass, path, energies,
                               (sampling or {}).get("energy") or energyKey or None)
        settings = _samplingSettings(sampling, chosen)
        try:
            result = energy_sampling.sample_by_energy(
                energies[chosen], settings["n"], method=settings["method"],
                bins=settings["bins"], seed=settings["seed"], n_atoms=counts)
        except ValueError as e:
            raise ReaderError(str(e)) from None
        if result.left_out:
            warn(f"{name}: {result.left_out:,} frames have no energy '{chosen}' and are "
                 f"left out of the sample.")
        if result.kept_all:
            warn(f"{name}: {settings['n']:,} frames asked for, but only {len(result.frames):,} "
                 f"have an energy: all {len(result.frames):,} are kept.")
        settings.update(bins_used=result.bins_used, per_atom=result.per_atom,
                        left_out=result.left_out, n_file_frames=len(counts))
        return result.frames.tolist(), settings

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
        from ffast.readers.dataset import ReaderDataset
        if isinstance(dataset, ReaderDataset):
            self._loadReaderPredictionsFromKeys(dataset, path, prediction_keys)
            return

        # Read file only if not provided
        if atomsList is None:
            import ase.io
            atomsList = ase.io.read(path, index=":")

        for energy_key, force_key, model_name in prediction_keys:
            try:
                temp_loader = self._aseLoaderFor(
                    path, atomsList,
                    energy_key=energy_key, force_key=force_key,
                )

                E = _resultOrNone(temp_loader.getEnergies)
                F = _flatForcesOrNone(temp_loader)
                if E is None and F is None:
                    logger.warning(f"Prediction '{model_name}' has neither energies nor forces; skipped.")
                    continue
                available = [x for x in (E, None if F is None else F.values) if x is not None]
                ghost_fp = md5FromArraysAndStrings(*available, model_name)


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

    def _loadReaderPredictionsFromKeys(self, dataset, path, prediction_keys):
        """The same for a reader's dataset (ADR 0095): the names are read from
        the arrays the reader returned with the dataset, paired and checked
        like any prediction. ``""`` as a name means none."""
        from ffast.readers.dataset import ReaderFieldSource

        arrays, contents = dataset.arrays, dataset.contents
        storage = getattr(dataset, "storage", None)
        if storage is not None:
            storage.check_source()
        if arrays is None:
            logger.error(f"The extra values of {os.path.basename(path)} are no longer in memory")
            return
        offsets = dataset.molecule_offsets
        source = ReaderFieldSource(arrays)
        for energy_key, force_key, model_name in prediction_keys:
            try:
                ek, fk = pair_names(
                    contents.get("energies") or [], contents.get("forces") or [],
                    energy_key or "", force_key or "", file=os.path.basename(path))
                E = arrays.get(f"energy:{ek}") if ek else None
                E = None if E is None else asarray(E, dtype=np.float64).reshape(-1)
                Fv = arrays.get(f"forces:{fk}") if fk else None
                if E is None and Fv is None:
                    logger.warning(f"Prediction '{model_name}' has neither energies nor forces; skipped.")
                    continue
                F = None if Fv is None else FlatFrames(Fv, offsets)
                available = [x for x in (E, Fv) if x is not None]
                facts = Facts(name=model_name, n_kept=dataset.N,
                              n_file_frames=dataset.sourceFrameCount, counts=np.diff(offsets))
                if not self._ingestPrediction(
                    dataset, E, F, path=path, name=model_name,
                    fingerprint=md5FromArraysAndStrings(*available, model_name),
                    source=source, facts=facts, energy_key=ek or "", force_key=fk or "",
                ):
                    continue
                logger.info(f"Loaded predictions for '{model_name}' from names {ek}/{fk}")
            except Exception as e:
                logger.error(f"Failed to load predictions for '{model_name}': {e}")
                logger.debug("", exc_info=True)

    #############
    ## GHOSTS
    #############

    def registerGhostModel(self, fingerprint, path, name, **extra):
        """Record a ghost model's metadata in the Object Catalog (ADR 0034).

        Single chokepoint for the ``{"path","name","type":"ghost_model", ...}``
        registration that was copy-pasted across the load paths.  ``initialise()`` on
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
        :meth:`registerGhostModel`: the place a ghost model is built.
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
