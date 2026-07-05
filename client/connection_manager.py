"""Remote/local server session management for the Environment (ADR 0020).

``ConnectionManager`` owns everything about talking to an ``ffast-server``:
the connection lifecycle (cluster via SLURM/SSH, direct, and the managed local
server), the server→client metadata handlers that materialise proxy datasets and
ghost models, and the synchronous/async array+metric fetch channels.

It owns the session *state* (``serverConnection``, ``_event_loop``, the managed
local-server handles, ``metricCatalog``).  Everything domain-level it needs —
creating datasets/models, pushing events, reading the cache, queuing tasks — it
reaches through the owning ``Environment`` via the same-named delegators below,
so the method bodies are the former ``Environment`` methods unchanged.

Per ADR 0020 the dependency points one way: the manager reads/writes session
state and *announces* results through the event bus (the registries' handlers,
exposed here as ``_onRemote*Meta``, listen and build the objects).  ``DataService``
never reaches in here directly — it only sees a ``PredictionSource`` that wraps
this manager's ``_fetch*Sync`` methods.
"""

import asyncio
import logging

import numpy as np

from ffast.protocol import control

logger = logging.getLogger("FFAST")


class ConnectionManager:
    """Owns the ffast-server session lifecycle and transport (ADR 0020)."""

    def __init__(self, env):
        self._env = env

        # Active remote cluster session (set by connectToCluster).
        self.serverConnection = None
        # Set to True by _disconnectServerConnection so connectToCluster's
        # CancelledError handler knows not to scancel or delete the record.
        self._connection_quitting = False

        # Managed local server (set by main.py via startLocalServer).
        self.localServerConnection = None
        self.localServerHandle = None
        self.localServerManager = None
        self._localServerListener = None
        self._event_loop = None

        # Server-owned metric catalog (ADR 0016): the source of truth for which
        # metrics exist (incl. config-loaded external ones).
        self.metricCatalog = {}

    # ── env-domain delegators: keep the moved method bodies verbatim ──────
    def eventPush(self, *args, **kwargs):
        return self._env.eventPush(*args, **kwargs)

    def newTask(self, *args, **kwargs):
        return self._env.newTask(*args, **kwargs)

    def getDataType(self, dataTypeKey):
        return self._env.data.getDataType(dataTypeKey)

    def getDataset(self, key):
        return self._env.datasets.get(key)

    def getModel(self, key):
        return self._env.models.get(key)

    def setData(self, *args, **kwargs):
        return self._env.data.setData(*args, **kwargs)

    def setNewDataset(self, *args, **kwargs):
        return self._env.datasets.add(*args, **kwargs)

    def setNewModel(self, *args, **kwargs):
        return self._env.models.add(*args, **kwargs)

    def lookForGhosts(self):
        return self._env.lookForGhosts()

    @property
    def tm(self):
        return self._env.tm

    @property
    def cache(self):
        return self._env.cache

    @property
    def objects(self):
        return self._env.objects

    # ── local-server push ─────────────────────────────────────────────────
    def _sync_to_local_server(self, event, *args, **kwargs):
        """Thread-safe push to the managed local server.

        May be called from task worker threads; uses run_coroutine_threadsafe
        so the coroutine runs on the main async event loop.
        """
        if self.localServerConnection is None or self._event_loop is None:
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            self.localServerConnection.push_event(event, *args, **kwargs),
            self._event_loop,
        )

    # ── connection lifecycle ──────────────────────────────────────────────
    async def _disconnectServerConnection(self):
        """Disconnect any active remote session on QUIT_EVENT."""
        session = self.serverConnection
        if session is not None:
            # Set flag BEFORE disconnecting so that when TaskManager
            # subsequently cancels the connectToCluster task the CancelledError
            # handler knows this is a quit, not a user-initiated disconnect.
            # Without this the handler would scancel the job and delete the
            # session record, preventing reconnect on next launch.
            self._connection_quitting = True
            logger.info("Cleaning up remote session on quit…")
            await session.disconnect()
            self.serverConnection = None

    async def connectToCluster(
        self, profile, reconnect_job_id=None, taskID=None
    ):
        """Connect to (or reconnect to) a remote cluster session.

        Owns the full session lifecycle: SLURM job dispatch, SSH tunnel,
        WebSocket, listener startup, and clean teardown.  The UI layer
        (menuHandler) is responsible only for the dialog and profile
        selection; all session state lives here.
        """
        from cluster.backend import ClusterError

        _job_id = reconnect_job_id

        def _on_job_submitted(job_id: str):
            nonlocal _job_id
            _job_id = job_id

        def _progress(msg: str):
            prefix = f"[Job {_job_id}] " if _job_id else ""
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                message=f"{prefix}{msg}",
            )

        async def _scancel(job_id: str):
            """Best-effort SLURM job cancellation (new jobs only)."""
            from cluster.slurm import RemoteSlurmBackend
            try:
                backend = RemoteSlurmBackend(
                    host=profile.host,
                    username=profile.username,
                    identity_file=profile.identity_file,
                )
                await backend.cancel_job(job_id)
                logger.info("scancel %s succeeded", job_id)
            except Exception as exc:
                logger.warning("scancel %s failed: %s", job_id, exc)

        try:
            if reconnect_job_id is not None:
                from cluster.connection import reconnect_to_cluster
                session = await reconnect_to_cluster(
                    profile,
                    reconnect_job_id,
                    progress_cb=_progress,
                )
            else:
                from cluster.connection import connect_to_cluster
                session = await connect_to_cluster(
                    profile,
                    progress_cb=_progress,
                    on_job_submitted=_on_job_submitted,
                )

            self.serverConnection = session
            # Forward server→client events into the local event system.
            # For reconnect sessions the server replays REMOTE_DATASET_META
            # and REMOTE_MODEL_META automatically on connection, so the
            # listener will receive and forward them as normal.
            listener = await session.start_listener(self._env)
            logger.info(
                "Remote session ready: job=%s local_port=%d",
                session.job_id,
                session.local_port,
            )
            self.eventPush("REMOTE_CONNECTED")

            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                title=f"Cluster: {profile.host} [{session.job_id}]",
                message="Connected — ✕ to disconnect",
            )

            # Keep task alive so the sidebar item stays as a
            # disconnect handle. ListenerHandle.wait_done() always raises
            # CancelledError to the caller (see its docstring for why this
            # matters over awaiting the task directly).
            try:
                await listener.wait_done()
                # Natural exit — listener died (server dropped).
                # Keep session record: user can still reconnect if the
                # job is still alive (e.g. transient tunnel failure).
                logger.warning(
                    "Cluster listener exited unexpectedly (job %s)",
                    session.job_id,
                )
                self.eventPush(
                    "TASK_PROGRESS",
                    taskID,
                    message="Connection lost (server dropped)",
                    error=True,
                )
                self.serverConnection = None
            except asyncio.CancelledError:
                if self._connection_quitting:
                    # App is quitting — _disconnectServerConnection already
                    # closed the tunnel.  Job is still alive on the cluster;
                    # keep the session record so the user can reconnect on the
                    # next launch.  Do NOT scancel.
                    raise
                # ✕ clicked — disconnect cleanly.
                logger.info(
                    "Disconnecting from cluster (user request, job %s)",
                    session.job_id,
                )
                listener.cancel()
                await session.disconnect()
                self.serverConnection = None
                if reconnect_job_id is None:
                    # New job submitted by us — cancel it on the cluster.
                    asyncio.create_task(_scancel(session.job_id))
                # Delete local record: user explicitly disconnected.
                from cluster.connection import delete_session_record
                delete_session_record(session.job_id)
                raise

        except asyncio.CancelledError:
            # Cancelled before connection was established.
            if _job_id is not None and reconnect_job_id is None and not self._connection_quitting:
                logger.info(
                    "Task cancelled — sending scancel for job %s",
                    _job_id,
                )
                asyncio.create_task(_scancel(_job_id))
            raise
        except ClusterError as exc:
            logger.error("Cluster connection failed: %s", exc)
            if getattr(exc, "stderr", ""):
                logger.error("  remote stderr:\n%s", exc.stderr)
            await self._log_provision_diagnostics(profile, _job_id)
            # Job is definitively dead — purge the stale record so the
            # reconnect dialog doesn't re-appear on the next connect attempt.
            if reconnect_job_id is not None:
                from cluster.connection import delete_session_record
                delete_session_record(reconnect_job_id)
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                message=f"Connection failed: {exc}",
                error=True,
            )
        except OSError as exc:
            logger.error("SSH/WebSocket error: %s", exc)
            await self._log_provision_diagnostics(profile, _job_id)
            # If we submitted a new job but the tunnel/WebSocket failed,
            # cancel it on the cluster so it doesn't run to its time limit.
            # For reconnect jobs we leave the job alone (tunnel failure is
            # transient; the job is still running and the record is kept).
            if reconnect_job_id is None and _job_id is not None:
                asyncio.create_task(_scancel(_job_id))
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                message=f"Connection failed: {exc}",
                error=True,
            )
        except Exception as exc:  # never swallow — log the full traceback
            logger.exception("Unexpected error during cluster connect")
            await self._log_provision_diagnostics(profile, _job_id)
            if reconnect_job_id is None and _job_id is not None:
                asyncio.create_task(_scancel(_job_id))
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                message=f"Connection failed: {exc}",
                error=True,
            )

    async def _log_provision_diagnostics(self, profile, job_id):
        """On a provision-enabled connect failure, fetch + log the SLURM job log.

        Provisioning runs inside the job, so a failure there (bad module, pip
        error on the compute node) lives in the job log, not a client exception.
        Best-effort — never raises."""
        if not getattr(profile, "provision", False) or not job_id:
            return
        try:
            from cluster.bootstrap import tail_job_log
            log = await tail_job_log(profile, job_id)
            if log:
                logger.error("SLURM job %s log — provisioning diagnostics:\n%s",
                             job_id, log)
            else:
                logger.error(
                    "SLURM job %s: no log found (~/slurm-%s.{out,err}); "
                    "check the cluster for provisioning output.", job_id, job_id)
        except Exception as diag_exc:
            logger.warning("Could not fetch SLURM job log for %s: %s", job_id, diag_exc)

    async def connectDirect(self, host, port, taskID=None):
        """Connect directly to a local ffast-server (no SLURM/SSH).

        Used for local testing without a cluster.  menuHandler is
        responsible for the input dialog; this method owns the session
        lifecycle.
        """
        from cluster.connection import connect_direct

        self.eventPush(
            "TASK_PROGRESS",
            taskID,
            message=f"Connecting to {host}:{port}…",
        )
        try:
            session = await connect_direct(host, port)
            self.serverConnection = session
            listener = await session.start_listener(self._env)
            logger.info("Local server connected: %s:%d", host, port)
            self.eventPush("REMOTE_CONNECTED")
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                title=f"Local server {host}:{port}",
                message="Connected — ✕ to disconnect",
            )

            try:
                await listener.wait_done()
                self.eventPush(
                    "TASK_PROGRESS",
                    taskID,
                    message="Connection lost (server stopped)",
                    error=True,
                )
                self.serverConnection = None
            except asyncio.CancelledError:
                listener.cancel()
                await session.disconnect()
                self.serverConnection = None
                raise

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Local server connect failed: %s", exc)
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                message=f"Failed: {exc}",
                error=True,
            )

    async def startLocalServer(self, taskID=None):
        """Auto-start a managed local ffast-server subprocess and connect to it.

        Called at app launch via tm.newTask so the connection is visible in the
        task progress UI (ADR 0017-desktop).  On success sets serverConnection and
        fires REMOTE_CONNECTED; on failure the task ends with an error message
        and the app continues without a local server.
        """
        import socket
        import sys
        import os
        from ffast.session.local import LocalServerManager
        from ffast.session.token import SessionToken
        from cluster.connection import connect_direct

        self.eventPush("TASK_PROGRESS", taskID, message="Starting local server…")

        bin_dir = os.path.dirname(sys.executable)
        server_exe = os.path.join(bin_dir, "ffast-server")
        if not os.path.isfile(server_exe):
            server_exe = "ffast-server"

        with socket.socket() as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]

        token = SessionToken.generate()
        manager = LocalServerManager()
        handle = manager.start(port, token)

        for delay in (0.3, 0.5, 0.8, 1.0, 1.5):
            import asyncio
            await asyncio.sleep(delay)
            if handle.process.poll() is not None:
                self.eventPush(
                    "TASK_PROGRESS", taskID,
                    message="Local server process exited during startup",
                    error=True,
                )
                return
            try:
                session = await connect_direct(port=port, token=token.plaintext)
            except Exception:
                continue

            self.localServerConnection = session
            self.serverConnection = session   # ADR 0017-desktop: local IS remote
            session.is_local = True        # same-machine ⇒ eager-populate proxies
            self.localServerHandle = handle
            self.localServerManager = manager
            self._localServerListener = await session.start_listener(self._env)
            self.eventPush("REMOTE_CONNECTED")
            self.eventPush(
                "TASK_PROGRESS", taskID,
                title="Local server",
                message="Running — ✕ to stop",
            )
            logger.info("Local server ready on port %d", port)

            await self._localServerListener.wait_done()
            self.eventPush(
                "TASK_PROGRESS", taskID,
                message="Local server stopped",
                error=True,
            )
            return

        manager.stop(handle)
        self.eventPush(
            "TASK_PROGRESS", taskID,
            message="Local server did not become ready",
            error=True,
        )

    # ── remote array transfer ─────────────────────────────────────────────
    def _onRemoteDatasetMeta(
        self,
        fingerprint,
        name=None,
        n=None,
        has_forces=True,
        is_sub=False,
        variable=False,
        elements=None,
        offsets=None,
        path=None,
        source_type=None,
    ):
        """Create a local CachedRemoteDataset proxy when the server loads a dataset.

        Stage 4c: the proxy is created LAZY — only cheap metadata (per-atom
        elements + molecule offsets) is applied here, so element labels,
        atom-filter, and scatter sub-indexing work on the main thread without a
        transfer.  The big R/F/E arrays are fetched on demand the first time a
        worker-thread consumer (export, prediction, in-process metric fallback)
        reads them (see CachedRemoteDataset._ensure_arrays).  The common flow
        (plots via server metrics, Loupe via server scenes) transfers nothing.
        """
        from cluster.remote_dataset import CachedRemoteDataset
        from ffast.protocol import DatasetMeta
        from pydantic import ValidationError

        if self.getDataset(fingerprint) is not None:
            logger.debug("REMOTE_DATASET_META: proxy already exists for %r", fingerprint)
            return

        # Slice 3: validate the announcement against the typed transport contract
        # (ffast.protocol.DatasetMeta) instead of trusting raw kwargs. Same wire
        # shape as DatasetLoader.toMetaDict, so behaviour is unchanged for valid
        # payloads; a malformed one is logged and dropped rather than half-creating
        # a proxy.
        try:
            meta = DatasetMeta(
                name=name, n=n, has_forces=has_forces, is_sub=is_sub,
                variable=variable, elements=elements, offsets=offsets,
                path=path, source_type=source_type,
            )
        except ValidationError as exc:
            logger.warning(
                "REMOTE_DATASET_META: invalid metadata for %r: %s", fingerprint, exc
            )
            return

        n_val = int(meta.n) if meta.n is not None else 0
        label = meta.name if meta.name else fingerprint[:12]
        proxy = CachedRemoteDataset(fingerprint, label, n_val)
        proxy.env = self._env  # needed by _ensure_arrays for lazy on-demand fetch (4c)
        if meta.path:
            proxy.path = meta.path  # real source path (server-side) so session save/load round-trips
        proxy._source_type = meta.source_type  # loader type, persisted for server-routed restore (Stage 5)
        proxy.apply_metadata(
            elements=meta.elements, offsets=meta.offsets, is_variable=bool(meta.variable)
        )
        # slice_num=-2 skips the maxDatasetSize update (proxy has no big arrays)
        self.setNewDataset(proxy, slice_num=-2)
        logger.info(
            "Remote proxy created (lazy): %r (n=%d, variable=%s, has_forces=%s)",
            label, n_val, bool(meta.variable), meta.has_forces,
        )

    def _onRemoteModelMeta(
        self,
        fingerprint,
        name=None,
        dataset_fingerprints=None,
    ):
        """Create a local GhostModelLoader when the server registers a ghost model.

        Called when ``REMOTE_MODEL_META`` arrives.  The server sends this event
        from the ``MODEL_LOADED`` handler, which fires *after*
        ``_loadPredictionsFromKeys`` and ``lookForGhosts()`` have run — so
        prediction arrays are already in ``env.cache`` on the server side.

        After creating the ghost model, auto-triggers ``taskFetchRemoteDataset``
        for every associated dataset that still has no arrays on the client
        (``is_remote_proxy=True``).  This pulls the arrays *including* the
        prediction data so plots work immediately.
        """
        from modelLoaders.ghost import GhostModelLoader
        from ffast.protocol import ModelMeta
        from pydantic import ValidationError

        if self.getModel(fingerprint) is not None:
            logger.debug(
                "REMOTE_MODEL_META: ghost model already exists for %r", fingerprint
            )
            return

        # Slice 3: validate the announcement against the typed transport contract
        # (ffast.protocol.ModelMeta) instead of trusting raw kwargs.
        try:
            meta = ModelMeta(name=name, dataset_fingerprints=dataset_fingerprints)
        except ValidationError as exc:
            logger.warning(
                "REMOTE_MODEL_META: invalid metadata for %r: %s", fingerprint, exc
            )
            return

        model_name = meta.name if meta.name else fingerprint[:8]
        # Register info so GhostModelLoader.initialise() finds the display name.
        self._env.loading.registerGhostModel(fingerprint, path="remote", name=model_name)
        model = GhostModelLoader(self._env, fingerprint)
        model.initialise()
        self.setNewModel(model)
        logger.info(
            "Remote ghost model created: %r (%s)", fingerprint[:8], model_name
        )
        # The zero baseline is NOT auto-loaded here. It loads only on explicit
        # request (File ▸ Load Zero Model / Ctrl+0); a prediction arriving must
        # not silently add a model the user never asked for.

        # Stage 4c: do NOT auto-fetch arrays/predictions here. Plots read metrics
        # via the server channel (4a) and Loupe reads server scenes, so the client
        # needs no local prediction arrays up front. If an in-process consumer ever
        # needs them (real client-model fallback), they are fetched lazily on
        # demand (CachedRemoteDataset._ensure_arrays / _fetchPredictionArraysSync).

    def _onMetricCatalog(self, metrics=None):
        """Store the server's metric catalog (ADR 0016) and notify listeners.

        Sent on connect / state-sync. Clients (e.g. the Loupe ATOMS pane) build
        their metric controls from this, keyed by metric id.
        """
        from ffast.protocol import MetricCatalog
        from pydantic import ValidationError

        # Slice 3: validate the catalog against the typed transport contract
        # (ffast.protocol.MetricCatalog) instead of trusting raw dicts. Entries are
        # kept as dicts downstream (Loupe ATOMS pane reads metricCatalog[id]["..."])
        # so model_dump() preserves the exact wire shape.
        try:
            catalog = MetricCatalog(metrics=metrics or [])
        except ValidationError as exc:
            logger.warning("METRIC_CATALOG: invalid catalog dropped: %s", exc)
            return
        self.metricCatalog = {e.id: e.model_dump() for e in catalog.metrics}
        logger.info("METRIC_CATALOG received: %d metrics", len(self.metricCatalog))
        self.eventPush("METRIC_CATALOG_UPDATED")

    async def _fetchRemoteDatasetTask(self, fingerprint, taskID=None):
        """Async task: transfer arrays from server and populate local proxy.

        Progress is reported through TASK_PROGRESS so the Tasks panel shows a
        progress bar during the transfer.
        """
        session = self.serverConnection
        if session is None:
            logger.error("taskFetchRemoteDataset: no remote session active")
            return

        self.eventPush(
            "TASK_PROGRESS",
            taskID,
            message="Requesting arrays from remote server…",
        )
        try:
            arrays = await session.request_subdataset_arrays(fingerprint)
        except asyncio.TimeoutError:
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                message="Timed out waiting for server response",
                error=True,
            )
            logger.error("Array transfer timed out for %r", fingerprint)
            return
        except Exception as exc:
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                message=f"Transfer error: {exc}",
                error=True,
            )
            logger.error("Array transfer failed for %r: %s", fingerprint, exc)
            return

        self.eventPush(
            "TASK_PROGRESS",
            taskID,
            message="Populating local cache…",
        )

        # Unpack payload — request_subdataset_arrays now returns a dict with
        # two top-level keys: "arrays" (coord/element arrays + pred__ entries)
        # and "model_names" (fp → display name).
        payload = arrays  # named "arrays" for historical reasons; it's the payload
        raw_arrays = payload.get("arrays", payload)   # back-compat if plain dict
        model_names = payload.get("model_names") or {}

        # Separate prediction entries from geometry/element arrays.
        pred_data: dict = {}   # model_fp → {dtype: np.ndarray}
        main_arrays: dict = {}
        from ffast.cache import PredictionArrayKey
        for key, val in raw_arrays.items():
            if PredictionArrayKey.is_prediction_key(key):
                pk = PredictionArrayKey.parse(key)
                pred_data.setdefault(pk.model_fp, {})[pk.dtype] = val
            else:
                main_arrays[key] = val

        dataset = self.getDataset(fingerprint)
        if dataset is None:
            # No proxy was created yet — build one now
            from cluster.remote_dataset import CachedRemoteDataset

            n_val = len(main_arrays.get("R") or [])
            dataset = CachedRemoteDataset(fingerprint, fingerprint[:12], n_val)
            self.setNewDataset(dataset, slice_num=-2)

        dataset.populate(main_arrays)

        # ── Recreate prediction DataEntities from transferred arrays ─────────
        if pred_data:
            self.eventPush(
                "TASK_PROGRESS", taskID,
                message="Importing prediction data…",
            )

        offsets = main_arrays.get("offsets")   # present for variable datasets
        for model_fp, preds in pred_data.items():
            try:
                E = preds.get("energy")
                F = preds.get("forces")

                if E is not None:
                    energy_dt = self.getDataType("energy")
                    energy_de = energy_dt.newDataEntity(
                        energy=np.asarray(E).flatten()
                    )
                    self.setData(energy_de, "energy",
                                 model=model_fp, dataset=dataset)

                if F is not None:
                    forces_dt = self.getDataType("forces")
                    F_arr = np.asarray(F)
                    if offsets is not None:
                        # Variable dataset — F was flattened on the server;
                        # reconstruct as list of per-molecule arrays.
                        F_val = [
                            F_arr[offsets[i]:offsets[i + 1]]
                            for i in range(len(offsets) - 1)
                        ]
                    else:
                        F_val = F_arr  # uniform (N, natoms, 3)
                    forces_de = forces_dt.newDataEntity(forces=F_val)
                    self.setData(forces_de, "forces",
                                 model=model_fp, dataset=dataset)

                # Store model info so GhostModelLoader.initialise() finds it.
                model_name = model_names.get(model_fp, model_fp[:8])
                self._env.loading.registerGhostModel(
                    model_fp, path="remote", name=model_name
                )
                logger.info(
                    "Imported predictions for %r (model %s)",
                    model_name, model_fp[:8],
                )
            except Exception as exc:
                logger.error(
                    "Failed to import predictions for model %r: %s",
                    model_fp[:8], exc,
                )

        # Create GhostModelLoader objects for any newly-imported prediction data
        if pred_data:
            self.lookForGhosts()

        # Notify Loupe (and any other subscriber) that arrays are ready
        self.eventPush("REMOTE_ARRAY_FETCH_DONE", fingerprint)
        self.eventPush("DATASET_UPDATED", fingerprint)
        logger.info("Array transfer complete for %r", fingerprint)

    def openRemoteView(
        self, view_id: str, dataset_ref: str, prediction_ref: str | None = None
    ) -> None:
        """Ask the remote server to open/refresh a VisualizationView.

        Server-owned render path (ADR 0014). Fire-and-forget: the server
        replies with a ``SCENE_SNAPSHOT`` that arrives asynchronously through
        the listener and is consumed by the Loupe scene adapter. No-op only
        before a session exists. On desktop ``serverConnection`` is always set once
        the managed local server connects at launch (ADR 0017-desktop), so the
        no-op branch fires only during the brief startup window — there is no
        separate embedded/in-process render transport.

        ``prediction_ref`` is the fingerprint of the model whose predicted
        forces overlay the view (force arrows, metric coloring per ADR 0016).
        Always sent so the key's presence lets the server set it to a value or
        clear it (null); ``None`` means no prediction overlay.
        """
        session = self.serverConnection
        if session is None:
            return

        async def _send():
            try:
                await session.push_event(
                    control.OPEN_VIEW,
                    view_id=view_id,
                    dataset_ref=dataset_ref,
                    prediction_ref=prediction_ref,
                )
            except Exception as exc:
                logger.error("openRemoteView failed: %s", exc)

        self.tm.simpleTask(_send)

    def closeRemoteView(self, view_id: str) -> None:
        """Tell the remote server to drop a VisualizationView (ADR 0014).

        Fire-and-forget; prevents server-side view accumulation when a Loupe
        window closes. No-op when not connected to a remote server.
        """
        session = self.serverConnection
        if session is None:
            return

        async def _send():
            try:
                await session.push_event(control.CLOSE_VIEW, view_id=view_id)
            except Exception as exc:
                logger.error("closeRemoteView failed: %s", exc)

        self.tm.simpleTask(_send)

    def sendViewCommand(self, **fields) -> None:
        """Send a typed ViewCommand to the remote server (ADR 0014).

        ``fields`` are the discriminated-union command fields, e.g.
        ``type="SET_FRAME", view_id=..., view_version=0, frame_index=...``.
        Fire-and-forget: the server replies with a ``SCENE_PATCH`` that arrives
        via the listener and is applied by the Loupe scene adapter. No-op when
        not connected to a remote server.
        """
        session = self.serverConnection
        if session is None:
            return

        async def _send():
            try:
                await session.push_event(control.VIEW_COMMAND, **fields)
            except Exception as exc:
                logger.error("sendViewCommand failed: %s", exc)

        self.tm.simpleTask(_send)

    def taskFetchRemoteDataset(self, fingerprint: str) -> None:
        """Schedule an async task to transfer arrays for *fingerprint* from the server.

        Idempotent: if arrays are already cached in the session,
        :meth:`ServerConnection.request_subdataset_arrays` returns instantly.
        """
        self.newTask(
            self._fetchRemoteDatasetTask,
            args=(fingerprint,),
            visual=True,
            name="Fetching remote arrays",
        )

    async def _fetchPredictionArraysTask(
        self, ds_fp: str, model_fp: str, taskID=None
    ):
        """Async task: fetch prediction arrays only via the Prediction-Only Channel.

        Sends ``REQUEST_PREDICTION_ARRAYS`` and populates the local cache with
        the returned energy/forces data.  Geometry arrays are not re-transferred.
        """
        session = self.serverConnection
        if session is None:
            logger.error("_fetchPredictionArraysTask: no remote session")
            return

        self.eventPush(
            "TASK_PROGRESS", taskID,
            message="Requesting prediction arrays from remote server…",
        )
        try:
            arrays = await session.request_prediction_arrays(
                ds_fp, model_fp
            )
        except asyncio.TimeoutError:
            self.eventPush(
                "TASK_PROGRESS", taskID,
                message="Timed out waiting for prediction arrays",
                error=True,
            )
            logger.error(
                "Prediction array transfer timed out: model=%r dataset=%r",
                model_fp[:8], ds_fp[:8],
            )
            return
        except Exception as exc:
            self.eventPush(
                "TASK_PROGRESS", taskID,
                message=f"Prediction transfer error: {exc}",
                error=True,
            )
            logger.error(
                "Prediction array transfer failed model=%r dataset=%r: %s",
                model_fp[:8], ds_fp[:8], exc,
            )
            return

        self.eventPush(
            "TASK_PROGRESS", taskID,
            message="Importing prediction data…",
        )
        self._importPredictionArrays(ds_fp, model_fp, arrays)
        self.eventPush("REMOTE_ARRAY_FETCH_DONE", ds_fp)

    def _importPredictionArrays(self, ds_fp, model_fp, arrays):
        """Populate the cache with server-returned prediction arrays.

        Shared by the async prediction-fetch task and the synchronous metric
        path (:meth:`_fetchPredictionArraysSync`).  ``arrays`` is keyed
        ``pred__energy__<fp>`` / ``pred__forces__<fp>`` (Prediction-Only Channel).
        """
        dataset = self.getDataset(ds_fp)
        if dataset is None:
            logger.error("_importPredictionArrays: dataset %r not found", ds_fp)
            return

        offsets = None
        if getattr(dataset, "isVariable", False):
            offsets = getattr(dataset, "_offsets", None)

        from ffast.cache import PredictionArrayKey
        E = arrays.get(PredictionArrayKey("energy", model_fp).format())
        F = arrays.get(PredictionArrayKey("forces", model_fp).format())

        if E is not None:
            energy_dt = self.getDataType("energy")
            energy_de = energy_dt.newDataEntity(energy=np.asarray(E).flatten())
            self.setData(energy_de, "energy", model=model_fp, dataset=dataset)

        if F is not None:
            forces_dt = self.getDataType("forces")
            F_arr = np.asarray(F)
            if offsets is not None:
                # Variable dataset — F was flattened on the server; reconstruct
                # as a list of per-molecule arrays.
                F_val = [
                    F_arr[offsets[i]:offsets[i + 1]]
                    for i in range(len(offsets) - 1)
                ]
            else:
                F_val = F_arr  # uniform: (N, natoms, 3)
            forces_de = forces_dt.newDataEntity(forces=F_val)
            self.setData(forces_de, "forces", model=model_fp, dataset=dataset)

        self.lookForGhosts()
        logger.info(
            "Prediction arrays imported: model=%r dataset=%r E=%s F=%s",
            model_fp[:8], ds_fp[:8], E is not None, F is not None,
        )

    def _fetchPredictionArraysSync(self, ds_fp, model_fp, timeout=300):
        """Blocking request for server-generated prediction arrays (Stage 2).

        Called from generateMetric's worker thread when a proxy model's
        predictions are missing: asks the server to generate + transfer them
        (the server runs model.predict), imports them, and returns True so the
        metric computes in the same task — no defer/retry needed.  Safe to block
        here: we are off the event loop, which stays free to receive the reply.
        """
        session = self.serverConnection
        if session is None or self._event_loop is None:
            return False
        import asyncio as _asyncio
        try:
            fut = _asyncio.run_coroutine_threadsafe(
                session.request_prediction_arrays(ds_fp, model_fp, timeout=timeout),
                self._event_loop,
            )
            arrays = fut.result(timeout=timeout + 20)
        except Exception as exc:
            logger.error(
                "Sync prediction fetch failed model=%r dataset=%r: %s",
                model_fp[:8], ds_fp[:8], exc,
            )
            return False
        self._importPredictionArrays(ds_fp, model_fp, arrays)
        return True

    def _fetchDatasetArraysSync(self, fingerprint):
        """Blocking full-array fetch for a lazy proxy (Stage 4c).

        Called from CachedRemoteDataset._ensure_arrays on a worker thread the
        first time a consumer reads R/F/E.  Pulls the geometry arrays from the
        server and populates the proxy.  Off the event loop, so blocking is safe.
        """
        session = self.serverConnection
        if session is None or self._event_loop is None:
            return False
        import asyncio as _asyncio
        try:
            fut = _asyncio.run_coroutine_threadsafe(
                session.request_subdataset_arrays(fingerprint), self._event_loop
            )
            payload = fut.result(timeout=300)
        except Exception as exc:
            logger.error("Sync dataset array fetch failed %r: %s", fingerprint, exc)
            return False
        raw_arrays = (
            payload.get("arrays", payload) if isinstance(payload, dict) else payload
        )
        from ffast.cache import PredictionArrayKey
        main_arrays = {
            k: v for k, v in raw_arrays.items()
            if not PredictionArrayKey.is_prediction_key(k)
        }
        ds = self.getDataset(fingerprint)
        if ds is not None and hasattr(ds, "populate"):
            ds.populate(main_arrays)
            logger.info("Lazy dataset arrays populated: %r", fingerprint)
        return True

    def _fetchMetricResultSync(self, metric_id, params, model, dataset, key):
        """Ask the server to compute a metric and cache the result (Stage 4a).

        Called from generateMetric's worker thread.  Returns True if the server
        computed + transferred a result (cached under ``key``); False if the
        server couldn't (e.g. a real client-only model) so the caller falls back
        to in-process computation.  Safe to block here — off the event loop,
        which stays free to receive the reply.
        """
        session = self.serverConnection
        if session is None or self._event_loop is None:
            return False
        import asyncio as _asyncio
        model_fp = model.fingerprint if model is not None else None
        dataset_fp = dataset.fingerprint if dataset is not None else None
        try:
            fut = _asyncio.run_coroutine_threadsafe(
                session.request_metric(
                    metric_id, params, model_fp, dataset_fp, key
                ),
                self._event_loop,
            )
            result = fut.result(timeout=140)
        except Exception as exc:
            logger.warning(
                "Server metric compute failed %s (%s) — falling back: %s",
                metric_id, key, exc,
            )
            return False
        if result is None:
            return False
        self.cache[key] = result
        self.eventPush("DATA_UPDATED", key)
        logger.info("Metric %s computed server-side (%s)", metric_id, key)
        return True

    def taskFetchPredictionArrays(
        self, ds_fp: str, model_fp: str
    ) -> None:
        """Schedule a prediction-only array fetch (no geometry re-transfer)."""
        self.newTask(
            self._fetchPredictionArraysTask,
            args=(ds_fp, model_fp),
            visual=True,
            name="Fetching remote predictions",
        )
