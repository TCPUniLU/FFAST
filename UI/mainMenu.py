import os
from PySide6.QtWidgets import QFileDialog
from UI.Templates import customFileDialog, BigDatasetWarningDialog, RemoteStrideDialog
from UI.menuShared import MenuHandlerBase
from UI.menuLogic import (
    resolve_key_options,
    stride_to_slice_num,
    parse_host_port,
    latest_session_record,
)
from utils import deep_getsizeof
from client.dataType import AtomsList


class MainMenuHandler(MenuHandlerBase):
    """Main-window menu: session, dataset/model/prediction load, cluster, remote.

    Reaches into ``self.handler.env``. The heavy remote-load flows are async
    orchestration that bridges Qt dialogs and the RPC session; the pure
    decisions they make live in ``UI.menuLogic``.
    """

    def connectActions(self):
        handler, window = (self.handler, self.window)
        mb = window.menuBar()

        # FILE
        File = mb.addMenu("&File")
        File.addAction("Save", self.onSave, "Ctrl+s")
        File.addAction("Load", self.onLoad, "Ctrl+l")

        File.addAction("Load Dataset", self.onDatasetLoad, "Ctrl+d")
        File.addAction("Load Model", self.onModelLoad, "Ctrl+m")

        File.addAction("Load Zero Model", self.onZeroModelLoad, "Ctrl+0")
        File.addAction("Load Prediction", self.onPrepredictedModelLoad, "Ctrl+p")

        File.addAction("Load Config…", self.onConfigLoad)

        File.addSeparator()
        File.addAction(
            "Connect to Cluster…",
            self.onConnectToCluster,
            "Ctrl+Shift+C",
        )
        File.addAction(
            "Connect to Local Server…",
            self.onConnectLocalServer,
            "Ctrl+Shift+L",
        )
        File.addAction(
            "Load Remote Dataset…",
            self.onRemoteDatasetLoad,
            "Ctrl+Shift+D",
        )
        File.addAction(
            "Load Remote Prediction…",
            self.onRemotePredictionLoad,
            "Ctrl+Shift+P",
        )

        # File.addAction("Preferences", self.onPreferences)
        # File.addAction("Exit", self.onExit)

        # LOUPE (shared "&3D View → New")
        self._addLoupeMenu(mb)

    def onSave(self):
        workdir = self.handler.workdir
        (path, _) = QFileDialog.getSaveFileName(self.handler.window, "Save File", workdir)
        if path is None or path.strip() == "":
            return

        # Stage 5: save SERVER-SIDE (the server holds the real datasets +
        # prediction cache; the thin client does not).
        self.handler.env.requestSessionSave(path)

    def onLoad(self):
        workdir = self.handler.workdir
        path = QFileDialog.getExistingDirectory(self.handler.window, "Select Directory", workdir)
        if path is None or path.strip() == "":
            return

        # Stage 5: load SERVER-SIDE; the server restores its Environment and
        # announces datasets/models back to the client.
        self.handler.env.requestSessionLoad(path)

    def onConfigLoad(self):
        workdir = self.handler.workdir
        path, _ = QFileDialog.getOpenFileName(
            self.handler.window, "Load Config", workdir, "TOML files (*.toml)"
        )
        if not path:
            return
        self.handler.env.loadConfig(path)

    def onPreferences(self):
        pass

    def onExit(self):
        self.eventPush("QUIT_EVENT")

    def onDatasetLoad(self):
        import logging
        logger = logging.getLogger("FFAST")

        env = self.handler.env
        workdir = self.handler.workdir
        fileTypes = sorted(list(env.datasetTypes.keys()))
        extensions = [
            env.datasetTypes[x].datasetFileExtension for x in fileTypes
        ]
        path, typ = customFileDialog(
            self.handler.window, fileTypes=fileTypes, extensions=extensions, directory=workdir
        )
        if path is None:
            logger.warning("No path was selected, please try again later")
            return
        # For ASE datasets, show key selection dialog on main thread (before threaded task)
        selected_energy_key = None
        selected_force_key = None
        prediction_keys = None

        if typ == "ase (auto)" and path:
            # Show dialog on main thread to avoid Qt threading issues
            result = self._showASEKeySelectionDialog(path)

            # If user cancelled, abort (all three values are None)
            if result == (None, None, None):
                return

            selected_energy_key, selected_force_key, prediction_keys = result
        file_size = os.path.getsize(path) / 1_000_000_000
        slice_num = -1  # load entirely on RAM by default
        if file_size >= 3:
            slice_num = self.large_dataset_handle(path, logger)

        if slice_num == -2:
            logger.info("load cancelled.")
            return
        if slice_num > 0:
            logger.info(f"loading dataset with slice: {slice_num}")
        env.requestDatasetLoad(path, typ, selected_energy_key=selected_energy_key, selected_force_key=selected_force_key,
                               prediction_keys=prediction_keys, slice_num=slice_num)

    def large_dataset_handle(self, path, logger):
        from PySide6.QtWidgets import QDialog
        import ase.io

        logger.info("Large dataset detected, calculating the length.")
        length = AtomsList.calc_dataset_length_static(path)
        logger.info(f"Total dataset length: {length}")

        logger.info("Total length calculated, approximating size of each atom in dataset.")
        temp_dataset = ase.io.read(path, index=slice(0, 1000, None))
        temp_size = deep_getsizeof(temp_dataset)  # the size of temp_dataset in bytes.
        avg_per_atom_size = temp_size/1000
        file_size = length*avg_per_atom_size
        logger.info(f"Total size of the dataset on RAM would be approximately {file_size/1_000_000_000:.2f} GBs.")

        dialog = BigDatasetWarningDialog(file_size, length, self.handler.window)
        result = dialog.exec()
        if result == QDialog.Accepted:
            slice_num = dialog.get_slice_number()
            if dialog.user_clicked_pick_samples:
                try:
                    slice_num = int(slice_num)
                    if slice_num >= 0:
                        return slice_num
                    else:
                        logger.info("Invalid slice number entered, load abort.")
                        return -2
                except (ValueError, TypeError):
                    logger.info("Invalid slice number entered, load abort.")
                    return -2
            elif dialog.user_clicked_load_no_cache:
                logger.info("User decided to load the whole dataset without caching.")
                return -1
            else:
                logger.info("User decided to load the whole dataset with caching.")
                return 0
        else:
            logger.info("User cancelled the load operation.")
            return -2

    def _showASEKeySelectionDialog(self, path, for_predictions=False):
        """Show ASE key selection dialog on main thread.

        Reads only the FIRST frame to detect available keys, then shows dialog.
        The full dataset is loaded later in the background thread.

        Args:
            path: Path to the ASE file
            for_predictions: If True, show simplified dialog for loading predictions only

        Returns:
            tuple: (selected_energy_key, selected_force_key, prediction_keys) or (None, None, None) if cancelled
        """
        import ase.io
        from modules.loaders.aseDataset import aseDatasetLoader
        from UI.KeySelectionDialog import KeySelectionDialog
        import logging

        logger = logging.getLogger("FFAST")

        try:
            # Read ONLY first frame to detect keys (much faster for large datasets)
            first_atoms = ase.io.read(path, index=0)

            # Create temporary loader with just first frame to access key detection
            # We'll load the full dataset later in the background thread
            temp_loader = aseDatasetLoader(path, atomsList=[first_atoms])

            # Check if multiple keys exist
            energy_keys = temp_loader.EneregyKeys()
            force_keys = temp_loader.ForceKeys()

            # Check if calculator is available
            has_calculator_energy = False
            has_calculator_forces = False
            try:
                first_atoms.get_potential_energy()
                has_calculator_energy = True
            except:
                pass

            try:
                first_atoms.get_forces()
                has_calculator_forces = True
            except:
                pass

            logger.info(f"Detected {len(energy_keys)} energy key(s) and {len(force_keys)} force key(s) from first frame")
            logger.info(f"Calculator available: energy={has_calculator_energy}, forces={has_calculator_forces}")

            need_dialog, default_energy, default_force = resolve_key_options(
                energy_keys, force_keys, has_calculator_energy, has_calculator_forces
            )

            # Skip dialog when there is only one option for each.
            if not need_dialog:
                return (default_energy, default_force, [])

            # Show dialog
            dialog = KeySelectionDialog(
                energy_keys, force_keys,
                parent=self.handler.window,
                for_predictions=for_predictions
            )
            if dialog.exec() == KeySelectionDialog.Accepted:
                selection = dialog.getSelection()
                return (
                    selection['energy_ref'],
                    selection['force_ref'],
                    selection['predictions']
                )
            else:
                # User cancelled
                logger.info("Dataset loading cancelled by user")
                return None, None, None

        except Exception as e:
            logger.error(f"Unable to read file: {path}. Error showing ASE key selection dialog: {e}")
            logger.error(f"The ase dataset loader could not recognize the specified dataset:"
                         f"'{path}'.\nIf you are choosing a file with .npz extension, please try again "
                         f"and choose *.npz in the file type filter dropdown")
            return None, None, None

    def onModelLoad(self):
        env = self.handler.env
        workdir = self.handler.workdir
        fileTypes = list(env.modelTypes.keys())
        extensions = [env.modelTypes[x].modelFileExtension for x in fileTypes]
        path, typ = customFileDialog(
            self.handler.window, fileTypes=fileTypes, extensions=extensions, directory=workdir
        )
        if path is None:
            return

        # Stage 2 server-side model loading (env.requestModelLoad → server runs
        # predictions on demand) is implemented end-to-end but DEPRIORITIZED.
        # In-process load is kept as default as a PRECAUTION — the suspected
        # regression vs the server path was never actually measured. Flip to
        # env.requestModelLoad(path, typ) to make live real-model inference
        # server-owned (infra is in place: server metas real models + generates
        # on demand; client _fetchPredictionArraysSync wires generateMetric).
        # NOTE: this call is unconditional, so in REMOTE mode a real model loads
        # into the local client, not the cluster — remote live inference from the
        # UI is not reachable until this flips (remote uses prediction files).
        env.taskLoadModel(path, typ)

    def onPrepredictedModelLoad(self):
        import logging
        logger = logging.getLogger("FFAST")

        env = self.handler.env
        workdir = self.handler.workdir
        names = [x.getName() for x in env.datasets.all(excludeSubs=True)]
        keys = [x.fingerprint for x in env.datasets.all(excludeSubs=True)]
        extensions = ["*"] * len(names)
        extensions += ["*.npz"] * len(names)
        names += names
        path, typ = customFileDialog(
            self.handler.window, fileTypes=names, extensions=extensions, directory=workdir
        )

        if path is None:
            logger.warning("No path was selected please try again.")
            return

        idx = names.index(typ)
        # For ASE files (non-NPZ), show key selection dialog on main thread
        selected_energy_key = None
        selected_force_key = None

        if path and "npz" not in path:
            # ASE file - might have multiple keys
            result = self._showASEKeySelectionDialog(path, for_predictions=True)

            # If user cancelled, abort
            if result == (None, None, None):
                return

            selected_energy_key, selected_force_key, _ = result  # Ignore prediction_keys for this use case

        env.requestPredictionLoad(
            path, keys[idx],
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key
        )

    def onZeroModelLoad(self):
        env = self.handler.env
        env.taskLoadZeroModel()

    def onConnectToCluster(self):
        """Open the cluster connection dialog and start the connect flow."""
        import logging
        logger = logging.getLogger("FFAST")
        from UI.ClusterProfileDialog import ClusterConnectDialog
        from PySide6.QtWidgets import QMessageBox

        dialog = ClusterConnectDialog(parent=self.handler.window)
        if dialog.exec() != ClusterConnectDialog.Accepted:
            return

        profile = dialog.get_profile()
        if not profile.host:
            QMessageBox.warning(
                self.handler.window,
                "Connect to Cluster",
                "No host specified in the profile.",
            )
            return

        logger.info(
            "Connect requested: host=%s user=%s partition=%s",
            profile.host,
            profile.username,
            profile.partition,
        )

        env = self.handler.env

        # ── reconnect check ───────────────────────────────────────────────
        # If a session record exists for this profile the server may still be
        # running.  Offer to reconnect (skips SLURM submit + polling).
        reconnect_job_id = None
        try:
            from cluster.session import load_session_records
            latest = latest_session_record(load_session_records(), profile.name)
            if latest:
                msg = QMessageBox(self.handler.window)
                msg.setWindowTitle("Reconnect to cluster?")
                msg.setText(
                    f"A previous session was found for <b>{profile.name}</b>:<br><br>"
                    f"&nbsp;&nbsp;Job <b>{latest['job_id']}</b>"
                    f" — started {latest.get('timestamp', 'unknown')}<br><br>"
                    f"Reconnect to the running job, or submit a new one?"
                )
                reconnect_btn = msg.addButton(
                    f"Reconnect to {latest['job_id']}", QMessageBox.AcceptRole
                )
                msg.addButton("Submit new job", QMessageBox.RejectRole)
                msg.exec()
                if msg.clickedButton() == reconnect_btn:
                    reconnect_job_id = latest["job_id"]
                    logger.info(
                        "User chose to reconnect to job %s", reconnect_job_id
                    )
        except Exception as exc:
            logger.warning("Reconnect check failed: %s", exc)

        env.tm.newTask(
            env.remote.connectToCluster,
            args=(profile,),
            kwargs={"reconnect_job_id": reconnect_job_id},
            visual=True,
            name=(
                f"Reconnecting to {profile.host} [{reconnect_job_id}]…"
                if reconnect_job_id
                else f"Connecting to {profile.host}…"
            ),
        )

    def onConnectLocalServer(self):
        """Connect directly to a local ffast-server (no SLURM/SSH).

        Use for local testing:
          Terminal 1: python server.py --port 8765
          Then File → Connect to Local Server…
        """
        import logging
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        logger = logging.getLogger("FFAST")
        env = self.handler.env

        addr_str, ok = QInputDialog.getText(
            self.handler.window,
            "Connect to Local Server",
            "host:port  (IPv4 — e.g. 127.0.0.1:8765):",
            text="127.0.0.1:8765",
        )
        if not ok or not addr_str.strip():
            return
        try:
            host, port = parse_host_port(addr_str)
        except ValueError:
            QMessageBox.warning(
                self.handler.window, "Invalid address",
                f"Expected host:port, got {addr_str!r}",
            )
            return

        env.tm.newTask(
            env.remote.connectDirect,
            args=(host, port),
            visual=True,
            name=f"Local server {host}:{port}",
        )

    def onRemoteDatasetLoad(self):
        """Load a dataset on the remote cluster via the active RPC session.

        Mirrors the local onDatasetLoad routine:
        1. Text dialog for cluster-side path.
        2. Type dropdown (same choices as local).
        3. For ASE files: server probes first frame, then shows the same
           KeySelectionDialog so energy/force keys can be picked.
        4. Sends LOAD_DATASET RPC with selected keys.
        """
        import logging
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        logger = logging.getLogger("FFAST")
        env = self.handler.env
        session = getattr(env, "remoteSession", None)

        if session is None:
            QMessageBox.warning(
                self.handler.window,
                "No Cluster Connection",
                "Not connected to a cluster.\n"
                "Use File → Connect to Cluster… first.",
            )
            return

        # ── 1. path ──────────────────────────────────────────────────────────
        path, ok = QInputDialog.getText(
            self.handler.window,
            "Load Remote Dataset",
            "Cluster path to dataset file:",
        )
        if not ok or not path.strip():
            return
        path = path.strip()

        # ── 2. type ──────────────────────────────────────────────────────────
        types = sorted(list(env.datasetTypes.keys()))
        typ, ok2 = QInputDialog.getItem(
            self.handler.window,
            "Dataset Type",
            "Format:",
            types,
            0,
            False,
        )
        if not ok2:
            return

        logger.info("Requesting remote load: path=%s type=%s", path, typ)

        if typ == "ase (auto)":
            # ── 3. ASE: probe keys on server, show KeySelectionDialog ─────
            #
            # IMPORTANT: dialog.exec() must NOT be called while an asyncio
            # task is active — calling it inside a coroutine triggers:
            #   RuntimeError: Cannot enter into task … while another task …
            #   is being executed.
            # Fix: probe keys in an async task, then defer the dialog to a
            # QTimer callback (no task running at that point) and bridge back
            # into the task via an asyncio.Future.
            handler_window = self.handler.window

            async def _probeAndLoadTask(taskID=None):
                import asyncio as _asyncio
                from PySide6.QtCore import QTimer

                # ── 3a. probe dataset length for stride estimate ──────────
                env.eventPush(
                    "TASK_PROGRESS", taskID,
                    message="Probing dataset length on server…",
                )
                n_total = None
                try:
                    length_result = await session.probe_dataset_length(path)
                    if not length_result.get("error"):
                        n_total = length_result.get("n")
                except Exception as exc:
                    logger.warning("Length probe failed (non-fatal): %s", exc)

                # ── 3b. show stride dialog on main thread ─────────────────
                loop = _asyncio.get_event_loop()
                stride_future = loop.create_future()

                def _show_stride_dialog():
                    try:
                        dlg = RemoteStrideDialog(
                            n_total=n_total,
                            parent=handler_window,
                        )
                        if dlg.exec() == RemoteStrideDialog.Accepted:
                            if not stride_future.done():
                                stride_future.set_result(dlg.get_stride())
                        else:
                            if not stride_future.done():
                                stride_future.set_result(None)
                    except Exception as exc:
                        if not stride_future.done():
                            stride_future.set_exception(exc)

                QTimer.singleShot(0, _show_stride_dialog)
                env.eventPush(
                    "TASK_PROGRESS", taskID,
                    message="Waiting for stride selection…",
                )
                stride = await stride_future
                if stride is None:
                    logger.info("Remote dataset loading cancelled by user")
                    return
                # slice_num=0 means "load all" (efficient path); N>1 = every Nth
                slice_num = stride_to_slice_num(stride)
                logger.info(
                    "Requesting remote load: path=%s type=%s stride=%d",
                    path, typ, stride,
                )

                # ── 3c. probe keys ────────────────────────────────────────
                env.eventPush(
                    "TASK_PROGRESS", taskID,
                    message="Probing dataset keys on server…",
                )
                try:
                    probe = await session.probe_dataset_keys(path, typ)
                except Exception as exc:
                    logger.error("Key probe failed: %s", exc)
                    env.eventPush(
                        "TASK_PROGRESS", taskID,
                        message=f"Key probe failed: {exc}", error=True,
                    )
                    return

                if probe.get("error"):
                    logger.warning(
                        "Server probe error for %r: %s", path, probe["error"]
                    )
                    # Fall back: load without explicit key selection
                    await session.push_event("LOAD_DATASET", path, typ, slice_num=slice_num)
                    return

                energy_keys = probe.get("energy_keys") or []
                force_keys = probe.get("force_keys") or []
                has_calc_energy = bool(probe.get("has_calculator_energy"))
                has_calc_forces = bool(probe.get("has_calculator_forces"))

                # Mirrors local _showASEKeySelectionDialog logic:
                # skip dialog when there is only one option for each.
                need_dialog, selected_energy_key, selected_force_key = resolve_key_options(
                    energy_keys, force_keys, has_calc_energy, has_calc_forces
                )
                prediction_keys = []

                if need_dialog:
                    loop = _asyncio.get_event_loop()
                    dialog_future = loop.create_future()

                    def _show_dialog_on_main_thread():
                        """Run dialog outside any asyncio task (QTimer callback).

                        When this fires, the probe task is suspended on
                        ``await dialog_future``, so no asyncio task is
                        "executing".  dialog.exec() can safely enter Qt's
                        nested event loop without the RuntimeError.
                        """
                        try:
                            from UI.KeySelectionDialog import KeySelectionDialog
                            dlg = KeySelectionDialog(
                                energy_keys, force_keys,
                                parent=handler_window,
                            )
                            if dlg.exec() == KeySelectionDialog.Accepted:
                                if not dialog_future.done():
                                    dialog_future.set_result(
                                        dlg.getSelection()
                                    )
                            else:
                                # User cancelled
                                if not dialog_future.done():
                                    dialog_future.set_result(None)
                        except Exception as exc:
                            if not dialog_future.done():
                                dialog_future.set_exception(exc)

                    # Schedule dialog for next Qt event loop tick.
                    # By this point our coroutine will be suspended on
                    # ``await dialog_future`` (no task executing).
                    QTimer.singleShot(0, _show_dialog_on_main_thread)

                    env.eventPush(
                        "TASK_PROGRESS", taskID,
                        message="Waiting for key selection…",
                    )
                    selection = await dialog_future  # task suspended here

                    if selection is None:
                        logger.info("Remote dataset loading cancelled by user")
                        return

                    selected_energy_key = selection["energy_ref"]
                    selected_force_key = selection["force_ref"]
                    prediction_keys = selection["predictions"]

                logger.info(
                    "Remote LOAD_DATASET: energy_key=%r force_key=%r "
                    "prediction_keys=%r",
                    selected_energy_key, selected_force_key, prediction_keys,
                )
                await session.push_event(
                    "LOAD_DATASET", path, typ,
                    selected_energy_key=selected_energy_key,
                    selected_force_key=selected_force_key,
                    prediction_keys=prediction_keys,
                    slice_num=slice_num,
                )

            env.tm.newTask(
                _probeAndLoadTask,
                visual=True,
                name="Load remote dataset…",
            )

        else:
            # ── non-ASE: probe length + stride dialog, then send ──────────
            handler_window = self.handler.window

            async def _nonAseLoadTask(taskID=None):
                import asyncio as _asyncio
                from PySide6.QtCore import QTimer

                env.eventPush(
                    "TASK_PROGRESS", taskID,
                    message="Probing dataset length on server…",
                )
                n_total = None
                try:
                    length_result = await session.probe_dataset_length(path)
                    if not length_result.get("error"):
                        n_total = length_result.get("n")
                except Exception as exc:
                    logger.warning("Length probe failed (non-fatal): %s", exc)

                loop = _asyncio.get_event_loop()
                stride_future = loop.create_future()

                def _show_stride_dialog():
                    try:
                        dlg = RemoteStrideDialog(
                            n_total=n_total,
                            parent=handler_window,
                        )
                        if dlg.exec() == RemoteStrideDialog.Accepted:
                            if not stride_future.done():
                                stride_future.set_result(dlg.get_stride())
                        else:
                            if not stride_future.done():
                                stride_future.set_result(None)
                    except Exception as exc:
                        if not stride_future.done():
                            stride_future.set_exception(exc)

                QTimer.singleShot(0, _show_stride_dialog)
                env.eventPush(
                    "TASK_PROGRESS", taskID,
                    message="Waiting for stride selection…",
                )
                stride = await stride_future
                if stride is None:
                    logger.info("Remote dataset loading cancelled by user")
                    return
                slice_num = stride_to_slice_num(stride)
                logger.info(
                    "Requesting remote load: path=%s type=%s stride=%d",
                    path, typ, stride,
                )
                await session.push_event("LOAD_DATASET", path, typ, slice_num=slice_num)

            env.tm.newTask(
                _nonAseLoadTask,
                visual=True,
                name="Load remote dataset…",
            )

    def onRemotePredictionLoad(self):
        """Load a cluster-side prediction file against an already-loaded remote dataset.

        Flow:
        1. Dropdown to select which remote dataset to attach the prediction to.
        2. Text dialog for the cluster-side path (.npz or ASE format).
        3. For ASE files: server probes first frame, KeySelectionDialog for
           energy/force keys.
        4. Sends LOAD_PREDICTION RPC — server calls
           taskLoadPrepredictedDataset, which fires MODEL_LOADED → client
           receives REMOTE_MODEL_META and auto-fetches prediction arrays via
           the Prediction-Only Array Channel.
        """
        import asyncio
        import logging
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        logger = logging.getLogger("FFAST")
        env = self.handler.env
        session = getattr(env, "remoteSession", None)

        if session is None:
            QMessageBox.warning(
                self.handler.window,
                "No Cluster Connection",
                "Not connected to a cluster.\n"
                "Use File → Connect to Cluster… first.",
            )
            return

        # ── 1. Pick remote dataset ───────────────────────────────────────────
        from cluster.remote_dataset import CachedRemoteDataset

        remote_datasets = [
            ds for ds in env.datasets.all(excludeSubs=True)
            if isinstance(ds, CachedRemoteDataset)
        ]
        if not remote_datasets:
            QMessageBox.warning(
                self.handler.window,
                "No Remote Datasets",
                "No remote datasets are loaded.\n"
                "Use File → Load Remote Dataset… first.",
            )
            return

        ds_names = [ds.getDisplayName() for ds in remote_datasets]
        ds_name, ok = QInputDialog.getItem(
            self.handler.window,
            "Load Remote Prediction",
            "Select remote dataset:",
            ds_names,
            0,
            False,
        )
        if not ok:
            return
        dataset = remote_datasets[ds_names.index(ds_name)]

        # ── 2. Cluster path to prediction file ──────────────────────────────
        path, ok = QInputDialog.getText(
            self.handler.window,
            "Load Remote Prediction",
            "Cluster path to prediction file (.npz or ASE format):",
        )
        if not ok or not path.strip():
            return
        path = path.strip()
        ds_fp = dataset.fingerprint

        logger.info(
            "Remote prediction load requested: path=%s dataset=%r",
            path, ds_fp[:8],
        )

        # ── 3. NPZ vs ASE ────────────────────────────────────────────────────
        if path.lower().endswith(".npz"):
            # NPZ: E/F keys are fixed ("E", "F") — no key dialog needed.
            asyncio.create_task(
                session.push_event("LOAD_PREDICTION", path, ds_fp)
            )
            return

        # ASE: probe keys on server, then show KeySelectionDialog if needed.
        handler_window = self.handler.window

        async def _probeAndLoadPredTask(taskID=None):
            env.eventPush(
                "TASK_PROGRESS", taskID,
                message="Probing prediction file keys on server…",
            )
            try:
                probe = await session.probe_dataset_keys(path, "ase (auto)")
            except Exception as exc:
                logger.error("Key probe failed: %s", exc)
                env.eventPush(
                    "TASK_PROGRESS", taskID,
                    message=f"Key probe failed: {exc}", error=True,
                )
                return

            if probe.get("error"):
                # Fall back: load without explicit key selection
                await session.push_event("LOAD_PREDICTION", path, ds_fp)
                return

            energy_keys = probe.get("energy_keys") or []
            force_keys = probe.get("force_keys") or []
            has_calc_energy = bool(probe.get("has_calculator_energy"))
            has_calc_forces = bool(probe.get("has_calculator_forces"))

            need_dialog, selected_energy_key, selected_force_key = resolve_key_options(
                energy_keys, force_keys, has_calc_energy, has_calc_forces
            )

            if need_dialog:
                import asyncio as _asyncio
                from PySide6.QtCore import QTimer

                loop = _asyncio.get_event_loop()
                dialog_future = loop.create_future()

                def _show_dialog():
                    """Show KeySelectionDialog outside any asyncio task."""
                    try:
                        from UI.KeySelectionDialog import KeySelectionDialog
                        dlg = KeySelectionDialog(
                            energy_keys, force_keys,
                            parent=handler_window,
                            for_predictions=True,
                        )
                        if dlg.exec() == KeySelectionDialog.Accepted:
                            if not dialog_future.done():
                                dialog_future.set_result(dlg.getSelection())
                        else:
                            if not dialog_future.done():
                                dialog_future.set_result(None)
                    except Exception as exc:
                        if not dialog_future.done():
                            dialog_future.set_exception(exc)

                QTimer.singleShot(0, _show_dialog)
                env.eventPush(
                    "TASK_PROGRESS", taskID,
                    message="Waiting for key selection…",
                )
                selection = await dialog_future

                if selection is None:
                    logger.info("Remote prediction load cancelled by user")
                    return

                selected_energy_key = selection["energy_ref"]
                selected_force_key = selection["force_ref"]

            logger.info(
                "Remote LOAD_PREDICTION: path=%r dataset=%r "
                "energy_key=%r force_key=%r",
                path, ds_fp[:8], selected_energy_key, selected_force_key,
            )
            await session.push_event(
                "LOAD_PREDICTION", path, ds_fp,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
            )

        env.tm.newTask(
            _probeAndLoadPredTask,
            visual=True,
            name="Load remote prediction…",
        )
