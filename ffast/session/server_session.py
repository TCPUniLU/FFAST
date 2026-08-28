"""ServerSession: the live, server-scoped core of a running ``ffast-server``.

One ``ServerSession`` exists per server process. It owns the open Visualization
Views and the server→client outbound queue, dispatches the controlling client's
Control messages to the Environment through a built-once event→handler table,
and replays current state to a client on connect or reconnect. It holds a
reference to the Environment rather than owning it.

Connection lifecycle — handshake, Client Role gating, recovery window, the
receive/send loops — stays in ``server.py``; this object assumes ``dispatch`` is
only called for events the connecting client is authorized to drive.

The dispatch table maps each event to a handler method, an ordered list of
parameter names (``"name"`` required, ``"?name"`` optional), and — per ADR
0033 — the typed request model that documents and validates its payload.
``dispatch`` resolves each name from positional ``args`` by index, falling
back to the same-named kwarg, validates that the required names are present,
validates the resolved payload against the route's model (a gate only — a
malformed message is dropped with the event named in the log, never reshaped
before reaching the handler), and calls the handler with named parameters —
so handlers read ``path`` rather than ``args[0]``. Genuinely irregular events
(``VIEW_COMMAND`` pydantic parsing) declare no names and no model; they
validate inside the handler instead.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
from pydantic import BaseModel, ValidationError

from ffast.protocol import control

logger = logging.getLogger("FFAST")


def _probe_path_diagnostics(path):
    """Report the server process's own view of a path for FileNotFound triage.

    A remote path that exists on the login node but not for the server process
    (e.g. the server runs in a SLURM job on a compute node that mounts a
    different filesystem — ADR 0028) is otherwise indistinguishable from a typo.
    Returns (hostname, cwd, exists, parent_exists) for a single log line.
    """
    import os, socket
    try:
        host = socket.gethostname()
    except Exception:
        host = "?"
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = "?"
    return (
        host,
        cwd,
        os.path.exists(path),
        os.path.isdir(os.path.dirname(path) or "."),
    )


# Metric computation runs in the SAME process as the GUI (the local server is
# in-process). Routing it through asyncio's default thread pool lets a burst of
# recomputes — e.g. toggling the energy shift re-runs ~15 KDE/density metrics
# across the loaded predictions — spin up a dozen CPU threads that thrash the
# GIL and starve the Qt event loop (measured 120-533ms stalls = visible UI
# lag). A small dedicated pool caps the GIL competitors so the UI keeps
# breathing; throughput barely drops since GIL-bound work doesn't parallelise
# anyway. It is deliberately SEPARATE from the default pool: client worker
# threads block there awaiting server replies, and sharing one bounded pool
# would deadlock (blocked clients holding every slot the compute needs).
#
# Built lazily on first metric request so merely importing ``ffast.session``
# (which the GUI client does for ConnectionRegistry / LocalServerManager) costs
# nothing — the pool only exists in a process that actually computes metrics.
_METRIC_COMPUTE_POOL: ThreadPoolExecutor | None = None


def _metric_compute_pool() -> ThreadPoolExecutor:
    global _METRIC_COMPUTE_POOL
    if _METRIC_COMPUTE_POOL is None:
        _METRIC_COMPUTE_POOL = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="ffast-metric"
        )
    return _METRIC_COMPUTE_POOL

# A table row: the handler method, the ordered names it binds from
# (args, kwargs) — a leading "?" marks an optional name (resolves to None) —
# and the typed request model that validates the resolved payload. ``model``
# is None only for VIEW_COMMAND, which validates its own irregular payload.
@dataclass(frozen=True)
class _Route:
    fn: object
    arg_names: list
    model: Optional[type[BaseModel]] = None


class _PredictionView:
    """Minimal adapter exposing cached prediction forces to build_scene.

    build_scene calls ``get_prediction(dataset_fp, model_fp).forces[idx]``;
    the cache stores forces under ``forces__{model_fp}__{dataset_fp}``.
    """
    __slots__ = ("forces",)

    def __init__(self, forces) -> None:
        self.forces = forces


class ServerSession:
    """Owns Visualization Views, dispatches client events, replays state.

    See the module docstring for the dispatch-table contract. The interface is
    deliberately small — ``dispatch`` and ``replay`` — so it tests by
    constructing ``ServerSession(fake_env, asyncio.Queue())`` and asserting on
    the env calls and what lands in the outbound queue, with no socket or
    thread in the loop.
    """

    def __init__(self, env, outbound: asyncio.Queue) -> None:
        self.env = env
        self.outbound = outbound
        self.views: dict = {}  # view_id → VisualizationView

        # event → (handler, ordered arg-name list, request model). Built once;
        # the table IS the documented RPC surface. "?name" marks an optional
        # parameter.
        from ffast.protocol.messages import (
            CloseViewRequest, CreateSubsetRequest, DeclareSubsetRequest,
            DeleteObjectRequest,
            EmptyRequest, ExportSubsetRequest, ListDirRequest,
            LoadDatasetRequest, LoadModelRequest,
            LoadPredictionRequest, LoadSessionRequest, OpenViewRequest,
            ProbeDatasetKeysRequest, ProbeDatasetLengthRequest,
            RequestMetricRequest, RequestPredictionArraysRequest,
            RequestSubdatasetArraysRequest, SaveSessionRequest,
        )
        self._handlers: dict[str, _Route] = {
            control.LOAD_DATASET:               _Route(self._on_load_dataset, ["path", "dataset_type"], LoadDatasetRequest),
            control.LOAD_MODEL:                 _Route(self._on_load_model, ["path", "model_type"], LoadModelRequest),
            control.DELETE_OBJECT:              _Route(self._on_delete_object, ["fingerprint"], DeleteObjectRequest),
            control.CREATE_SUBSET:              _Route(self._on_create_subset, ["parent_fingerprint", "indices"], CreateSubsetRequest),
            control.DECLARE_SUBSET:             _Route(self._on_declare_subset, ["parent_fingerprint", "indices"], DeclareSubsetRequest),
            control.REQUEST_SUBDATASET_ARRAYS:  _Route(self._on_request_subdataset_arrays, ["fingerprint"], RequestSubdatasetArraysRequest),
            control.PROBE_DATASET_KEYS:         _Route(self._on_probe_dataset_keys, ["path", "dataset_type"], ProbeDatasetKeysRequest),
            control.PROBE_DATASET_LENGTH:       _Route(self._on_probe_dataset_length, ["path"], ProbeDatasetLengthRequest),
            control.LIST_DIR:                   _Route(self._on_list_dir, ["?path"], ListDirRequest),
            control.LOAD_PREDICTION:            _Route(self._on_load_prediction, ["path", "dataset_fp"], LoadPredictionRequest),
            control.REQUEST_PREDICTION_ARRAYS:  _Route(self._on_request_prediction_arrays, ["dataset_fp", "model_fp"], RequestPredictionArraysRequest),
            control.OPEN_VIEW:                  _Route(self._on_open_view, [], OpenViewRequest),
            control.CLOSE_VIEW:                 _Route(self._on_close_view, ["view_id"], CloseViewRequest),
            control.VIEW_COMMAND:               _Route(self._on_view_command, []),
            control.REQUEST_STATE_SYNC:         _Route(self._on_request_state_sync, [], EmptyRequest),
            control.SAVE_SESSION:               _Route(self._on_save_session, ["path"], SaveSessionRequest),
            control.LOAD_SESSION:               _Route(self._on_load_session, ["path"], LoadSessionRequest),
            control.REQUEST_METRIC:             _Route(self._on_request_metric, ["metric_id", "?key"], RequestMetricRequest),
            control.REQUEST_METRIC_CATALOG:     _Route(self._on_request_metric_catalog, [], EmptyRequest),
            control.REQUEST_TAB_LAYOUT:         _Route(self._on_request_tab_layout, [], EmptyRequest),
            control.EXPORT_SUBSET:              _Route(self._on_export_subset, ["fingerprint", "path"], ExportSubsetRequest),
        }

    # ── dispatch ────────────────────────────────────────────────────────────

    async def dispatch(self, event: str, args, kwargs) -> None:
        """Route one authorized client event to its handler.

        Resolves the route's named parameters from ``args``/``kwargs``,
        validates that required names are present (logs and drops otherwise),
        validates the resolved payload against the route's request model —
        a gate only, per ADR 0033: on failure the event is named in the log
        and dropped, but a passing payload still reaches the handler as the
        original resolved dict, never the validated/reshaped one, so
        presence-sensitive fields keep their exact pre-validation meaning.
        """
        route = self._handlers.get(event)
        if route is None:
            logger.warning("Unknown client event: %s", event)
            return

        resolved, missing, consumed = self._resolve(route.arg_names, args, kwargs)
        if missing:
            logger.warning("%s: missing %s", event, ", ".join(missing))
            return

        extra = {k: v for k, v in kwargs.items() if k not in consumed}

        if route.model is not None:
            try:
                route.model.model_validate({**resolved, **extra})
            except ValidationError as exc:
                logger.warning("%s: payload validation failed: %s", event, exc)
                return

        await route.fn(**resolved, **extra)

    @staticmethod
    def _resolve(arg_names, args, kwargs):
        """Map an ordered name list onto (args, kwargs).

        Each name resolves to ``args[i]`` if present, else ``kwargs[name]``.
        A leading ``?`` marks the name optional. Returns the resolved dict, the
        list of missing required names, and the set of consumed names (so the
        caller can forward only the leftover kwargs). Pure — unit-testable with
        no env, socket, or thread.
        """
        resolved: dict = {}
        missing: list = []
        consumed: set = set()
        for i, spec in enumerate(arg_names):
            optional = spec.startswith("?")
            name = spec[1:] if optional else spec
            consumed.add(name)
            value = args[i] if i < len(args) else kwargs.get(name)
            if value is None and not optional:
                missing.append(name)
            resolved[name] = value
        return resolved, missing, consumed

    # ── outbound helpers ──────────────────────────────────────────────────────

    async def _emit(self, data) -> None:
        """Enqueue a server→client message; block if the queue is full so large
        transfers are never dropped."""
        try:
            self.outbound.put_nowait(data)
        except asyncio.QueueFull:
            await self.outbound.put(data)

    def _emit_or_drop(self, data, what: str) -> None:
        """Enqueue a state-replay message; drop with a warning if the queue is
        full (replay is best-effort — a reconnect or REQUEST_STATE_SYNC retries)."""
        try:
            self.outbound.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning(
                "State replay: outbound queue full, skipping %s", what
            )

    # ── scene accessors (built once; read live env state) ──────────────────────

    def get_prediction(self, dataset_fp, model_fp):
        """``get_prediction(dataset_fp, model_fp)`` for build_scene.

        Reads predicted forces from the fingerprint-keyed cache (the same
        entries the Prediction-Only Array Channel serves). Returns ``None`` when
        no prediction is cached for the pair, so build_scene omits force arrows.
        """
        if not dataset_fp or not model_fp:
            return None
        de = self.env.cache.get(f"forces__{model_fp}__{dataset_fp}")
        if de is None:
            return None
        forces = de.get("forces")
        if forces is None:
            return None
        return _PredictionView(forces)

    def get_forces(self, dataset_ref, idx):
        """``get_forces(dataset_ref, idx)`` for build_scene — *ground-truth*
        forces straight from the dataset (the default UI source). Predicted
        forces are resolved separately via ``get_prediction``.
        """
        if not dataset_ref:
            return None
        ds = self.env.datasets.get(dataset_ref)
        if ds is None:
            return None
        try:
            return ds.getForces(indices=idx)
        except Exception as exc:
            logger.debug("server: get_forces(%r, %d) failed: %s", dataset_ref, idx, exc)
            return None

    @staticmethod
    def _log_scene_degrade(where: str, view_id: str, exc: Exception) -> None:
        """Shared log line for the OPEN_VIEW/VIEW_COMMAND delete-race guards
        (ADR 0044 Phase 3) — an unexpected scene-rebuild exception, most
        likely a concurrent delete, degrades to a bare/empty scene."""
        logger.warning(
            "%s: scene rebuild failed for view=%r (likely a concurrent "
            "delete) — degrading to an empty scene: %s",
            where, view_id, exc,
        )

    # ── state replay ──────────────────────────────────────────────────────────

    def replay(self) -> None:
        """Push the current server state to a (re)connecting client.

        The degenerate (one-shot full push) case of the sync protocol: dataset
        and model metadata, the Metric Catalog, and a snapshot of every open
        view. Future incremental sync would replace this with delta events.
        """
        self._replay_state()
        self._replay_metric_catalog()
        if self.views:
            self._replay_views()

    def _replay_state(self) -> None:
        """Enqueue REMOTE_DATASET_META + REMOTE_MODEL_META for all objects."""
        from ffast.protocol.rpc import pack
        from ffast.protocol import DatasetMeta, ModelMeta

        # ── datasets ──────────────────────────────────────────────────────────
        try:
            datasets = self.env.datasets.all(excludeSubs=True)
        except Exception:
            datasets = []

        for dataset in datasets:
            fingerprint = getattr(dataset, "fingerprint", None)
            if fingerprint is None:
                continue
            try:
                data = pack(
                    control.REMOTE_DATASET_META,
                    (fingerprint,),
                    DatasetMeta.model_validate(dataset.toMetaDict()).model_dump(),
                )
                self._emit_or_drop(data, f"dataset {fingerprint!r}")
                logger.info(
                    "State replay: REMOTE_DATASET_META queued for %r", fingerprint
                )
            except Exception as exc:
                logger.warning(
                    "State replay: dataset %r error: %s", fingerprint, exc
                )

        # ── models (ghost predictions + real server-side models, Stage 2) ───────
        for model_fp, model in list(self.env.models.items()):
            try:
                name = getattr(model, "name", None) or model_fp[:8]
                dataset_fps = []
                from ffast.cache import CacheKey
                for cache_key in list(self.env.cache.keys()):
                    ck = CacheKey.try_parse(cache_key)
                    if ck is not None and ck.matches_model(model_fp) and ck.dataset_fp:
                        if ck.dataset_fp not in dataset_fps:
                            dataset_fps.append(ck.dataset_fp)
                data = pack(
                    control.REMOTE_MODEL_META,
                    (model_fp,),
                    ModelMeta(name=name, dataset_fingerprints=dataset_fps).model_dump(),
                )
                self._emit_or_drop(data, f"model {model_fp[:8]!r}")
                logger.info(
                    "State replay: REMOTE_MODEL_META queued for model=%r name=%r",
                    model_fp[:8], name,
                )
            except Exception as exc:
                logger.warning(
                    "State replay: model %r error: %s", model_fp[:8], exc
                )

    def _replay_metric_catalog(self) -> None:
        """Enqueue METRIC_CATALOG so the client builds metric controls from the
        server's registry (ADR 0016) rather than its own local one.

        Ensures the built-in metrics are registered first. (External Trusted
        Metric Modules appear here too once the server loads them from config.)
        """
        from ffast.protocol.rpc import pack
        try:
            import ffast.metrics.builtin  # noqa: F401 — register built-in metrics
            from ffast.metrics.catalog import build_metric_catalog
            from ffast.metrics.registry import _default_registry
            from ffast.protocol import MetricCatalog
            catalog = build_metric_catalog(_default_registry)
            data = pack(control.METRIC_CATALOG, [], MetricCatalog(metrics=catalog).model_dump())
            self._emit_or_drop(data, "METRIC_CATALOG")
            logger.info("State replay: METRIC_CATALOG queued (%d metrics)", len(catalog))
        except Exception as exc:
            logger.warning("State replay: METRIC_CATALOG error: %s", exc)

    def _replay_views(self) -> None:
        """Send a SCENE_SNAPSHOT for each open view on reconnect."""
        from ffast.protocol.rpc import pack

        for view in self.views.values():
            try:
                snapshot = view.snapshot(
                    get_dataset=self.env.datasets.get,
                    get_prediction=self.get_prediction,
                    get_forces=self.get_forces,
                    executor=self.env.data.metricExecutor,
                )
                data = pack(control.SCENE_SNAPSHOT, [], snapshot.model_dump())
                self._emit_or_drop(data, f"SCENE_SNAPSHOT view={view.state.view_id!r}")
                logger.debug("State replay: SCENE_SNAPSHOT queued for view=%r", view.state.view_id)
            except Exception as exc:
                logger.warning("State replay: SCENE_SNAPSHOT error for view=%r: %s", view.state.view_id, exc)

    # ── handlers ────────────────────────────────────────────────────────────

    async def _on_load_dataset(self, path, dataset_type, **kwargs) -> None:
        # msgpack deserializes tuples as lists; restore for prediction_keys
        if kwargs.get("prediction_keys"):
            kwargs["prediction_keys"] = [
                tuple(k) for k in kwargs["prediction_keys"]
            ]
        self.env.taskLoadDataset(path, dataset_type, **kwargs)

    async def _on_load_model(self, path, model_type, **kwargs) -> None:
        self.env.taskLoadModel(path, model_type)

    async def _on_delete_object(self, fingerprint, **kwargs) -> None:
        self.env.deleteObject(fingerprint)

    async def _on_create_subset(self, parent_fingerprint, indices, **kwargs) -> None:
        """Extract an atom-filtered subset dataset (ADR 0045 issue 12).

        ``indices`` is the mixed spec the view "hide atoms" filter accepts —
        integers and element-symbol tokens ("C", "-H"). It is resolved here
        against the parent's frame-0 composition (the same ``_resolve_filter_indices``
        the scene builder uses), then materialized through the existing
        in-process ``createAtomFilteredDataset``. The new dataset announces
        itself via the ``DATASET_LOADED`` → ``REMOTE_DATASET_META`` subscriber,
        so there is nothing to emit here.
        """
        from ffast.visualization.scene_builder import _resolve_filter_indices

        parent = self.env.datasets.get(parent_fingerprint)
        if parent is None:
            logger.warning("CREATE_SUBSET: parent %r not found", parent_fingerprint)
            return
        try:
            z = parent.getElements(0)
            idxs = _resolve_filter_indices(list(indices or []), z)
        except Exception as exc:
            logger.warning("CREATE_SUBSET: index resolution failed: %s", exc)
            return
        if not idxs:
            logger.warning("CREATE_SUBSET: %r resolved to no atoms", indices)
            return
        try:
            self.env.createAtomFilteredDataset(parent, idxs)
        except Exception as exc:
            logger.warning("CREATE_SUBSET: createAtomFilteredDataset failed: %s", exc)

    async def _on_declare_subset(self, parent_fingerprint, indices, **kwargs) -> None:
        """Declare a frame-index SubDataset from a plot box-select (subbing).

        The desktop's ``BasicPlotWidget`` turns a plot viewport/selection into a
        set of parent **configuration** indices and calls
        ``env.declareSubDataset(parent, model, idx, name)`` in-process (ADR 0021
        subbing). The browser has no in-process Environment, so it ships the
        covered indices here; this is the server-side twin of that call. The new
        (or refreshed) ``SubDataset`` announces itself via ``DATASET_LOADED`` →
        ``REMOTE_DATASET_META``, so — like ``CREATE_SUBSET`` — nothing is emitted
        directly, and the subset becomes usable by the 3D view and other tabs
        (PRD stories 61-62).
        """
        parent = self.env.datasets.get(parent_fingerprint)
        if parent is None:
            logger.warning("DECLARE_SUBSET: parent %r not found", parent_fingerprint)
            return
        model_fp = kwargs.get("model_fp")
        model = self.env.models.get(model_fp) if model_fp else None
        name = kwargs.get("name") or "Subset"
        idx = [int(i) for i in (indices or [])]
        if not idx:
            logger.warning("DECLARE_SUBSET: empty index set for %r", parent_fingerprint)
            return
        try:
            self.env.declareSubDataset(parent, model, idx, name)
        except Exception as exc:
            logger.warning("DECLARE_SUBSET: declareSubDataset failed: %s", exc)

    async def _on_request_subdataset_arrays(self, fingerprint, **kwargs) -> None:
        """Serialize SubDataset arrays and push them onto the outbound queue.

        Supports both uniform datasets (R shape: Nxnatomsx3) and variable
        datasets (molecules of different sizes, stored as flat arrays + offsets).
        """
        from ffast.protocol.rpc import pack_arrays

        dataset = self.env.datasets.get(fingerprint)
        if dataset is None:
            logger.warning(
                "REQUEST_SUBDATASET_ARRAYS: fingerprint %r not found", fingerprint
            )
            return

        is_variable = bool(getattr(dataset, "isVariable", False))
        logger.info(
            "Sending arrays for dataset %r (n=%d, variable=%s) to client",
            fingerprint, dataset.getN(), is_variable,
        )

        # Offload to a thread: to_transfer_arrays() + pack_arrays() call
        # np.ascontiguousarray / .tobytes() / msgpack.packb() — all synchronous
        # CPU/memory operations that can take seconds for large datasets.
        # Keeping them on the event loop blocks WebSocket ping handling and
        # causes the websockets library to close after ping_timeout (20 s).
        arrays = await asyncio.to_thread(dataset.to_transfer_arrays)

        # ── Include cached prediction data for this dataset ──────────────────
        # Pack prediction arrays as "pred__<dtype>__<model_fp>" entries so the
        # client can reconstruct DataEntity objects and show ghost models in the
        # sidebar without a separate round-trip.
        model_names: dict = {}
        pred_count = 0
        from ffast.cache import CacheKey, PredictionArrayKey
        for cache_key in list(self.env.cache.keys()):
            ck = CacheKey.try_parse(cache_key)
            if ck is None or ck.dtype not in ("energy", "forces"):
                continue
            if ck.dataset_fp != fingerprint:
                continue
            dt_key, model_fp = ck.dtype, ck.model_fp
            de = self.env.cache.get(cache_key)
            if de is None:
                continue
            raw = de.get(dt_key)
            if raw is None:
                continue

            # Variable-dataset forces arrive as a list of (natoms_i, 3) arrays;
            # flatten to (total_atoms, 3) — client rebuilds per-molecule slices
            # using the already-transferred offsets.
            if isinstance(raw, list):
                try:
                    raw = np.concatenate(raw, axis=0)
                except Exception as exc:
                    logger.warning(
                        "Could not concatenate prediction %s for %r: %s",
                        cache_key, fingerprint, exc,
                    )
                    continue

            arrays[PredictionArrayKey(dt_key, model_fp).format()] = np.asarray(raw)
            pred_count += 1

        # Collect human-readable model names for all models whose prediction
        # data was included above.
        for model_fp, model in self.env.models.items():
            model_names[model_fp] = getattr(model, "name", model_fp[:8]) or model_fp[:8]

        if pred_count:
            logger.info(
                "Including %d prediction arrays for %d model(s) with dataset %r",
                pred_count, len({
                    PredictionArrayKey.parse(k).model_fp
                    for k in arrays if PredictionArrayKey.is_prediction_key(k)
                }),
                fingerprint,
            )

        data = await asyncio.to_thread(pack_arrays, fingerprint, arrays, model_names=model_names)
        await self._emit(data)
        logger.info("Arrays for %r queued (%d bytes)", fingerprint, len(data))

    async def _on_probe_dataset_keys(self, path, dataset_type, **kwargs) -> None:
        """Probe first frame of an ASE file and push DATASET_KEYS_RESPONSE.

        Uses the same key-detection logic as the local
        _showASEKeySelectionDialog so the client displays an identical dialog.
        """
        from ffast.protocol.rpc import pack

        energy_keys: list = []
        force_keys: list = []
        has_calculator_energy = False
        has_calculator_forces = False
        error: str | None = None

        try:
            from ffast.io.xyz import read_ase_or_explain
            from ffast.loaders.ase import aseDatasetLoader

            first_atoms = read_ase_or_explain(path, index=0)
            temp_loader = aseDatasetLoader(path, atomsList=[first_atoms])
            energy_keys = list(temp_loader.EneregyKeys())
            force_keys = list(temp_loader.ForceKeys())

            try:
                first_atoms.get_potential_energy()
                has_calculator_energy = True
            except Exception:
                pass
            try:
                first_atoms.get_forces()
                has_calculator_forces = True
            except Exception:
                pass

            logger.info(
                "PROBE_DATASET_KEYS %r: energy_keys=%r force_keys=%r",
                path, energy_keys, force_keys,
            )
        except Exception as exc:
            logger.warning("PROBE_DATASET_KEYS error for %r: %s", path, exc)
            logger.warning(
                "  server view: host=%s cwd=%s exists=%s parent_exists=%s",
                *_probe_path_diagnostics(path),
            )
            error = str(exc)

        from ffast.protocol import DatasetKeysResponse
        data = pack(
            control.DATASET_KEYS_RESPONSE,
            (path,),
            DatasetKeysResponse(
                energy_keys=energy_keys,
                force_keys=force_keys,
                has_calculator_energy=has_calculator_energy,
                has_calculator_forces=has_calculator_forces,
                error=error,
            ).model_dump(),
        )
        await self._emit(data)
        logger.debug("DATASET_KEYS_RESPONSE queued for %r", path)

    async def _on_probe_dataset_length(self, path, **kwargs) -> None:
        """Count frames in a dataset file and push DATASET_LENGTH_RESPONSE."""
        from ffast.protocol.rpc import pack

        n: int | None = None
        error: str | None = None
        try:
            from ffast.core.data_types import AtomsList
            n = AtomsList.calc_dataset_length_static(path)
            logger.info("PROBE_DATASET_LENGTH %r: n=%d", path, n)
        except Exception as exc:
            logger.warning("PROBE_DATASET_LENGTH error for %r: %s", path, exc)
            logger.warning(
                "  server view: host=%s cwd=%s exists=%s parent_exists=%s",
                *_probe_path_diagnostics(path),
            )
            error = str(exc)

        from ffast.protocol import DatasetLengthResponse
        data = pack(
            control.DATASET_LENGTH_RESPONSE,
            (path,),
            DatasetLengthResponse(n=n, error=error).model_dump(),
        )
        await self._emit(data)
        logger.debug("DATASET_LENGTH_RESPONSE queued for %r", path)

    async def _on_list_dir(self, path=None, **kwargs) -> None:
        """List a server-side directory and push DIR_LISTING.

        Full-filesystem browse backing the web file picker. An empty/None
        ``path`` starts at the server user's home directory. Each entry is
        ``{name, is_dir, size}``; directories sort first, then files, both
        case-insensitive. ``parent`` is None at the filesystem root.
        """
        import os
        from ffast.protocol.rpc import pack

        home = os.path.expanduser("~")
        abspath = os.path.abspath(os.path.expanduser(path)) if path else home

        entries: list = []
        error: str | None = None
        try:
            with os.scandir(abspath) as it:
                for de in it:
                    try:
                        is_dir = de.is_dir()
                        size = 0 if is_dir else de.stat(follow_symlinks=False).st_size
                    except OSError:
                        # Broken symlink / unreadable — list name, treat as file
                        is_dir, size = False, 0
                    entries.append({"name": de.name, "is_dir": is_dir, "size": size})
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
            logger.info("LIST_DIR %r: %d entries", abspath, len(entries))
        except Exception as exc:
            logger.warning("LIST_DIR error for %r: %s", abspath, exc)
            error = str(exc)

        parent = os.path.dirname(abspath)
        if parent == abspath:
            parent = None  # filesystem root has no parent

        from ffast.protocol import DirListing
        # Echo the *requested* path as a second positional arg so the desktop
        # client can correlate the reply to its request (the abspath differs
        # from the request for None→home or relative inputs). The web client
        # reads only kwargs and ignores the extra positional.
        data = pack(
            control.DIR_LISTING,
            (abspath, path),
            DirListing(
                path=abspath,
                parent=parent,
                home=home,
                entries=entries,
                error=error,
            ).model_dump(),
        )
        await self._emit(data)
        logger.debug("DIR_LISTING queued for %r", abspath)

    async def _on_load_prediction(self, path, dataset_fp, **kwargs) -> None:
        selected_energy_key = kwargs.get("selected_energy_key")
        selected_force_key = kwargs.get("selected_force_key")
        logger.info(
            "LOAD_PREDICTION: path=%r dataset=%r energy_key=%r force_key=%r",
            path, dataset_fp[:8], selected_energy_key, selected_force_key,
        )
        self.env.taskLoadPrepredictedDataset(
            path, dataset_fp,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
        )

    async def _on_request_prediction_arrays(self, dataset_fp, model_fp, **kwargs) -> None:
        """Pack only cached prediction arrays for (dataset_fp, model_fp) and push.

        Uses the Prediction-Only Array Channel — geometry/element arrays are NOT
        re-sent. Replies with a ``PREDICTION_ARRAYS`` event so the client
        listener resolves its pending Future without treating it as a geometry
        transfer.
        """
        from ffast.protocol.rpc import pack_prediction_arrays
        from ffast.cache import CacheKey, PredictionArrayKey

        # Stage 2: generate predictions on demand for real (non-ghost)
        # server-side models. Ghost models only carry file-loaded predictions; a
        # real model can predict any dataset, so compute + cache missing entries
        # before serving. model.predict is CPU/GPU-heavy — run off the event
        # loop so WebSocket pings keep flowing (avoids the ping_timeout drop).
        model = self.env.models.get(model_fp)
        dataset = self.env.datasets.get(dataset_fp)
        if (model is not None and dataset is not None
                and not getattr(model, "isGhost", False)):
            for dt_key in ("energy", "forces"):
                if self.env.cache.get(CacheKey(dt_key, model_fp, dataset_fp).format()) is None:
                    try:
                        await asyncio.to_thread(
                            self.env.data.generateData, dt_key, model, dataset
                        )
                    except Exception as exc:
                        logger.error(
                            "On-demand prediction gen failed (%s) model=%r dataset=%r: %s",
                            dt_key, model_fp[:8], dataset_fp[:8], exc,
                        )

        arrays = {}
        for dt_key in ("energy", "forces"):
            cache_key = CacheKey(dt_key, model_fp, dataset_fp).format()
            de = self.env.cache.get(cache_key)
            if de is None:
                continue
            raw = de.get(dt_key)
            if raw is None:
                continue
            # Variable-dataset forces arrive as list of (natoms_i, 3) arrays;
            # flatten to (total_atoms, 3) — client rebuilds per-molecule slices
            # using the already-held offsets.
            if isinstance(raw, list):
                try:
                    raw = np.concatenate(raw, axis=0)
                except Exception as exc:
                    logger.warning(
                        "_send_prediction_arrays: could not concatenate %s: %s",
                        cache_key, exc,
                    )
                    continue
            arrays[PredictionArrayKey(dt_key, model_fp).format()] = np.asarray(raw)

        if not arrays:
            logger.warning(
                "_send_prediction_arrays: no cache entries for model=%r dataset=%r",
                model_fp[:8], dataset_fp[:8],
            )

        data = await asyncio.to_thread(pack_prediction_arrays, dataset_fp, model_fp, arrays)
        await self._emit(data)
        logger.info(
            "PREDICTION_ARRAYS queued: model=%r dataset=%r keys=%r",
            model_fp[:8], dataset_fp[:8], list(arrays.keys()),
        )

    async def _on_open_view(self, **kwargs) -> None:
        """Create or reopen a VisualizationView and send its SCENE_SNAPSHOT."""
        import uuid
        from ffast.protocol.rpc import pack
        from ffast.visualization.view import VisualizationView

        view_id = kwargs.get("view_id") or str(uuid.uuid4())
        dataset_ref = kwargs.get("dataset_ref") or None

        if view_id not in self.views:
            view = VisualizationView(view_id)
            self.views[view_id] = view
        else:
            view = self.views[view_id]

        if dataset_ref is not None:
            view.state.dataset_ref = dataset_ref

        # prediction_ref is the ghost-model fingerprint; presence of the key
        # lets the client both set (fp) and clear (null) the prediction overlay.
        if "prediction_ref" in kwargs:
            view.state.prediction_ref = kwargs.get("prediction_ref") or None

        # ADR 0044 Phase 3: the referenced dataset/model may have been deleted
        # by another connection between OPEN_VIEW arriving and the snapshot
        # build (delete race) — degrade to a bare (camera-only) snapshot
        # instead of raising, mirroring _on_view_command's guard.
        try:
            snapshot = view.snapshot(
                get_dataset=self.env.datasets.get,
                get_prediction=self.get_prediction,
                get_forces=self.get_forces,
                executor=self.env.data.metricExecutor,
            )
        except Exception as exc:
            self._log_scene_degrade("OPEN_VIEW", view_id, exc)
            snapshot = view.snapshot()
        data = pack(control.SCENE_SNAPSHOT, [], snapshot.model_dump())
        await self._emit(data)
        logger.info(
            "OPEN_VIEW: view_id=%r dataset_ref=%r prediction_ref=%r scene_version=%d",
            view_id, dataset_ref, view.state.prediction_ref, view.version,
        )

    async def _on_close_view(self, view_id, **kwargs) -> None:
        self.views.pop(view_id, None)
        logger.info("CLOSE_VIEW: view_id=%r", view_id)

    async def _on_view_command(self, **kwargs) -> None:
        """Apply a ViewCommand and send COMMAND_RESULT plus an optional SCENE_PATCH."""
        from pydantic import TypeAdapter, ValidationError
        from ffast.protocol.rpc import pack
        from ffast.visualization.commands import ViewCommand
        from ffast.visualization.scene_builder import build_scene, fill_patch_from_scene

        ta = TypeAdapter(ViewCommand)
        try:
            cmd = ta.validate_python(kwargs)
        except (ValidationError, Exception) as exc:
            logger.warning("VIEW_COMMAND parse error: %s — kwargs=%r", exc, kwargs)
            return

        view = self.views.get(cmd.view_id)
        if view is None:
            logger.warning("VIEW_COMMAND for unknown view_id=%r", cmd.view_id)
            return

        result = view.apply_command(cmd)

        # Rebuild scene components that changed. build_scene is synchronous and
        # may block while the worker-process executor communicates with its
        # subprocess, so run it in a thread to keep the event loop responsive.
        #
        # ADR 0044 Phase 3 (delete race): another connection may have deleted
        # this view's dataset/model between the command arriving and the
        # rebuild running. build_scene itself already degrades to a bare scene
        # when get_dataset returns None; this guards against anything deeper
        # in the pipeline (e.g. a colour-by metric referencing a just-deleted
        # model) raising instead — the view degrades to an empty patch rather
        # than the COMMAND_RESULT/SCENE_PATCH never reaching the client.
        if result.success and result.patch and result.patch.changed:
            view_state = view.state
            only_forces_selected = "only_forces" in view_state.enabled_features
            try:
                scene = await asyncio.to_thread(
                    build_scene, view_state, self.env.datasets.get,
                    self.get_prediction, self.get_forces,
                    self.env.data.metricExecutor,
                )
            except Exception as exc:
                self._log_scene_degrade("VIEW_COMMAND", cmd.view_id, exc)
                from ffast.visualization.scene import RenderScene
                scene = RenderScene(
                    view_id=view_state.view_id,
                    version=view_state.version,
                    camera=view_state.camera,
                )
            fill_patch_from_scene(result.patch, scene)
            if only_forces_selected:
                result.patch.changed.add("only_forces")
                print("server says yes!")

        result_data = pack(control.COMMAND_RESULT, [], result.model_dump())
        await self._emit(result_data)

        if result.success and result.patch:
            patch_data = pack(control.SCENE_PATCH, [], result.patch.model_dump())
            await self._emit(patch_data)

        logger.debug(
            "VIEW_COMMAND %s → success=%s new_version=%d",
            kwargs.get("type", "?"), result.success, result.new_version,
        )

    async def _on_request_state_sync(self, **kwargs) -> None:
        # Client explicitly requests a full state replay (e.g. after reconnect).
        # The server also replays state automatically on every new connection
        # (see server._handler), so this is a fallback for explicit re-sync.
        logger.info("REQUEST_STATE_SYNC received — replaying state to client")
        self.replay()

    async def _on_save_session(self, path, **kwargs) -> None:
        # Stage 5: save runs on the server, which owns the real datasets +
        # prediction cache. Reuses the env task manager (same path as loads).
        # The web client's path prompt may send "~/…" or a relative path
        # (it has no server file dialog), so expand it here — a no-op on the
        # absolute paths the Qt file dialog produces (ADR 0045 Phase 4).
        self._queue_session_op("save", path)

    async def _on_load_session(self, path, **kwargs) -> None:
        # Server restores its Environment (datasets in-process + prediction
        # cache); DATASET_LOADED / MODEL_LOADED subscribers announce them to the
        # client via REMOTE_DATASET_META / REMOTE_MODEL_META.
        self._queue_session_op("load", path)

    def _queue_session_op(self, kind: str, path: str) -> None:
        """Queue a session save/load and announce its outcome when it finishes.

        The task stays (``visual=True``, so both clients keep their Tasks-panel
        entry) but its completion is now also reported as ``SESSION_SAVED`` /
        ``SESSION_LOADED`` carrying ``{ok, path, error}`` — ADR 0050.
        ``TASK_DONE`` carries only a task id, and a client never learns which id
        its own request produced, so a browser waiting on a save could not tell
        its completion from an unrelated dataset load's and reported whichever
        task finished first as its own.

        The work runs in a task worker thread, so the ack is handed back to this
        session's event loop rather than enqueued directly — ``asyncio.Queue``
        is not thread-safe.
        """
        import os

        loop = asyncio.get_running_loop()
        path = os.path.abspath(os.path.expanduser(path))
        saving = kind == "save"
        event = control.SESSION_SAVED if saving else control.SESSION_LOADED
        run = self.env.persistence.save if saving else self.env.persistence.load

        def _work(taskID=None):
            try:
                run(path, taskID=taskID)
            except Exception as exc:
                logger.warning("%s session failed for %r: %s", kind, path, exc)
                self._ack_from_thread(loop, event, False, path, error=str(exc))
                raise
            self._ack_from_thread(loop, event, True, path)

        self.env.newTask(
            _work,
            visual=True,
            # Unchanged task names: the desktop Tasks panel shows these strings.
            name="Saving session" if saving else "Loading save",
            threaded=True,
        )

    def _ack_from_thread(self, loop, event, ok, path, error=None) -> None:
        """Hand a session-op ack from a task worker thread onto ``loop``."""
        from ffast.protocol.rpc import pack

        data = pack(event, (), {"ok": bool(ok), "path": path, "error": error})
        loop.call_soon_threadsafe(self._emit_or_drop, data, f"session ack {event}")

    async def _on_export_subset(self, fingerprint, path, **kwargs) -> None:
        """Write a dataset out to an extxyz server-side (ADR 0045 issue 20).

        The browser has no local files, so the export is a server-side write:
        we resolve the dataset by fingerprint, pick the ASE serializer matching
        its uniform/variable shape (the same static ``saveDataset`` the Qt
        "save dataset" path uses), write it off the event loop, and report the
        resolved path — or an error — back over ``SUBSET_EXPORTED``. Any loaded
        dataset is valid; the common cases are a pick-derived
        ``AtomFilteredDataset`` and a plot-derived ``SubDataset``.
        """
        import os
        from ffast.protocol.rpc import pack

        async def _reply(ok: bool, resolved: str, error: str | None = None, n: int = 0):
            data = pack(
                control.SUBSET_EXPORTED, (),
                {"ok": ok, "path": resolved, "error": error, "n": n},
            )
            await self._emit(data)

        resolved = os.path.abspath(os.path.expanduser(path))
        dataset = self.env.datasets.get(fingerprint)
        if dataset is None:
            logger.warning("EXPORT_SUBSET: dataset %r not found", fingerprint)
            await _reply(False, resolved, error="dataset not found")
            return

        fmt = kwargs.get("format")
        if fmt is None:
            ext = os.path.splitext(resolved)[1].lower()
            # .xyz too → extxyz so energies/forces survive (plain xyz drops them).
            fmt = "extxyz" if ext in (".xyz", ".extxyz", "") else None

        # Uniform vs variable picks the serializer, exactly as the desktop
        # SideBar export does. A SubDataset forwards its parent's isVariable;
        # an AtomFilteredDataset is atom-filtering, always the uniform path.
        from ffast.loaders.ase import (
            VariableASEDatasetLoader, aseDatasetLoader,
        )
        is_variable = bool(getattr(dataset, "isVariable", False))
        saver = VariableASEDatasetLoader if is_variable else aseDatasetLoader

        try:
            parent = os.path.dirname(resolved)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            await asyncio.to_thread(saver.saveDataset, dataset, resolved, fmt)
            n = int(dataset.getN())
            logger.info("EXPORT_SUBSET: wrote %d structure(s) → %s", n, resolved)
            await _reply(True, resolved, n=n)
        except Exception as exc:
            logger.warning("EXPORT_SUBSET: write failed for %r: %s", resolved, exc)
            await _reply(False, resolved, error=str(exc))

    async def _on_request_metric(self, metric_id, key=None, **kwargs) -> None:
        """Compute a metric server-side and reply with METRIC_RESULT (Stage 4a).

        Reuses the same in-process metric spine the client has — the server owns
        the full dataset arrays and ghost/remote predictions, so plots no longer
        need the client to hold source arrays. Replies ok=False (client falls
        back to in-process compute) when the metric needs a model the server
        doesn't have (a real client-only model).
        """
        from ffast.protocol.rpc import pack_metric_result

        params = kwargs.get("params") or {}
        model_fp = kwargs.get("model_fp")
        dataset_fp = kwargs.get("dataset_fp")

        model = self.env.models.get(model_fp) if model_fp else None
        dataset = self.env.datasets.get(dataset_fp) if dataset_fp else None
        if key is None:
            key = self.env.data.make_metric_cache_key(metric_id, params, model, dataset)

        result = self.env.cache.get(key)

        # A model-dependent metric whose model isn't on the server (a real,
        # client-only model) can't be computed here — signal fallback.
        server_can_compute = not (model_fp and model is None)

        if result is None and server_can_compute:
            try:
                loop = asyncio.get_running_loop()
                ok = await loop.run_in_executor(
                    _metric_compute_pool(),
                    self.env.data.generateMetric, metric_id, params, model, dataset, key,
                )
            except Exception as exc:
                logger.error("REQUEST_METRIC compute failed %s: %s", metric_id, exc)
                ok = False
            result = self.env.cache.get(key) if ok else None

        data = pack_metric_result(key, metric_id, result is not None, result)
        await self._emit(data)
        logger.info(
            "METRIC_RESULT queued: metric=%r ok=%s key=%r",
            metric_id, result is not None, key,
        )

    async def _on_request_metric_catalog(self, **kwargs) -> None:
        self._replay_metric_catalog()

    async def _on_request_tab_layout(self, **kwargs) -> None:
        """Reply with TAB_LAYOUT: the merged Analysis-Tab layout (ADR 0045
        Phase 3). Mirrors ``_on_request_metric_catalog`` — a dedicated
        announcement, not a correlated reply. The layout is resolved once at
        server startup and cached on the Environment (``analysis_tab_layout``);
        if it isn't there (e.g. a unit test with a bare env), fall back to the
        bundled tabs so the built-in analyses are always available."""
        from ffast.protocol.rpc import pack
        try:
            from ffast.protocol import TabLayout
            tabs = getattr(self.env, "analysis_tab_layout", None)
            if tabs is None:
                from ffast.config.tabs import build_tab_layout, merge_tabs
                tabs = build_tab_layout(merge_tabs())
            data = pack(control.TAB_LAYOUT, [], TabLayout(tabs=tabs).model_dump())
            await self._emit(data)
            logger.info("TAB_LAYOUT queued (%d tab(s))", len(tabs))
        except Exception as exc:
            logger.warning("TAB_LAYOUT error: %s", exc)
