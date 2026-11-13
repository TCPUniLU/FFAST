"""ServerSession: the live, server-scoped core of a running ``ffast-server``.

One ``ServerSession`` exists per client connection (ADR 0044). It owns that
connection's open Visualization Views and its server→client outbound queue,
dispatches the client's Control messages to the Environment through a
built-once event→handler table, and replays current state to the client on
connect or reconnect. Every session holds a reference to the one shared
Environment rather than owning it.

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
import copy
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from pydantic import BaseModel, ValidationError

from ffast.core.flat_frames import FlatFrames
from ffast.protocol import control
from ffast.protocol import reply as replies
from ffast.session import timing, trace
from ffast.session.window_feed import WindowFeed

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
    """A pair's cached predicted energies and forces, for build_scene and
    atom colouring: ``get_prediction(dataset_fp, model_fp).forces[idx]`` or
    ``.energies[idx]``. Either is None when it was not predicted.
    """
    __slots__ = ("forces", "energies")

    def __init__(self, forces, energies=None) -> None:
        self.forces = forces
        self.energies = energies


def _frames_of(values, frames):
    """``values`` (one entry per frame) at ``frames``, or None."""
    if values is None:
        return None
    if isinstance(values, FlatFrames):
        return values.frames(frames)
    # Older entries: a list of arrays, or one array.
    if isinstance(values, list):
        return [values[i] for i in frames]
    return np.asarray(values)[frames]


def _frame_of(values, i):
    """Frame ``i`` of per-atom values held as FlatFrames, a list of per-frame
    arrays, or a (frames, atoms, ...) array."""
    if isinstance(values, FlatFrames):
        return values.values[values.offsets[i]:values.offsets[i + 1]]
    return values[i]


class ServerSession:
    """Owns Visualization Views, dispatches client events, replays state.

    See the module docstring for the dispatch-table contract. The interface is
    deliberately small — ``dispatch`` and ``replay`` — so it tests by
    constructing ``ServerSession(fake_env, asyncio.Queue())`` and asserting on
    the env calls and what lands in the outbound queue, with no socket or
    thread in the loop.
    """

    def __init__(self, env, outbound: asyncio.Queue, broadcast=None) -> None:
        self.env = env
        # Who this connection is, for the trace file ("127.0.0.1:50812")
        self.peer = ""
        # Whether the page asked for timing notes (ADR 0072); set by the handshake
        self.timing = False
        self.outbound = outbound
        # The running frame count of the Load dialog (frame index plan 2026-11-01)
        self._length_probe_cancel: threading.Event | None = None
        self._length_probe_task: asyncio.Task | None = None
        # Views' whole-dataset colours being computed (ADR 0082).
        self._colouring: set[asyncio.Task] = set()
        # Submitted "Run with FFAST" jobs followed until their first frame.
        self._job_watches: dict[str, asyncio.Task] = {}
        self.views: dict = {}  # view_id → VisualizationView
        # Sends packed bytes to every connection (the hub, ADR 0044); a
        # session on its own, as in a test, has only itself to tell.
        self._broadcast = broadcast or (lambda data: self._emit_or_drop(data, "broadcast"))
        # The one owner of what every window holds (ADR 0062). A session on its
        # own, as in a test, gets a feed of its own.
        self.feed = getattr(env, "window_feed", None) or WindowFeed(env, self._broadcast)
        self.feed.attach(self)
        # The (dataset_fp, model_fp) this window last asked METRIC_INPUTS for
        self._inputs_for: tuple | None = None
        # Running Tries of this connection (ADR 0057 rule 12): try_id → (stop
        # flag, task). The task is held here so it is not garbage-collected.
        self._tries: dict = {}
        self._score_asks: set = set()      # SCORE_DIALOG_INFO answers in flight (ADR 0093)
        # REQUEST_METRIC handlers by request id, so CANCEL_METRIC can stop one;
        # and cancels that came before their request started (ADR 0085).
        self._metric_requests: dict = {}
        self._cancelled_early: dict = {}
        # Offers made after a session load, waiting for "Add them?" (rule 15)
        self._session_offers: dict = {}

        # event → (handler, ordered arg-name list, request model). Built once;
        # the table IS the documented RPC surface. "?name" marks an optional
        # parameter.
        from ffast.protocol.messages import (
            CloseViewRequest, CreateSubsetRequest, DeclareSubsetRequest,
            DeleteObjectRequest, DeleteTabRequest, FreezeSubsetRequest, RenameObjectRequest, CancelTaskRequest,
            EmptyRequest, ExportSubsetRequest, ExportTabRequest, HideTabRequest, SetTabOrderRequest,
            ListDirRequest,
            LoadDatasetRequest, LoadModelRequest,
            LoadPredictionRequest, LoadSessionRequest, OpenViewRequest,
            ProbeDatasetKeysRequest, ProbeDatasetLengthRequest,
            ProbeDatasetEnergiesRequest, PreviewEnergySampleRequest,
            RequestMetricRequest, RequestPredictionArraysRequest,
            RequestSubdatasetArraysRequest, SaveSessionRequest, SaveTabRequest,
            DeleteUserMetricRequest, ExportUserMetricRequest, SaveUserMetricRequest,
            RequestMetricInputsRequest, CheckMetricRequest, TryMetricRequest,
            CancelTryMetricRequest, CancelMetricRequest, AddSessionMetricsRequest, SetDatasetUnitRequest,
            TraceRequest, RequestMetricBoxesRequest, OpenLiveRunRequest,
            StartPracticeRunRequest, LiveStopRequest, LiveSetStopOnAlarmRequest, LiveAttributionRequest,
            ForgetLiveRunRequest, MakeReferenceRequest, RunWithFfastRequest, WriteRunWithFfastRequest,
            SubmitJobRequest, WatchJobRequest, ScanRunFolderRequest,
            ScoreDialogInfoRequest, ScoreDatasetRequest, ContinueScoringRequest,
            StoredResultsRequest,
        )
        self._handlers: dict[str, _Route] = {
            control.LOAD_DATASET:               _Route(self._on_load_dataset, ["path", "dataset_type"], LoadDatasetRequest),
            control.LOAD_MODEL:                 _Route(self._on_load_model, ["path", "model_type"], LoadModelRequest),
            control.DELETE_OBJECT:              _Route(self._on_delete_object, ["fingerprint", "?delete_results"], DeleteObjectRequest),
            control.STORED_RESULTS:             _Route(self._on_stored_results, ["fingerprint"], StoredResultsRequest),
            control.FREEZE_SUBSET:              _Route(self._on_freeze_subset, ["fingerprint"], FreezeSubsetRequest),
            control.RENAME_OBJECT:              _Route(self._on_rename_object, ["fingerprint", "name"], RenameObjectRequest),
            control.CANCEL_TASK:                _Route(self._on_cancel_task, ["task_id"], CancelTaskRequest),
            control.SET_DATASET_UNIT:           _Route(self._on_set_dataset_unit, ["fingerprint", "dimension", "unit"], SetDatasetUnitRequest),
            control.CREATE_SUBSET:              _Route(self._on_create_subset, ["parent_fingerprint", "indices"], CreateSubsetRequest),
            control.DECLARE_SUBSET:             _Route(self._on_declare_subset, ["parent_fingerprint", "?indices"], DeclareSubsetRequest),
            control.REQUEST_SUBDATASET_ARRAYS:  _Route(self._on_request_subdataset_arrays, ["fingerprint"], RequestSubdatasetArraysRequest),
            control.PROBE_DATASET_KEYS:         _Route(self._on_probe_dataset_keys, ["path", "dataset_type"], ProbeDatasetKeysRequest),
            control.PROBE_DATASET_LENGTH:       _Route(self._on_probe_dataset_length, ["path"], ProbeDatasetLengthRequest),
            control.CANCEL_PROBE_DATASET_LENGTH: _Route(self._on_cancel_probe_dataset_length, [], EmptyRequest),
            control.PROBE_DATASET_ENERGIES:     _Route(self._on_probe_dataset_energies, ["path", "dataset_type"], ProbeDatasetEnergiesRequest),
            control.PREVIEW_ENERGY_SAMPLE:      _Route(self._on_preview_energy_sample, ["path", "dataset_type"], PreviewEnergySampleRequest),
            control.LIST_DIR:                   _Route(self._on_list_dir, ["?path"], ListDirRequest),
            control.LOAD_PREDICTION:            _Route(self._on_load_prediction, ["path", "dataset_fp"], LoadPredictionRequest),
            control.LOAD_EXAMPLE:               _Route(self._on_load_example, [], EmptyRequest),
            control.LOAD_ZERO_MODEL:            _Route(self._on_load_zero_model, [], EmptyRequest),
            control.REQUEST_PREDICTION_ARRAYS:  _Route(self._on_request_prediction_arrays, ["dataset_fp", "model_fp"], RequestPredictionArraysRequest),
            control.OPEN_VIEW:                  _Route(self._on_open_view, [], OpenViewRequest),
            control.CLOSE_VIEW:                 _Route(self._on_close_view, ["view_id"], CloseViewRequest),
            control.VIEW_COMMAND:               _Route(self._on_view_command, []),
            control.SAVE_SESSION:               _Route(self._on_save_session, ["path"], SaveSessionRequest),
            control.LOAD_SESSION:               _Route(self._on_load_session, ["path"], LoadSessionRequest),
            control.REQUEST_METRIC:             _Route(self._on_request_metric, ["metric_id", "request_id"], RequestMetricRequest),
            control.REQUEST_METRIC_CATALOG:     _Route(self._on_request_metric_catalog, [], EmptyRequest),
            control.REQUEST_TAB_LAYOUT:         _Route(self._on_request_tab_layout, [], EmptyRequest),
            control.EXPORT_SUBSET:              _Route(self._on_export_subset, ["fingerprint", "path"], ExportSubsetRequest),
            control.SAVE_TAB:                   _Route(self._on_save_tab, ["tab", "?previous_name"], SaveTabRequest),
            control.DELETE_TAB:                 _Route(self._on_delete_tab, ["name"], DeleteTabRequest),
            control.HIDE_TAB:                   _Route(self._on_hide_tab, ["name", "hidden"], HideTabRequest),
            control.SET_TAB_ORDER:              _Route(self._on_set_tab_order, ["names"], SetTabOrderRequest),
            control.EXPORT_TAB:                 _Route(self._on_export_tab, ["name"], ExportTabRequest),
            control.REQUEST_USER_METRICS:       _Route(self._on_request_user_metrics, [], EmptyRequest),
            control.SAVE_USER_METRIC:           _Route(self._on_save_user_metric, ["metric", "?previous_id", "?base_revision", "?overwrite"], SaveUserMetricRequest),
            control.DELETE_USER_METRIC:         _Route(self._on_delete_user_metric, ["id"], DeleteUserMetricRequest),
            control.REQUEST_METRIC_INPUTS:      _Route(self._on_request_metric_inputs, ["?dataset_fp", "?model_fp"], RequestMetricInputsRequest),
            control.CHECK_METRIC:               _Route(self._on_check_metric, ["metric", "?check_id", "?dataset_fp"], CheckMetricRequest),
            control.TRY_METRIC:                 _Route(self._on_try_metric, ["metric", "try_id", "?dataset_fp", "?model_fp"], TryMetricRequest),
            control.CANCEL_TRY_METRIC:          _Route(self._on_cancel_try_metric, ["try_id"], CancelTryMetricRequest),
            control.CANCEL_METRIC:              _Route(self._on_cancel_metric, ["request_id"], CancelMetricRequest),
            control.REQUEST_METRIC_BOXES:       _Route(self._on_request_metric_boxes, ["request_id", "?metric_id", "?base", "?setting", "?dataset_fp"], RequestMetricBoxesRequest),
            control.ADD_SESSION_METRICS:        _Route(self._on_add_session_metrics, ["offer_id"], AddSessionMetricsRequest),
            control.EXPORT_USER_METRIC:         _Route(self._on_export_user_metric, ["id"], ExportUserMetricRequest),
            control.TRACE:                      _Route(self._on_trace, ["kind", "text"], TraceRequest),
            control.PREPARE_PRACTICE_INPUT: _Route(self._on_prepare_practice_input, [], EmptyRequest),
            control.START_PRACTICE_RUN: _Route(self._on_start_practice_run, ["?kind"], StartPracticeRunRequest),
            control.HEAT_PRACTICE_RUN: _Route(self._on_heat_practice_run, ["run_id"], LiveStopRequest),
            control.DELETE_PRACTICE_RUN: _Route(self._on_delete_practice_run, ["run_id"], LiveStopRequest),
            control.OPEN_LIVE_RUN:              _Route(self._on_open_live_run, ["path"], OpenLiveRunRequest),
            control.LIVE_STOP:                  _Route(self._on_live_stop, ["run_id"], LiveStopRequest),
            control.LIVE_SET_STOP_ON_ALARM:     _Route(self._on_live_set_stop_on_alarm, ["run_id", "on"],
                                                       LiveSetStopOnAlarmRequest),
            control.LIVE_ATTRIBUTION:           _Route(self._on_live_attribution, ["run_id", "frame"],
                                                       LiveAttributionRequest),
            control.LIST_LIVE_RUNS:             _Route(self._on_list_live_runs, [], EmptyRequest),
            control.FORGET_LIVE_RUN:            _Route(self._on_forget_live_run, ["run_id"], ForgetLiveRunRequest),
            control.SCORE_DIALOG_INFO:           _Route(self._on_score_dialog_info, ["fingerprint", "?quick"],
                                                       ScoreDialogInfoRequest),
            control.SCORE_DATASET:              _Route(self._on_score_dataset, ["fingerprint", "bundle", "?frame_dt_fs", "?persist_ps"],
                                                       ScoreDatasetRequest),
            control.CONTINUE_SCORING:           _Route(self._on_continue_scoring, ["fingerprint"],
                                                       ContinueScoringRequest),
            control.MAKE_REFERENCE:             _Route(self._on_make_reference, ["train", "valid", "name", "folder"],
                                                       MakeReferenceRequest),
            control.PREPARE_RUN_WITH_FFAST:     _Route(self._on_prepare_run_with_ffast, ["input", "?job", "bundle"],
                                                       RunWithFfastRequest),
            control.WRITE_RUN_WITH_FFAST:       _Route(self._on_write_run_with_ffast,
                                                       ["input", "?job", "bundle", "originals"],
                                                       WriteRunWithFfastRequest),
            control.SUBMIT_JOB:                 _Route(self._on_submit_job, ["folder", "script"], SubmitJobRequest),
            control.WATCH_JOB:                  _Route(self._on_watch_job, ["job_id"], WatchJobRequest),
            control.SCAN_RUN_FOLDER:            _Route(self._on_scan_run_folder, ["?folder", "?job"], ScanRunFolderRequest),
        }

    # ── dispatch ────────────────────────────────────────────────────────────

    async def dispatch(self, event: str, args, kwargs, msg_id=None) -> None:
        """Route one authorized client event to its handler.

        A ``msg_id`` is the page's number for the message. While the handler
        runs, and in the tasks it starts, it is the request ``replies.reply``
        answers (ADR 0097). When the page keeps a timing log (ADR 0072) it
        also links what the handler queues to that message, and a TIMING note
        after the handler ends tells the page when it was read and done.
        """
        if msg_id is None:
            return await self._dispatch(event, args, kwargs)
        answering = replies.answering(msg_id)
        try:
            if not self.timing:
                return await self._dispatch(event, args, kwargs)
            read = timing.now_ms()
            token = timing.handling(self.outbound, msg_id)
            try:
                await self._dispatch(event, args, kwargs)
            finally:
                timing.done(token)
                timing.put_note(self.outbound, timing.handled(msg_id, read))
        finally:
            replies.done(answering)

    async def _dispatch(self, event: str, args, kwargs) -> None:
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
        if event != control.TRACE:
            trace.record("in", f"{trace.message(event, args, kwargs)}  [{self.peer}]")
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

        if not trace.enabled() or event == control.TRACE:
            await route.fn(**resolved, **extra)
            return
        started = time.perf_counter()
        try:
            await route.fn(**resolved, **extra)
        except Exception as exc:
            trace.record_error(f"{event} failed: {exc!r}", exc)
            raise
        trace.record("done", f"{event} {(time.perf_counter() - started) * 1000:.0f} ms")

    async def _on_trace(self, kind, text) -> None:
        """A line the browser reports: a click, a change, an error in the page."""
        first, _, rest = str(text).partition("\n")
        trace.record(kind, f"{first}  [{self.peer}]" + (f"\n{rest}" if rest else ""))

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
        full (replay is best-effort — a reconnect retries)."""
        try:
            self.outbound.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning(
                "State replay: outbound queue full, skipping %s", what
            )

    # ── scene accessors (built once; read live env state) ──────────────────────

    def get_prediction(self, dataset_fp, model_fp):
        """``get_prediction(dataset_fp, model_fp)`` for build_scene.

        Reads predicted forces and energies from the fingerprint-keyed cache
        (the same entries the Prediction-Only Array Channel serves). Returns
        ``None`` when neither is cached for the pair, so build_scene omits
        force arrows.
        """
        if not dataset_fp or not model_fp:
            return None
        from ffast.cache import CacheKey

        def cached(dt_key, field):
            entry = self.env.cache.get(CacheKey(dt_key, model_fp, dataset_fp).format())
            return None if entry is None else entry.get(field)

        forces, energies = cached("forces", "forces"), cached("energy", "energy")
        if forces is not None or energies is not None:
            return _PredictionView(forces, energies)
        # A frame subset's prediction is its parent's, at the parent frames
        # it holds (a subset made with SUB, say).
        dataset = self.env.datasets.get(dataset_fp)
        if (getattr(dataset, "isSubDataset", False) and not getattr(dataset, "isAtomFiltered", False)
                and getattr(dataset, "parent", None) is not None):
            parent = self.get_prediction(dataset.parent.fingerprint, model_fp)
            if parent is not None:
                frames = [int(i) for i in np.asarray(dataset.frames).ravel()]
                return _PredictionView(_frames_of(parent.forces, frames),
                                       _frames_of(parent.energies, frames))
        return None

    def get_metric(self, metric_id, params, dataset_fp, model_fp, wanted=None):
        """``get_metric(...)`` for build_scene: a metric over the whole dataset,
        read from the result store, the same one a 2D plot reads (ADR 0085).

        None when it is not stored. With ``wanted`` (a list), a result the
        store could compute has its arguments added to ``wanted`` (ADR 0082).
        Nothing is computed here, and a prediction is never started: a model or
        dataset that is gone, or a prediction not made for the dataset (a live
        model asked for one frame), gives None and atom colouring uses the
        frame on screen alone.
        """
        results = self.env.results
        result = results.peek(metric_id, params, dataset_fp, model_fp)
        if result is not None:
            return result
        if wanted is not None and results.can_compute(metric_id, params, dataset_fp, model_fp):
            wanted.append((metric_id, params, dataset_fp, model_fp))
        return None

    # ── draw first, colour later (ADR 0082) ─────────────────────────────────

    def _scene_metric(self, wanted):
        """``get_metric`` for a scene build: it never computes a whole-dataset
        result; one not yet cached is added to ``wanted``."""
        return lambda *args: self.get_metric(*args, wanted=wanted)

    @staticmethod
    def _mark_provisional(scene, wanted) -> None:
        """Say ``scene``'s colours are the frame's own while ``wanted`` is computed."""
        color_by = scene.atoms.color_by if scene is not None and scene.atoms else None
        if wanted and color_by is not None:
            color_by.note = "Computing colours over all structures…"
            color_by.dataset_range = True   # coming, not missing (ADR 0084)

    def _colour_later(self, view, wanted) -> None:
        """Compute ``wanted`` beside the message queue; send the view again when done."""
        if not wanted:
            return
        state = view.state
        task = asyncio.create_task(self._finish_colours(
            view, wanted, copy.deepcopy(state.parameters.get("ffast.atom_color")),
            state.dataset_ref, state.prediction_ref))
        self._colouring.add(task)
        task.add_done_callback(self._colouring.discard)

    async def colours_settled(self) -> None:
        """Wait until no view's colours are being computed."""
        while self._colouring:
            await asyncio.gather(*list(self._colouring), return_exceptions=True)

    async def _finish_colours(self, view, wanted, colouring, dataset_ref, prediction_ref) -> None:
        from ffast.protocol.rpc import pack
        from ffast.visualization.scene import ScenePatch
        from ffast.visualization.scene_builder import build_scene, fill_patch_from_scene

        from ffast.metrics.models import MetricResult

        data = self.env.data
        made, problems = [], []
        for args in wanted:
            # Shared with a 2D plot asking for the same metric meanwhile.
            outcome = await self.env.results.get(*args)
            if isinstance(outcome, MetricResult):
                made.append(outcome)
            else:
                made.append(None)
                problems.append(outcome.text)

        state = view.state
        if (self.views.get(state.view_id) is not view
                or state.parameters.get("ffast.atom_color") != colouring
                or state.dataset_ref != dataset_ref or state.prediction_ref != prediction_ref):
            return      # the user moved on; the result stays in the cache

        def finished(*args):
            for asked, result in zip(wanted, made):
                if tuple(args) == tuple(asked):
                    return result
            return self.get_metric(*args, wanted=[])

        try:
            scene = await asyncio.to_thread(
                build_scene, state, self.env.datasets.get, self.get_prediction,
                self.get_forces, data.metricExecutor, get_metric=finished,
                prediction_fields=getattr(data, "predictionFields", {}))
        except Exception as exc:
            self._log_scene_degrade("colours", state.view_id, exc)
            return
        color_by = scene.atoms.color_by if scene.atoms else None
        if problems and color_by is not None:
            color_by.note = (f"Colours over all structures could not be computed "
                             f"({problems[0]}); these are the frame's own.")
        patch = ScenePatch(view_id=state.view_id, from_version=view.version,
                           to_version=view.version, changed={"atoms"})
        fill_patch_from_scene(patch, scene)
        await self._emit(pack(control.SCENE_PATCH, [], patch.model_dump()))

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
        """Push the current server state to a (re)connecting client: what the
        window feed holds for a new window. A new connection has a new session
        with no views yet, so there are no views to send."""
        for data, label in self.feed.replay():
            self._emit_or_drop(data, label)
            logger.info("State replay: %s queued", label)

    # ── handlers ────────────────────────────────────────────────────────────

    async def _on_load_dataset(self, path, dataset_type, **kwargs) -> None:
        # msgpack deserializes tuples as lists; restore for prediction_keys
        if kwargs.get("prediction_keys"):
            kwargs["prediction_keys"] = [
                tuple(k) for k in kwargs["prediction_keys"]
            ]
        answer = self._load_answer("dataset", path)
        self.env.taskLoadDataset(
            path, dataset_type, on_error=answer.failed, on_done=answer.done, **kwargs
        )

    async def _on_load_model(self, path, model_type, **kwargs) -> None:
        answer = self._load_answer("model", path)
        self.env.taskLoadModel(path, model_type, on_error=answer.failed, on_done=answer.done)

    async def _on_delete_object(self, fingerprint, delete_results=False, **kwargs) -> None:
        self.env.deleteObject(fingerprint, delete_results=bool(delete_results))

    async def _on_stored_results(self, fingerprint, **kwargs) -> None:
        """For the delete dialog: the metric results deleting this object could
        delete. To the asking window only."""
        from ffast.protocol.rpc import pack
        out = {"fingerprint": fingerprint,
               **self.env.storedMetricResults(fingerprint)}
        await self._emit(pack(control.STORED_RESULTS_INFO, (), out))

    async def _on_freeze_subset(self, fingerprint, **kwargs) -> None:
        """Freeze a live subset's frames as a frozen subset, as the desktop's
        freeze button does. The frozen subset announces itself to every
        window like any new dataset; the live one carries on with its plot."""
        self.env.freezeSubDataset(fingerprint)

    async def _on_rename_object(self, fingerprint, name, **kwargs) -> None:
        """Rename a dataset or prediction, as the desktop's rename does. The
        name follows the desktop's rule (no ".", "/" or "\\"). setName pushes
        OBJECT_NAME_CHANGED, which server.py turns into a new announcement."""
        obj = self.env.datasets.get(fingerprint) or self.env.models.get(fingerprint)
        if obj is None:
            logger.warning("RENAME_OBJECT: %r not found", fingerprint)
            return
        name = (name or "").strip()
        if not name or any(c in name for c in "./\\"):
            logger.warning("Cannot rename to %r: a name needs a character and no '.', '/' or '\\'", name)
            return
        obj.setName(name)

    async def _on_cancel_task(self, task_id, **kwargs) -> None:
        """Cancel a running task (the Tasks list's Cancel). The task manager
        cancels it and sends TASK_DONE, so every window drops its row. Work
        already running in a thread finishes in the background, as on the
        desktop."""
        if self.env.getTask(task_id) is None:
            logger.info("CANCEL_TASK: task %r is not running", task_id)
            return
        self.env.eventPush("TASK_CANCEL", task_id)

    async def _on_set_dataset_unit(self, fingerprint, dimension, unit, **kwargs) -> None:
        """Name the unit of a dataset's energies or forces. The dataset (the
        one a subset was cut from) announces itself again to every window
        through ``DATASET_STATE_CHANGED`` → ``REMOTE_DATASET_META``."""
        dataset = self.env.datasets.get(fingerprint)
        if dataset is None:
            logger.warning("SET_DATASET_UNIT: dataset %r not found", fingerprint)
            return
        try:
            dataset.setUnit(dimension, unit)
        except ValueError as exc:
            logger.warning("SET_DATASET_UNIT: %s", exc)

    async def _on_create_subset(self, parent_fingerprint, indices, **kwargs) -> None:
        """Extract an atom subset or an element subset (ADR 0045 issue 12, ADR 0067).

        ``indices`` is the mixed spec the view "hide atoms" filter accepts —
        integers and element-symbol tokens ("C", "-H"). Elements alone make an
        element subset, which tests each atom's own element in every frame.
        Atom numbers make an atom subset, resolved against frame 0, and are
        refused when the structures differ: atom 3 is then a different atom in
        each. The new dataset announces itself via the ``DATASET_LOADED`` →
        ``REMOTE_DATASET_META`` subscriber; the window that asked hears the
        outcome (``SUBSET_EXTRACTED``), or why it was refused.
        """
        from ffast.loaders.dataset import atomNumbersRefusal, structuresDiffer
        from ffast.protocol.rpc import pack
        from ffast.visualization.scene_builder import (
            _resolve_filter_indices, parse_filter_tokens, resolve_element_tokens)

        async def _reply(text=None, error=None):
            await self._emit(pack(control.SUBSET_EXTRACTED, (),
                                  {"ok": error is None, "text": text, "error": error}))

        parent = self.env.datasets.get(parent_fingerprint)
        if parent is None:
            logger.warning("CREATE_SUBSET: parent %r not found", parent_fingerprint)
            await _reply(error="That dataset is no longer loaded.")
            return
        tokens = parse_filter_tokens(list(indices or []))
        try:
            if tokens.atoms and structuresDiffer(parent):
                raise ValueError(atomNumbersRefusal(tokens.atoms[0]))
            if tokens.atoms:
                idxs = _resolve_filter_indices(list(indices), parent.getElements(0))
                if not idxs:
                    raise ValueError("Those atoms leave nothing to extract.")
                self.env.createAtomFilteredDataset(parent, idxs)
                text = f"{len(idxs)} atom(s) of every frame"
            else:
                kept = resolve_element_tokens(list(indices or []),
                                              np.unique(parent.getAllElements()))
                view = self.env.createElementSubset(parent, kept)
                text = (f"{view.elementSymbols()} in {view.framesHoldingAtoms()} "
                        f"of {view.getN()} frames")
        except ValueError as exc:
            logger.info("CREATE_SUBSET refused: %s", exc)
            await _reply(error=str(exc))
            return
        except Exception as exc:
            logger.warning("CREATE_SUBSET: extract failed: %s", exc)
            await _reply(error=str(exc))
            return
        await _reply(text=text)

    async def _on_declare_subset(self, parent_fingerprint, indices=None, **kwargs) -> None:
        """Declare, update or hide a frame-index SubDataset (subbing).

        The desktop's ``BasicPlotWidget`` turns a plot viewport into a set of
        parent **configuration** indices and calls
        ``env.declareSubDataset(parent, model, idx, name)`` in-process (ADR 0021
        subbing). The browser has no in-process Environment, so it ships either
        the ``indices`` or the plot's ``view`` here — while SUB is ticked it
        sends the view on every zoom, and ``ffast.session.subbing`` works out
        the frames it covers. ``active=False`` (SUB unticked) hides the subset.
        The new or changed ``SubDataset`` announces itself to every window via
        ``REMOTE_DATASET_META`` (server.py), and is usable by the 3D view and
        other tabs (PRD stories 61-62). The window that sent this is told which
        subset it made (``SUBSET_DECLARED``), so it can show it.
        """
        from ffast.loaders.dataset import DatasetView  # ADR 0047 Phase 5c
        from ffast.protocol.rpc import pack

        parent = self.env.datasets.get(parent_fingerprint)
        if parent is None:
            logger.warning("DECLARE_SUBSET: parent %r not found", parent_fingerprint)
            return
        model_fp = kwargs.get("model_fp")
        model = self.env.models.get(model_fp) if model_fp else None
        if model_fp and model is None:
            # A model only the window holds (the zero model): a subset without
            # it would have another fingerprint than the window's.
            logger.warning("DECLARE_SUBSET: model %r not found", model_fp)
            return
        name = kwargs.get("name") or "Subset"
        existing = self.env.datasets.get(DatasetView.subsetFingerprint(parent, model, name))

        if kwargs.get("active", True) is False:
            if existing is not None:
                existing.setActive(False)
            return

        view = kwargs.get("view")
        if view is not None:
            try:
                idx = await self._frames_in_view(view, parent, parent_fingerprint, model_fp)
            except Exception as exc:
                logger.warning("DECLARE_SUBSET: view of %r failed: %s", parent_fingerprint, exc)
                return
            if idx is None:
                return
        else:
            idx = [int(i) for i in (indices or [])]
        if not idx:
            # Zoomed onto no frames: the subset keeps the frames it had.
            logger.info("DECLARE_SUBSET: no frames for %r", parent_fingerprint)
            return
        unchanged = (existing is not None and getattr(existing, "active", True)
                     and list(existing.frames) == idx)
        if not unchanged:   # the same frames: nothing to rebuild or announce
            try:
                self.env.declareSubDataset(parent, model, idx, name)
            except Exception as exc:
                logger.warning("DECLARE_SUBSET: declareSubDataset failed: %s", exc)
                return
        await self._emit(pack(control.SUBSET_DECLARED, [], {
            "fingerprint": DatasetView.subsetFingerprint(parent, model, name),
            "parent_fingerprint": parent_fingerprint, "model_fp": model_fp, "name": name,
        }))

    async def _frames_in_view(self, view, parent, parent_fingerprint, model_fp):
        """The parent frames a plot's view covers, or None when a metric it
        reads is missing. The metrics come from the result store first (ADR
        0085), so the frame search only reads them."""
        from ffast.metrics.models import MetricResult
        from ffast.metrics.registry import default_registry
        from ffast.session.subbing import frames_in_view, metrics_read

        pairs = metrics_read(view, default_registry)
        results = self.env.results if pairs else None      # a timeline reads no metric
        for metric_id, params in pairs:
            outcome = await results.get(metric_id, params, parent_fingerprint, model_fp)
            if not isinstance(outcome, MetricResult):
                logger.info("DECLARE_SUBSET: %s", outcome.text)
                return None

        def value_of(metric_id, params):
            result = results.peek(metric_id, params, parent_fingerprint, model_fp)
            return None if result is None else result.values

        return await asyncio.to_thread(
            frames_in_view, view, n_frames=parent.getN(), value_of=value_of,
            registry=default_registry, dataset=parent)

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
            if isinstance(raw, FlatFrames):
                raw = raw.values
            elif isinstance(raw, list):
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

        # A reader's file (ADR 0095): its contents() lists the names; the reply
        # keeps the shape the ASE probe gives. A reader that does not take the
        # file (ASE's does not take .xyz and .extxyz until step 4) leaves it to
        # the probe below.
        from ffast.core.loading_coordinator import LoadingCoordinator
        from ffast.readers import chunks, registry as readers
        reader_class = LoadingCoordinator._datasetReader(dataset_type, path)

        energy_sampling = False
        if reader_class is not None:
            try:
                with chunks.reader_errors(reader_class.name, path):
                    contents = reader_class(path).contents() or {}
                energy_keys = list(contents.get("energies") or [])
                force_keys = list(contents.get("forces") or [])
                # A sample by energy (ADR 0096) needs a reader that lists each
                # frame's energies, and a file that names one.
                energy_sampling = bool(energy_keys) and callable(getattr(reader_class, "energies", None))
            except Exception as exc:
                logger.warning("PROBE_DATASET_KEYS error for %r: %s", path, exc,
                               extra={"popup": False})
                error = str(exc)
        else:
            try:
                from ffast.io.frame_index import read_first_frame
                from ffast.loaders.ase import aseDatasetLoader

                first_atoms = read_first_frame(path)
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
                logger.warning("PROBE_DATASET_KEYS error for %r: %s", path, exc, extra={"popup": False})
                logger.warning(
                    "  server view: host=%s cwd=%s exists=%s parent_exists=%s",
                    *_probe_path_diagnostics(path), extra={"popup": False},
                )
                error = str(exc)

        # The Type the file's readers pick (the ending, then can_read): the page
        # starts on it unless the user chose a Type (ADR 0095, decision 9).
        suggested = next((r.name for r in readers.readers_for(path)
                          if not getattr(r, "hidden", False)), None)

        from ffast.protocol import DatasetKeysResponse
        data = pack(
            control.DATASET_KEYS_RESPONSE,
            (path,),
            DatasetKeysResponse(
                reader=suggested,
                energy_keys=energy_keys,
                force_keys=force_keys,
                has_calculator_energy=has_calculator_energy,
                has_calculator_forces=has_calculator_forces,
                error=error,
                energy_sampling=energy_sampling,
            ).model_dump(),
        )
        await self._emit(data)
        logger.debug("DATASET_KEYS_RESPONSE queued for %r", path)

    def _energy_reader(self, path, dataset_type):
        """The reader that would load ``path`` as ``dataset_type``, or a
        ReaderError saying the file cannot be sampled by energy."""
        from ffast.core.loading_coordinator import LoadingCoordinator
        from ffast.readers import ReaderError
        reader_class = LoadingCoordinator._datasetReader(dataset_type, path)
        if reader_class is None:
            raise ReaderError(f"{os.path.basename(path)} cannot be sampled by energy: the "
                              f"{dataset_type} type has no reader that lists energies")
        return reader_class

    async def _on_probe_dataset_energies(self, path, dataset_type, energy=None, **kwargs) -> None:
        """The file's energies as a histogram, for the load dialog (ADR 0096).
        The first ask reads every frame's energy (worker processes, seconds to a
        minute for millions of frames); later asks use the kept copy."""
        from ffast.protocol.rpc import pack
        from ffast.readers.energy_sample import energy_histogram

        def work():
            return energy_histogram(self._energy_reader(path, dataset_type), path, energy)

        try:
            out = {**await asyncio.to_thread(work), "error": None}
        except Exception as exc:
            logger.warning("PROBE_DATASET_ENERGIES %r: %s", path, exc, extra={"popup": False})
            out = {"error": str(exc)}
        await self._emit(pack(control.DATASET_ENERGIES, (path,), out))

    async def _on_preview_energy_sample(self, path, dataset_type, sampling=None,
                                        preview_id=None, **kwargs) -> None:
        """What a load with ``sampling`` would keep, per bin (ADR 0096)."""
        from ffast.protocol.rpc import pack
        from ffast.readers.energy_sample import energy_preview

        def work():
            return energy_preview(self._energy_reader(path, dataset_type), path, sampling or {})

        try:
            out = await asyncio.to_thread(work)
        except Exception as exc:
            logger.warning("PREVIEW_ENERGY_SAMPLE %r: %s", path, exc, extra={"popup": False})
            out = {"error": str(exc)}
        await self._emit(pack(control.ENERGY_SAMPLE_PREVIEW, (path,), {**out, "preview_id": preview_id}))

    async def _on_probe_dataset_length(self, path, **kwargs) -> None:
        """Count frames in the background; reply as it grows, then once with done=True.

        Returns at once: a count of a big file must not hold up the message
        loop (frame index plan 2026-11-01). A newer count cancels the older.
        """
        from ffast.io import frame_index as fi
        from ffast.protocol import DatasetLengthResponse
        from ffast.protocol.rpc import pack

        self._stop_length_probe()
        cancel = threading.Event()
        self._length_probe_cancel = cancel
        loop = asyncio.get_running_loop()

        async def send(n, error=None, done=False):
            if cancel.is_set():
                return
            await self._emit(pack(
                control.DATASET_LENGTH_RESPONSE, (path,),
                DatasetLengthResponse(n=n, error=error, done=done).model_dump(),
            ))

        async def run():
            n: int | None = None
            error: str | None = None
            try:
                if fi.is_indexable(path):
                    index = await asyncio.to_thread(
                        fi.get_frame_index, path, cancel=cancel,
                        progress=lambda k: asyncio.run_coroutine_threadsafe(send(k), loop),
                    )
                    n = len(index)
                else:
                    from ffast.core.data_types import AtomsList
                    n = await asyncio.to_thread(AtomsList.calc_dataset_length_static, path)
                logger.info("PROBE_DATASET_LENGTH %r: n=%d", path, n)
            except fi.FrameIndexCancelled:
                return
            except Exception as exc:
                logger.warning("PROBE_DATASET_LENGTH error for %r: %s", path, exc, extra={"popup": False})
                logger.warning(
                    "  server view: host=%s cwd=%s exists=%s parent_exists=%s",
                    *_probe_path_diagnostics(path), extra={"popup": False},
                )
                error = str(exc)
            await send(n, error, done=True)
            logger.debug("DATASET_LENGTH_RESPONSE queued for %r", path)

        self._length_probe_task = asyncio.create_task(run())

    def _stop_length_probe(self) -> None:
        if self._length_probe_cancel is not None:
            self._length_probe_cancel.set()

    async def _on_cancel_probe_dataset_length(self, **kwargs) -> None:
        """The Load dialog moved on to another file, or closed without Load."""
        self._stop_length_probe()

    async def _on_prepare_practice_input(self, **kwargs) -> None:
        from ffast.live.local_launch import prepare_tutorial_input
        from ffast.protocol.rpc import pack

        try:
            out = await asyncio.to_thread(prepare_tutorial_input)
        except (ValueError, OSError) as exc:
            out = {"error": str(exc)}
        await self._emit(pack(control.PRACTICE_INPUT_PREPARED, (), out))

    async def _on_start_practice_run(self, kind="replay", **kwargs) -> None:
        from ffast.live.local_launch import start_practice_run
        from ffast.live.live_run import open_live_run
        from ffast.protocol.rpc import pack

        try:
            run_id, folder = await start_practice_run(kind or "replay")
            open_live_run(self.env, folder, self._broadcast)
            out = {"run_id": run_id}
        except (ValueError, OSError) as exc:
            logger.warning("START_PRACTICE_RUN: %s", exc)
            out = {"error": str(exc)}
        await self._emit(pack(control.PRACTICE_RUN_STARTED, (), out))

    async def _on_heat_practice_run(self, run_id, **kwargs) -> None:
        from ffast.live.local_launch import heat_practice_run
        from ffast.protocol.rpc import pack

        out = {"run_id": run_id, "action": "heat"}
        try:
            heat_practice_run(run_id)
        except (ValueError, OSError) as exc:
            out["error"] = str(exc)
        await self._emit(pack(control.PRACTICE_RUN_RESULT, (), out))

    async def _on_delete_practice_run(self, run_id, **kwargs) -> None:
        from ffast.live.local_launch import delete_practice_run
        from ffast.protocol.rpc import pack

        out = {"run_id": run_id, "action": "delete"}
        try:
            delete_practice_run(run_id)
            runs = getattr(self.env, "live_runs", {})
            run = runs.get(run_id)
            if run and run.registered:
                await self._on_delete_object(run.fingerprint)
        except (ValueError, OSError) as exc:
            out["error"] = str(exc)
        await self._emit(pack(control.PRACTICE_RUN_RESULT, (), out))
        await self._on_list_live_runs()

    async def _on_open_live_run(self, path, **kwargs) -> None:
        """Follow a live LAMMPS run folder (ADR 0087). Its frames appear as a
        dataset; its scores go to every window in ``LIVE_RUN_UPDATE``. A path
        that is not a run folder is answered to this window only, with ``error``."""
        from ffast.live.live_run import open_live_run
        from ffast.protocol.rpc import pack

        try:
            open_live_run(self.env, path, self._broadcast)
        except (ValueError, OSError) as exc:
            logger.warning("OPEN_LIVE_RUN %r: %s", path, exc)
            await self._emit(pack(control.LIVE_RUN_UPDATE, (), {"path": path, "error": str(exc)}))

    async def _on_live_stop(self, run_id, **kwargs) -> None:
        """"Stop now" (ADR 0086): writes the run's stop request; its wrapper
        stops LAMMPS. A run that is not open or has ended is answered with ``error``."""
        from ffast.live.live_run import stop_run
        from ffast.protocol.rpc import pack

        try:
            stop_run(self.env, run_id)
        except (ValueError, OSError) as exc:
            logger.warning("LIVE_STOP %r: %s", run_id, exc)
            await self._emit(pack(control.LIVE_RUN_UPDATE, (), {"run_id": None, "error": str(exc)}))

    async def _on_live_set_stop_on_alarm(self, run_id, on, **kwargs) -> None:
        """Turn a live run's stop on alarm on or off (``control.json``, read by its scorer)."""
        from ffast.live.live_run import set_stop_on_alarm
        from ffast.protocol.rpc import pack

        try:
            set_stop_on_alarm(self.env, run_id, on)
        except (ValueError, OSError) as exc:
            logger.warning("LIVE_SET_STOP_ON_ALARM %r: %s", run_id, exc)
            await self._emit(pack(control.LIVE_RUN_UPDATE, (), {"run_id": None, "error": str(exc)}))

    async def _on_live_attribution(self, run_id, frame, track=None, score=None, **kwargs) -> None:
        """Attribution of one live frame (ADR 0087), computed in a thread and
        answered to this window only; ``error`` says why when it cannot be made."""
        from ffast.live.live_run import find_run
        from ffast.protocol.rpc import pack

        test = (track, score) if track and score else None
        try:
            run = find_run(self.env, run_id, scored=True)
            out = await asyncio.to_thread(run.attribution, int(frame), test)
        except (ValueError, OSError) as exc:
            out = {"run_id": run_id, "frame": frame, "error": str(exc)}
        await self._emit(pack(control.LIVE_ATTRIBUTION_RESULT, (), out))

    # ── scoring any dataset (ADR 0093) ──────────────────────────────────────
    async def _on_score_dialog_info(self, fingerprint, quick=False, **kwargs) -> None:
        """What the Score dialog needs: the recorded references, whether DetectAna is
        there, the time between frames the file suggests, a refusal, the items made
        already. To this window only. Answered from a task of its own: the first answer
        imports DetectAna, and the window's other messages (a view command waiting for
        its view to open) must not wait for that."""
        task = asyncio.create_task(self._score_dialog_info(fingerprint, bool(quick)))
        self._score_asks.add(task)
        task.add_done_callback(self._score_asks.discard)

    async def _score_dialog_info(self, fingerprint, quick=False) -> None:
        from ffast.live import scored_items as si
        from ffast.protocol.rpc import pack

        dataset = self.env.datasets.get(fingerprint)
        if dataset is None:
            out = {"fingerprint": fingerprint, "error": f"dataset {fingerprint!r} is not loaded"}
        else:
            try:
                out = await asyncio.to_thread(si.dialog_info, dataset, None, quick)
            except Exception as exc:
                logger.warning("SCORE_DIALOG_INFO %r: %s", fingerprint, exc)
                out = {"fingerprint": fingerprint, "error": f"{type(exc).__name__}: {exc}"}
        await self._emit(pack(control.SCORE_DIALOG_INFO_RESULT, (), out))

    async def _on_score_dataset(self, fingerprint, bundle, frame_dt_fs=None, persist_ps=None, **kwargs) -> None:
        """Score a loaded dataset with a reference. The scored item appears under the
        dataset at once and fills in as frames are scored (a task, cancellable). The
        same key as an item made before opens it again, and scores only what is missing."""
        import os
        from pathlib import Path

        from ffast.live import scored_items as si
        from ffast.live.detectana_api import DetectAnaMissing, require_detectana
        from ffast.live.scored_item import open_scored_item
        from ffast.protocol.rpc import pack

        dataset = self.env.datasets.get(fingerprint)
        try:
            if dataset is None:
                raise ValueError(f"dataset {fingerprint!r} is not loaded")
            bundle_dir = Path(os.path.expanduser(bundle))
            settings = si.make_settings(None if frame_dt_fs is None else frame_dt_fs / 1000.0, persist_ps)

            def prepare():
                require_detectana()         # the first import takes a moment: not on the event loop
                return si.prepare_item(dataset, bundle_dir, settings)

            iid, folder, existed = await asyncio.to_thread(prepare)
            scored = open_scored_item(self.env, folder, dataset, self._broadcast)
            state = (si.read_item(folder) or {}).get("state")
            started = not existed or state != "done"
            if started:
                self._start_scoring(dataset, folder, iid)
            out = {"item_id": iid, "fingerprint": scored.fingerprint, "started": started}
        except (ValueError, OSError, DetectAnaMissing) as exc:
            logger.warning("SCORE_DATASET %r: %s", fingerprint, exc)
            out = {"error": str(exc)}
            scored = None
        await self._emit(pack(control.SCORE_STARTED, (), out))
        if scored is not None:
            scored.announce()       # after the answer: the window opens the item's tab on this

    async def _on_continue_scoring(self, fingerprint, **kwargs) -> None:
        """Score the frames an incomplete scored item is missing."""
        from ffast.live import scored_items as si
        from ffast.live.scored_item import SCORED_PREFIX
        from ffast.protocol.rpc import pack

        scored = (getattr(self.env, "scored_items", None) or {}).get(fingerprint.removeprefix(SCORED_PREFIX))
        if scored is None:
            out = {"error": f"scored item {fingerprint!r} is not open"}
        else:
            self._start_scoring(scored.dataset.parent, scored.folder, scored.run_id)
            out = {"item_id": scored.run_id, "fingerprint": scored.fingerprint, "started": True}
        await self._emit(pack(control.SCORE_STARTED, (), out))
        if scored is not None:
            scored.announce()

    def _start_scoring(self, parent, folder, iid) -> None:
        """Run ``score_dataset`` for the item in ``folder`` as a visible, cancellable task."""
        from ffast.live import scored_items as si
        from ffast.live import run_folder as rf
        from ffast.protocol.rpc import pack

        loop = asyncio.get_running_loop()
        item = si.read_item(folder) or {}
        env = self.env

        def work(taskID=None):
            def stopped():
                info = env.getTask(taskID)
                return info is None or info["task"].cancelling() > 0 or info["task"].cancelled()

            def progress(done, total):
                data = pack("TASK_PROGRESS", [taskID], {
                    "message": f"Scoring: {done} of {total} frames",
                    "progress": done / total if total else 1.0, "error": False})
                loop.call_soon_threadsafe(self._broadcast, data)

            try:
                si.score_dataset(parent, item["bundle"], item["settings"], stopped=stopped, progress=progress)
            except Exception as exc:
                text = f"{type(exc).__name__}: {exc}"
                si.write_item_json(folder, state="failed", reason=text)
                rf.append_event(folder, "failed", reason=text)
                raise

        env.newTask(work, visual=True, name=f"Scoring {parent.getName()}", threaded=True,
                    taskKey=f"score:{iid}")

    async def _on_list_live_runs(self, **kwargs) -> None:
        """The "Live runs" list (ADR 0087): every run the wrapper started on
        this account, read from the pointers in ``~/.ffast/live-runs``."""
        import os

        from ffast.live import run_folder as rf
        from ffast.live.runs_list import list_runs
        from ffast.protocol.rpc import pack

        open_ids = set(getattr(self.env, "live_runs", None) or {})
        rows = await asyncio.to_thread(list_runs, rf.live_runs_dir(os.environ), open_ids=open_ids)
        await self._emit(pack(control.LIVE_RUNS, (), {"runs": rows}))

    async def _on_forget_live_run(self, run_id, **kwargs) -> None:
        """Drop a run from the list (its pointer, never its folder), then send the list."""
        import os

        from ffast.live import run_folder as rf
        from ffast.live.runs_list import forget_run

        try:
            forget_run(rf.live_runs_dir(os.environ), run_id)
        except (ValueError, OSError) as exc:
            logger.warning("FORGET_LIVE_RUN %r: %s", run_id, exc)
        await self._on_list_live_runs()

    async def _on_make_reference(self, train, valid, name, folder, every=100, **kwargs) -> None:
        """"Make reference" (ADR 0086): fit a bundle on one loaded dataset,
        calibrate it on another, write it and its include file on the server.
        Refuses one dataset given twice. Answered to this window only."""
        from ffast.live.detectana_api import DetectAnaMissing
        from ffast.protocol.rpc import pack

        try:
            out = await asyncio.to_thread(self._make_reference, train, valid, name, folder, int(every))
        except (ValueError, DetectAnaMissing) as exc:
            out = {"error": str(exc)}
        except Exception as exc:   # a fit can fail in ways worth reading in full
            logger.exception("MAKE_REFERENCE failed")
            out = {"error": f"{type(exc).__name__}: {exc}"}
        await self._emit(pack(control.REFERENCE_MADE, (), out))

    async def _on_prepare_run_with_ffast(self, input, job, bundle, **kwargs) -> None:
        """"Run with FFAST" check and review (ADR 0086): loads the job's LAMMPS
        module on this node, places the include, returns the copies as diffs.
        Writes nothing. Answered to this window only."""
        from ffast.live.run_with_ffast import prepare

        await self._run_with_ffast(control.RUN_WITH_FFAST_PREPARED, prepare, input, job, bundle, **kwargs)

    async def _on_write_run_with_ffast(self, input, job, bundle, originals, **kwargs) -> None:
        """"Write copies": the approved copies next to the originals, after
        checking the originals did not change since the review."""
        from ffast.live.run_with_ffast import write

        await self._run_with_ffast(control.RUN_WITH_FFAST_WRITTEN, write, input, job, bundle, originals, **kwargs)

    async def _on_submit_job(self, folder, script, **kwargs) -> None:
        """Submit a job script from this node, without this server job's Slurm
        variables ("Run with FFAST", submit from the server's node)."""
        import os

        from ffast.live.job_submit import SubmitError, submit_on_node
        from ffast.protocol.rpc import pack

        folder = os.path.expanduser(folder)
        try:
            job_id = await asyncio.to_thread(submit_on_node, folder, script)
            out = {"job_id": job_id, "via": "server", "folder": folder, "script": script}
        except SubmitError as exc:
            out = {"error": str(exc), "folder": folder, "script": script}
        await self._emit(pack(control.JOB_SUBMITTED, (), out))

    async def _on_scan_run_folder(
        self, folder=None, job=None, target="cluster", input=None, **kwargs
    ) -> None:
        """Run with FFAST: what a run folder holds (job scripts, inputs,
        references), for the Choose step. Reads only."""
        from ffast.live.run_folder_scan import scan
        from ffast.protocol.rpc import pack

        try:
            out = await asyncio.to_thread(
                scan, folder, job, target=target, input=input
            )
        except Exception as exc:   # a scan that breaks must say so, not leave the dialog waiting
            logger.exception("SCAN_RUN_FOLDER %r failed", folder)
            out = {"folder": folder, "problem": f"{type(exc).__name__}: {exc}"}
        out["asked"] = folder or ""
        out["target"] = target
        await self._emit(pack(control.RUN_FOLDER_SCANNED, (), out))

    async def _on_watch_job(self, job_id, **kwargs) -> None:
        """Follow a submitted job and push JOB_STATE on each change until its
        run's first frame or its end. One watch per job; ends with the window."""
        if job_id in self._job_watches:
            return
        task = asyncio.create_task(self._watch_job(job_id))
        self._job_watches[job_id] = task
        task.add_done_callback(lambda _t: self._job_watches.pop(job_id, None))

    async def _watch_job(self, job_id, poll_s: float | None = None) -> None:
        from ffast.live.job_watch import POLL_S, JobWatch
        from ffast.protocol.rpc import pack

        watch = JobWatch(job_id)
        last = None
        while True:
            try:
                state = await asyncio.to_thread(watch.poll)
            except Exception as exc:   # a broken watch must say so, not hang the dialog
                logger.exception("WATCH_JOB %s failed", job_id)
                state = {"job_id": job_id, "state": "ended", "reason": f"{type(exc).__name__}: {exc}"}
                watch.done = True
            if state != last:
                await self._emit(pack(control.JOB_STATE, (), state))
                last = state
            if watch.done:
                return
            await asyncio.sleep(POLL_S if poll_s is None else poll_s)

    async def _run_with_ffast(self, reply, fn, *args, **kwargs) -> None:
        from ffast.live.detectana_api import DetectAnaMissing
        from ffast.protocol.rpc import pack

        opts = {
            k: kwargs[k]
            for k in (
                "data",
                "data_path",
                "data_index",
                "stop_on_alarm",
                "target",
                "executable",
            )
            if k in kwargs
        }
        try:
            out = await asyncio.to_thread(fn, *args, **opts)
            if (
                reply == control.RUN_WITH_FFAST_WRITTEN
                and out.get("ok")
                and opts.get("target") == "local"
            ):
                from ffast.live.local_launch import start_local_run

                launch = out["launch"]
                run_id, folder = await start_local_run(
                    launch["executable"],
                    launch["input"],
                    practice=kwargs.get("practice", False),
                )
                out.update(
                    run_id=run_id,
                    run_dir=folder,
                    practice=kwargs.get("practice", False),
                )
        except DetectAnaMissing as exc:
            out = {"ok": False, "refusal": {"file": "bundle", "reason": str(exc), "line": None,
                                            "hand_edit": "", "text": str(exc)}}
        except Exception as exc:   # an unexpected failure is worth reading in full
            logger.exception("%s failed", reply)
            out = {"ok": False, "refusal": {"file": None, "reason": f"{type(exc).__name__}: {exc}",
                                            "line": None, "hand_edit": "", "text": f"{type(exc).__name__}: {exc}"}}
        await self._emit(pack(reply, (), out))

    def _make_reference(self, train, valid, name, folder, every) -> dict:
        import os

        from ffast.live.reference import ReferenceRefused, dataset_frames, make_reference

        if train == valid:
            raise ReferenceRefused("training and validation are the same dataset; the thresholds are "
                                   "calibrated on frames the fit has not seen")
        sets = []
        for fp, role in ((train, "training"), (valid, "validation")):
            ds = self.env.datasets.get(fp)
            if ds is None:
                raise ReferenceRefused(f"the {role} dataset is not loaded")
            sets.append(dataset_frames(ds, f"the {role} dataset"))
        (t_sym, t_pos), (v_sym, v_pos) = sets
        out = Path(os.path.expanduser(folder)) / name
        return make_reference(t_pos, v_pos, t_sym, out, every=every, valid_symbols=v_sym)

    async def _on_list_dir(self, path=None, **kwargs) -> None:
        """List a server-side directory and push DIR_LISTING.

        Full-filesystem browse backing the web file picker. An empty/None
        ``path`` starts at ``env.workdir`` (``--workdir``, else the folder the
        server was started from); ``home`` is sent back for the ⌂ button. Each entry is
        ``{name, is_dir, size}``; directories sort first, then files, both
        case-insensitive. ``parent`` is None at the filesystem root.
        """
        import os
        from ffast.protocol.rpc import pack

        home = os.path.expanduser("~")
        start = getattr(self.env, "workdir", None) or os.getcwd()
        abspath = os.path.abspath(os.path.expanduser(path)) if path else start

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
        answer = self._load_answer("prediction", path, dataset_fp=dataset_fp)
        self.env.taskLoadPrepredictedDataset(
            path, dataset_fp,
            selected_energy_key=selected_energy_key,
            selected_force_key=selected_force_key,
            on_error=answer.failed,
            on_done=answer.done,
            reader=kwargs.get("reader"),
        )

    async def _on_load_zero_model(self, **kwargs) -> None:
        """Load the zero baseline model; a second request loads nothing,
        and its final reply says it was already loaded."""
        answer = self._load_answer("zero", None)
        self.env.taskLoadZeroModel(on_error=answer.failed, on_done=answer.done)

    async def _on_load_example(self, **kwargs) -> None:
        """Load the SPICE pair in ``examples/data/variable-sized molecular``:
        the dataset, then its prediction, in one task so the prediction waits
        for the dataset. Both announce themselves like any other load. A server
        installed without the repository has no examples; the window is told."""
        from ffast.session.examples import example_data_dir

        data = example_data_dir() / "variable-sized molecular"
        dataset_path = str(data / "dataset.xyz")
        prediction_path = str(data / "prediction.xyz")
        answer = self._load_answer("example", dataset_path)
        if not (data / "dataset.xyz").is_file() or not (data / "prediction.xyz").is_file():
            answer.failed(
                "this server has no examples (it was installed without the repository)"
            )
            return
        env = self.env
        dataset_failed = answer.failed
        prediction_failed = answer.failed

        def load(taskID=None):
            # The Loading Coordinator's load* take on_error; the Environment's
            # delegates do not pass it on.
            if env.getDatasetFromPath(dataset_path) is None:
                try:
                    env.loading.loadDataset(dataset_path, "ase (auto)", on_error=dataset_failed)
                except Exception as exc:
                    dataset_failed(exc)
                    raise
            dataset_fp = env.getKeyFromPath(dataset_path)
            if dataset_fp is None:
                return
            if env.getKeyFromPath(prediction_path) is None:
                try:
                    env.loading.loadPrepredictedDataset(
                        prediction_path, dataset_fp,
                        on_error=prediction_failed,
                    )
                except Exception as exc:
                    prediction_failed(exc)
                    raise
            model_fp = env.getKeyFromPath(prediction_path)
            if model_fp is None:
                answer.failed("its prediction did not load (see the server log)")
            else:
                answer.send(dataset_fp=dataset_fp, model_fp=model_fp)

        env.newTask(load, visual=True, name="Loading example", threaded=True)

    def _load_answer(self, kind: str, path, *, dataset_fp=None) -> "_LoadAnswer":
        """The final reply to the load request being handled (ADR 0097).

        Built in the handler, so it knows the request's number and what the
        server held for ``path`` before the load. The load task calls
        ``failed(exc)`` or ``done()`` from its thread; the first call wins.
        """
        return _LoadAnswer(self, kind, path, dataset_fp)

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
            if isinstance(raw, FlatFrames):
                raw = raw.values
            elif isinstance(raw, list):
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

        #print("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

        copied_metric = False
        view_id = kwargs.get("view_id") or str(uuid.uuid4())
        #print(view_id)
        dataset_ref = kwargs.get("dataset_ref") or None

        if view_id not in self.views:
            view = VisualizationView(view_id)
            self.views[view_id] = view
            # A frame subset opens looking like its parent: the stage
            # parameters and features (colouring, display, bonds) and the
            # camera. Picks name atoms of one view, so they stay behind.
            like = self.views.get(kwargs.get("start_from"))
            if like is not None:
                view.state.parameters = copy.deepcopy(like.state.parameters)
                view.state.enabled_features = list(like.state.enabled_features)
                view.state.camera = like.state.camera.model_copy()
                copied_metric = (self._colour_source(view) or "").startswith("metric:")
        else:
            view = self.views[view_id]
        #print('only_forces' in view.state.enabled_features)

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
        wanted = []
        try:
            snapshot = view.snapshot(
                get_dataset=self.env.datasets.get,
                get_prediction=self.get_prediction,
                get_forces=self.get_forces,
                executor=self.env.data.metricExecutor,
                get_metric=self._scene_metric(wanted),
                prediction_fields=getattr(
                    self.env.data, "predictionFields", {}),
            )
        except Exception as exc:
            self._log_scene_degrade("OPEN_VIEW", view_id, exc)
            snapshot = view.snapshot()
            wanted = []
        self._mark_provisional(snapshot.scene, wanted)
        data = pack(control.SCENE_SNAPSHOT, [], snapshot.model_dump())
        #if snapshot.scene.only_forces: print("only forces sent!")
        await self._emit(data)
        self._colour_later(view, wanted)
        logger.info(
            "OPEN_VIEW: view_id=%r dataset_ref=%r prediction_ref=%r scene_version=%d",
            view_id, dataset_ref, view.state.prediction_ref, view.version,
        )
        if copied_metric:
            # The copied colouring is one more view coloured by a metric.
            self.feed.changed(control.METRIC_USERS)

    async def _on_close_view(self, view_id, **kwargs) -> None:
        self.views.pop(view_id, None)
        logger.info("CLOSE_VIEW: view_id=%r", view_id)
        self.feed.changed(control.METRIC_USERS)

    def _colour_source(self, view) -> str | None:
        return view.state.parameters.get("ffast.atom_color", {}).get("source")

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

        before = self._colour_source(view)
        result = view.apply_command(cmd)
        if self._colour_source(view) != before:
            # Who uses a metric can change, so Delete is neither blocked nor
            # allowed on old news (ADR 0057 rule 7).
            self.feed.changed(control.METRIC_USERS)

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
        wanted = []
        if result.success and result.patch and result.patch.changed:
            view_state = view.state
            only_forces_selected = "only_forces" in view_state.enabled_features
            try:
                scene = await asyncio.to_thread(
                    build_scene, view_state, self.env.datasets.get,
                    self.get_prediction, self.get_forces,
                    self.env.data.metricExecutor, get_metric=self._scene_metric(wanted),
                    prediction_fields=getattr(
                        self.env.data, "predictionFields", {}),
                )
            except Exception as exc:
                self._log_scene_degrade("VIEW_COMMAND", cmd.view_id, exc)
                from ffast.visualization.scene import RenderScene
                scene = RenderScene(
                    view_id=view_state.view_id,
                    version=view_state.version,
                    camera=view_state.camera,
                )
                wanted = []
            if "atoms" in result.patch.changed:
                self._mark_provisional(scene, wanted)
            else:
                wanted = []
            fill_patch_from_scene(result.patch, scene)
            if only_forces_selected:
                result.patch.changed.add("only_forces")
                #print("server says yes!") # Debugging purposes

        # Several views share a connection (independent 3D panels, ADR 0056),
        # and a failed result carries no patch, so the result names its view.
        result_data = pack(control.COMMAND_RESULT, [], {**result.model_dump(), "view_id": cmd.view_id})
        await self._emit(result_data)

        if result.success and result.patch:
            patch_data = pack(control.SCENE_PATCH, [], result.patch.model_dump())
            await self._emit(patch_data)
            self._colour_later(view, wanted)

        logger.debug(
            "VIEW_COMMAND %s → success=%s new_version=%d",
            kwargs.get("type", "?"), result.success, result.new_version,
        )

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
            if not saving:
                self._offer_session_metrics(loop, path)

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
        ``AtomFilteredDataset`` and a plot-derived ``SubDataset``. The format
        names are ASE's (``extxyz``, ``xyz``, ``db``, ``traj``, ``vasp``,
        ``dftb``), plus ``sgdml``.
        """
        import os
        from ffast.protocol.rpc import pack

        async def _reply(ok: bool, resolved: str, error: str | None = None, n: int = 0,
                         skipped: int = 0):
            data = pack(
                control.SUBSET_EXPORTED, (),
                {"ok": ok, "path": resolved, "error": error, "n": n, "skipped": skipped},
            )
            await self._emit(data)

        resolved = os.path.abspath(os.path.expanduser(path))
        dataset = self.env.datasets.get(fingerprint)
        if dataset is None:
            logger.warning("EXPORT_SUBSET: dataset %r not found", fingerprint)
            await _reply(False, resolved, error="dataset not found")
            return

        from ffast.readers import ReaderError, registry
        from ffast.readers.writing import write_dataset

        fmt = kwargs.get("format")
        if fmt is None:
            ext = os.path.splitext(resolved)[1].lower()
            # .xyz too → extxyz so energies/forces survive (plain xyz drops them).
            fmt = "extxyz" if ext in (".xyz", ".extxyz", "") else registry.format_for_ext(ext)
        writer = registry.writer_for(fmt) if fmt else None
        if writer is None:
            if fmt is not None:
                await _reply(False, resolved, error=f"this server cannot write {fmt} files")
                return
            writer = registry.writer_for("extxyz")      # ASE names the format by the ending
        info = next((f for f in registry.writable_formats() if f["format"] == fmt),
                    {"label": fmt, "can_write": {}})

        try:
            items = self._export_items(dataset, info, kwargs.get("reference"),
                                       kwargs.get("predictions") or [])
        except ReaderError as exc:
            await _reply(False, resolved, error=str(exc))
            return

        loop = asyncio.get_running_loop()
        last = [time.monotonic()]

        def progress(done, total):
            """A long write tells the window how far it is, at most every half
            second; the end is the reply itself."""
            now = time.monotonic()
            if done >= total or now - last[0] < 0.5:
                return
            last[0] = now
            from ffast.protocol.rpc import pack as _pack
            data = _pack(control.SUBSET_EXPORTED, (), {
                "ok": None, "path": resolved, "error": None, "progress": [done, total]})
            asyncio.run_coroutine_threadsafe(self._emit(data), loop)

        try:
            # Frames with no atoms (an element subset's) are left out and counted.
            skipped = int(await asyncio.to_thread(
                write_dataset, writer, resolved, dataset, items, format=fmt, progress=progress) or 0)
            n = int(dataset.getN()) - skipped
            logger.info("EXPORT_SUBSET: wrote %d structure(s) → %s", n, resolved)
            await _reply(True, resolved, n=n, skipped=skipped)
        except Exception as exc:
            logger.warning("EXPORT_SUBSET: write failed for %r: %s", resolved, exc)
            await _reply(False, resolved, error=str(exc))

    def _export_items(self, dataset, info, reference, predictions):
        """What an export writes beside the geometry: the reference values
        and the predictions chosen, as ``ExportItem``s. The reference follows
        the format unless the page said (None: when the format can hold values);
        a prediction with no values for this dataset is refused by name."""
        import re
        from ffast.readers import ReaderError
        from ffast.readers.writing import ExportItem

        can = info.get("can_write") or {}
        items = []
        if reference is None:
            reference = can.get("energies") != 0
        if reference:
            has_forces = bool(dataset.hasForces())
            try:
                has_energies = dataset.getN() > 0 and dataset.getEnergies(0) is not None
            except RuntimeError:
                has_energies = False
            if has_energies or has_forces:
                items.append(ExportItem(
                    "energy", "forces",
                    (lambda i: dataset.getEnergies(i)) if has_energies else None,
                    (lambda i: dataset.getForces(i)) if has_forces else None))
        used = {"reference"}
        for fp in predictions:
            model = self.env.models.get(fp)
            name = getattr(model, "name", None) or fp[:8]
            stem = re.sub(r"[^\w-]+", "_", name).strip("_") or fp[:8]
            while stem in used:
                stem += "_2"
            used.add(stem)
            energy = self.env.data.getData("energy", model=fp, dataset=dataset)
            forces = self.env.data.getData("forces", model=fp, dataset=dataset)
            e = None if energy is None else energy.get("energy")
            f = None if forces is None else forces.get("forces")
            if e is None and f is None:
                raise ReaderError(f"{name} has no values for this dataset")
            items.append(ExportItem(
                f"{stem}_energy", f"{stem}_forces",
                None if e is None else (lambda i, e=np.asarray(e).reshape(-1): e[i]),
                None if f is None else (lambda i, f=f: _frame_of(f, i))))
        return items

    async def _on_request_metric(self, metric_id, request_id, **kwargs) -> None:
        """Ask the result store for a metric and reply with METRIC_RESULT
        (Stage 4a, ADR 0085). A reply with no result says why, and of what kind."""
        from ffast.metrics.models import MetricResult
        from ffast.metrics.result_store import Missing
        from ffast.protocol.rpc import pack_metric_result

        params = kwargs.get("params") or {}
        model_fp = kwargs.get("model_fp")
        dataset_fp = kwargs.get("dataset_fp")

        if self._cancelled_early.pop(request_id, None):
            return              # the page let go of it before it started
        self._metric_requests[request_id] = asyncio.current_task()
        try:
            outcome = await self.env.results.get(metric_id, params, dataset_fp, model_fp)
        except asyncio.CancelledError:
            logger.info("REQUEST_METRIC %s (%s) cancelled", metric_id, request_id)
            raise
        except Exception as exc:
            logger.error("REQUEST_METRIC compute failed %s: %s", metric_id, exc)
            outcome = Missing("failed", str(exc))
        finally:
            if self._metric_requests.get(request_id) is asyncio.current_task():
                del self._metric_requests[request_id]
        result = outcome if isinstance(outcome, MetricResult) else None
        error = kind = None
        if result is None and not (outcome.kind == "needs_prediction" and not model_fp):
            # With no prediction chosen the reply stays quiet: the page says
            # "Select a prediction" (ADR 0085 decision 5).
            error, kind = outcome.text, outcome.kind
        try:
            data = pack_metric_result(request_id, metric_id, result is not None, result, error, kind)
        except Exception as exc:
            # The page waits for every reply (no timeout), so one that cannot
            # be built is answered as a failure ("bytes object is too large"
            # on 1.77M structures, 2026-11-02).
            logger.warning("METRIC_RESULT for %r could not be packed: %s", metric_id, exc)
            result = None
            data = pack_metric_result(
                request_id, metric_id, False, None,
                f"The result could not be sent to the page: {exc}", "failed")
        await self._emit(data)
        logger.info(
            "METRIC_RESULT queued: metric=%r ok=%s request_id=%r",
            metric_id, result is not None, request_id,
        )

    async def _on_cancel_metric(self, request_id, **kwargs) -> None:
        """The page no longer wants a metric it asked for (its panel was
        redrawn or closed). Its request stops waiting and sends nothing; the
        result store stops the computation when nobody else waits for it
        (ADR 0085 decision 8)."""
        task = self._metric_requests.get(request_id)
        if task is not None:
            task.cancel()
            return
        # Its request may not have started yet (metric requests run beside the
        # queue): remember the cancel, a few hundred at most.
        self._cancelled_early[request_id] = True
        while len(self._cancelled_early) > 256:
            del self._cancelled_early[next(iter(self._cancelled_early))]

    async def _on_request_metric_catalog(self, **kwargs) -> None:
        try:
            await self._emit(self.feed.metric_catalog())
        except Exception as exc:
            logger.warning("METRIC_CATALOG error: %s", exc)

    async def _on_request_tab_layout(self, **kwargs) -> None:
        """Reply with TAB_LAYOUT: the merged tab layout (ADR 0045 Phase 3,
        ADR 0056). Mirrors ``_on_request_metric_catalog`` — a dedicated
        announcement, not a correlated reply. See ``WindowFeed.tab_layout``."""
        try:
            data = self.feed.tab_layout()
            await self._emit(data)
            logger.info("TAB_LAYOUT queued")
        except Exception as exc:
            logger.warning("TAB_LAYOUT error: %s", exc)

    # ── user tabs (ADR 0056) ────────────────────────────────────────────────

    async def _on_save_tab(self, tab, previous_name=None, **kwargs) -> None:
        await self._change_tabs("save", tab.get("name"),
                                lambda sources: sources.save(tab, previous_name))

    async def _on_delete_tab(self, name, **kwargs) -> None:
        await self._change_tabs("delete", name, lambda sources: sources.delete(name))

    async def _on_hide_tab(self, name, hidden, **kwargs) -> None:
        await self._change_tabs("hide" if hidden else "show", name,
                                lambda sources: sources.set_hidden(name, bool(hidden)))

    async def _on_set_tab_order(self, names, **kwargs) -> None:
        await self._change_tabs("order", None, lambda sources: sources.set_order(list(names)))

    async def _change_tabs(self, action: str, name, change) -> None:
        """Apply a tab change, answer this window with TAB_SAVED, then send the
        new layout to every window — in that order, so the window that saved
        hears its own outcome before the layout it caused."""
        from ffast.config.user_tabs import TabError
        from ffast.protocol.rpc import pack
        sources = getattr(self.env, "tab_sources", None)
        error = None
        try:
            if sources is None:
                raise TabError("This server keeps no user tabs")
            saved = await asyncio.to_thread(change, sources)
            if isinstance(saved, str):
                name = saved
        except TabError as exc:
            error = str(exc)
        except Exception as exc:
            logger.warning("Tab %s of %r failed: %s", action, name, exc)
            error = f"Could not {action} the tab: {exc}"
        await self._emit(pack(control.TAB_SAVED, [], {
            "ok": error is None, "action": action, "name": name, "error": error}))
        if error is None:
            logger.info("Tab %s: %r; sending the layout to every window", action, name)
            self.feed.changed(control.METRIC_CATALOG, control.TAB_LAYOUT, control.METRIC_USERS)

    async def _on_export_tab(self, name, **kwargs) -> None:
        from ffast.config.user_tabs import TabError
        from ffast.protocol.rpc import pack
        sources = getattr(self.env, "tab_sources", None)
        toml = error = None
        try:
            if sources is None:
                raise TabError("This server keeps no user tabs")
            toml = sources.export(name)
        except TabError as exc:
            error = str(exc)
        await self._emit(pack(control.TAB_EXPORTED, [], {"name": name, "toml": toml, "error": error}))

    # ── user metrics (ADR 0057) ─────────────────────────────────────────────

    async def _on_request_user_metrics(self, **kwargs) -> None:
        await self._emit(self.feed.user_metrics())

    async def _on_save_user_metric(self, metric, previous_id=None, base_revision=None,
                                   overwrite=False, **kwargs) -> None:
        await self._change_metrics(
            "save", metric.get("id"),
            lambda um: um.save(metric, previous_id, base_revision, bool(overwrite)))

    async def _on_delete_user_metric(self, id, **kwargs) -> None:
        await self._change_metrics("delete", id, lambda um: um.delete(id))

    async def _change_metrics(self, action: str, id, change) -> None:
        """Apply a metric change, answer this window with USER_METRIC_SAVED,
        then tell every window (METRICS_UPDATED, then the new list) — in that
        order, so the window that saved hears its own outcome first.

        Before anyone is told, results cached under the changed metrics are
        dropped (rule 5). Correctness does not depend on it: the formula is in
        the cache key and a run that overlapped the change is discarded
        (ADR 0061), so dropping only frees memory."""
        from ffast.config.user_metrics import MetricConflict, MetricError
        from ffast.protocol.rpc import pack
        um = getattr(self.env, "user_metrics", None)
        error, conflict, change_result = None, False, None
        try:
            if um is None:
                raise MetricError("This server keeps no user metrics")
            # One thread call, so no other task runs between the change and the
            # drop of old results (ADR 0061).
            change_result = await asyncio.to_thread(self._change_and_invalidate, um, change)
            if change_result.id:
                id = change_result.id
        except MetricConflict as exc:
            error, conflict = str(exc), True
        except MetricError as exc:
            error = str(exc)
        except Exception as exc:
            logger.warning("User metric %s of %r failed: %s", action, id, exc)
            error = f"Could not {action} the metric: {exc}"
        await self._emit(pack(control.USER_METRIC_SAVED, [], {
            "ok": error is None, "action": action, "id": id,
            "error": error, "conflict": conflict}))
        if error is None:
            logger.info("User metric %s: %r; telling every window", action, id)
            self._broadcast(pack(control.METRICS_UPDATED, [], {}))
            self.feed.changed(control.METRIC_CATALOG, control.TAB_LAYOUT,
                              control.USER_METRICS, control.METRIC_USERS)
            await self.feed.resend_inputs()

    def _change_and_invalidate(self, um, change):
        """Run the metric change, then drop the results of what it affected."""
        result = change(um)
        self._apply_metric_change(result.affected)
        return result

    def _apply_metric_change(self, affected) -> None:
        """Drop cached results of the changed metrics."""
        data = getattr(self.env, "data", None)
        if data is None:
            return
        results = getattr(self.env, "results", None)
        if results is not None:
            results.forget_metrics(affected)
        dropped = data.invalidate_metrics(affected)
        logger.info("Metrics %s changed: %d cached result(s) dropped", sorted(affected), len(dropped))

    async def _on_export_user_metric(self, id, **kwargs) -> None:
        from ffast.config.user_metrics import MetricError
        from ffast.protocol.rpc import pack
        um = getattr(self.env, "user_metrics", None)
        toml = error = None
        try:
            if um is None:
                raise MetricError("This server keeps no user metrics")
            toml = um.export(id)
        except MetricError as exc:
            error = str(exc)
        await self._emit(pack(control.USER_METRIC_EXPORTED, [], {"id": id, "toml": toml, "error": error}))

    async def _on_request_metric_inputs(self, dataset_fp=None, model_fp=None, **kwargs) -> None:
        """Reply with METRIC_INPUTS, and remember the pair: it is the dataset
        and prediction this window is looking at, so after a metric change the
        window is sent a fresh list for it (ADR 0062)."""
        self._inputs_for = (dataset_fp, model_fp)
        await self.send_inputs()

    async def send_inputs(self, drop_if_full: bool = False) -> None:
        """METRIC_INPUTS for the pair this window last asked about: what a
        formula can use for that dataset and prediction, extra file keys
        included (rule 10). Does nothing for a window that never asked."""
        if self._inputs_for is None:
            return
        dataset_fp, model_fp = self._inputs_for
        from ffast.metrics.input_catalog import build_input_catalog
        from ffast.metrics.registry import default_registry
        from ffast.protocol.rpc import pack
        dataset = self.env.datasets.get(dataset_fp) if dataset_fp else None
        model = self.env.models.get(model_fp) if model_fp else None
        data = getattr(self.env, "data", None)
        inputs = await asyncio.to_thread(
            build_input_catalog, default_registry, dataset, model,
            getattr(data, "predictionFields", None),
            getattr(self.env, "user_metrics", None))
        message = pack(control.METRIC_INPUTS, [], {
            "dataset_fp": dataset_fp, "model_fp": model_fp, "inputs": inputs})
        if drop_if_full:
            self._emit_or_drop(message, "METRIC_INPUTS")
        else:
            await self._emit(message)

    # ── check and try a formula (ADR 0057 rules 11, 12) ─────────────────────

    def _uniform(self, dataset_fp) -> bool | None:
        """Whether every structure of a dataset has the same atoms in the same
        order (ADR 0058), as by=atom needs; None when no dataset is named."""
        dataset = self.env.datasets.get(dataset_fp) if dataset_fp else None
        return None if dataset is None else not getattr(dataset, "isVariable", False)

    async def _on_check_metric(self, metric, check_id=None, dataset_fp=None, **kwargs) -> None:
        """Reply with METRIC_CHECKED: the formula's error or result shape, and
        warnings for the pane's dataset (ADR 0066). Touches no data and
        changes nothing, so it runs on every keystroke."""
        from ffast.config.user_metrics import MetricError
        from ffast.protocol.rpc import pack
        um = getattr(self.env, "user_metrics", None)
        if um is None:
            out = {"ok": False, "error": "This server keeps no user metrics", "shape": None,
                   "warnings": []}
        else:
            out = um.check(metric, uniform=self._uniform(dataset_fp))
        await self._emit(pack(control.METRIC_CHECKED, [], {"check_id": check_id, **out}))

    async def _on_request_metric_boxes(self, request_id, metric_id=None, base=None,
                                       setting=None, dataset_fp=None, **kwargs) -> None:
        """Reply with METRIC_BOXES: the keep boxes of a metric, or the formula,
        id and name a box change gives (ADR 0065). Touches no data; the
        dataset is asked only whether its structures have the same atoms."""
        from ffast.metrics.boxes import box_state
        from ffast.metrics.registry import default_registry
        from ffast.protocol.rpc import pack
        uniform = self._uniform(dataset_fp)
        um = getattr(self.env, "user_metrics", None)
        revision = default_registry.revision
        try:
            out = box_state(default_registry, metric_id=metric_id, base=base,
                            setting=setting, uniform=uniform,
                            saved=lambda i: um is not None and um.origin(i) == "user")
        except (KeyError, ValueError) as exc:
            out = {"error": str(exc).strip("'\"")}
        await self._emit(pack(control.METRIC_BOXES, [], {"request_id": request_id, **out}))
        if default_registry.revision != revision:
            # Opening a box id registered it: other panels may now name it.
            self.feed.changed(control.METRIC_CATALOG)

    async def _on_try_metric(self, metric, try_id, dataset_fp=None, model_fp=None, **kwargs) -> None:
        """Start a Try and return at once: it runs as its own task so a
        CANCEL_TRY_METRIC from the same window can be heard while it runs."""
        import threading
        stop = threading.Event()
        cancelled = asyncio.Event()
        task = asyncio.create_task(self._run_try(metric, try_id, dataset_fp, model_fp, stop, cancelled))
        self._tries[try_id] = (stop, task, cancelled)
        task.add_done_callback(lambda _t: self._tries.pop(try_id, None))

    async def _on_cancel_try_metric(self, try_id, **kwargs) -> None:
        running = self._tries.get(try_id)
        if running is not None:
            running[0].set()
            running[2].set()

    def close(self) -> None:
        """The window has gone: stop its Tries, colourings and length probe,
        drop its views, and leave the feed, so its colourings stop counting as
        users of a metric (ADR 0062). Nobody reads its queue any more, so
        work left running could wait on a full queue for ever."""
        self.cancel_tries()
        self._stop_length_probe()
        for task in list(self._colouring):
            task.cancel()
        for task in list(self._job_watches.values()):
            task.cancel()
        coloured = any((self._colour_source(v) or "").startswith("metric:")
                       for v in self.views.values())
        self.views.clear()
        self.feed.detach(self)
        if coloured:
            self.feed.changed(control.METRIC_USERS)

    def cancel_tries(self) -> None:
        """Stop every Try of this connection (it has gone away): no reply, and
        the result store is told nobody here waits any more (ADR 0085)."""
        for stop, task, _cancelled in list(self._tries.values()):
            stop.set()
            task.cancel()

    async def _run_try(self, metric, try_id, dataset_fp, model_fp, stop, cancelled) -> None:
        from ffast.metrics.try_formula import TryError, TryResult, try_formula
        from ffast.protocol.rpc import pack
        loop = asyncio.get_running_loop()

        def progress(done, total):
            data = pack("TASK_PROGRESS", [try_id], {
                "message": f"Trying the formula: {done} of {total} frames",
                "progress": done / total if total else 1.0, "error": False})
            loop.call_soon_threadsafe(self._emit_or_drop, data, "TASK_PROGRESS")

        heard = ready = work = None
        try:
            heard = asyncio.ensure_future(cancelled.wait())
            # The saved metrics the formula reads come from the result store
            # first (ADR 0085); Cancel while they compute lets the store stop a
            # computation nobody else waits for.
            ready = asyncio.ensure_future(self._try_inputs(metric, dataset_fp, model_fp))
            await asyncio.wait({ready, heard}, return_when=asyncio.FIRST_COMPLETED)
            if not ready.done():
                ready.cancel()
                heard.cancel()
                out = {"ok": True, "error": None, **TryResult(cancelled=True).to_dict()}
            elif ready.result():
                heard.cancel()
                out = {"ok": False, "error": ready.result()}
            else:
                work = asyncio.ensure_future(asyncio.to_thread(
                    self._try_blocking, metric, dataset_fp, model_fp, progress, stop.is_set))
                await asyncio.wait({work, heard}, return_when=asyncio.FIRST_COMPLETED)
                heard.cancel()
                if not work.done():
                    # Answer Cancel at once. The thread may still be computing an
                    # input (a metric on the worker); it finishes in the background,
                    # the result stays in the cache, and it then sees the stop and exits.
                    work.add_done_callback(lambda f: f.cancelled() or f.exception())
                    out = {"ok": True, "error": None, **TryResult(cancelled=True).to_dict()}
                else:
                    out = {"ok": True, "error": None, **work.result()}
        except asyncio.CancelledError:
            # The window went away: stop waiting for the store, so a metric
            # nobody else waits for is stopped (decision 8).
            for waiting in (ready, heard):
                if waiting is not None:
                    waiting.cancel()
            if work is not None:   # the thread sees `stop` and ends on its own
                work.add_done_callback(lambda f: f.cancelled() or f.exception())
            raise
        except TryError as exc:
            out = {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.warning("TRY_METRIC %s failed: %s", try_id, exc)
            out = {"ok": False, "error": f"Could not try the formula: {exc}"}
        out.setdefault("cancelled", False)
        await self._emit(pack(control.METRIC_TRIED, [], {"try_id": try_id, **out}))

    async def _try_inputs(self, metric, dataset_fp, model_fp) -> str | None:
        """Get the saved metrics a draft formula reads, through the result
        store. A sentence saying why when one is missing, else None. A formula
        that does not validate, or no dataset, is left to ``_try_blocking`` to
        report as it always has."""
        from pydantic import ValidationError

        from ffast.config.models import ExprMetricConfig
        from ffast.metrics.expr import analyze_expr
        from ffast.metrics.models import MetricResult
        from ffast.metrics.registry import default_registry

        try:
            cfg = ExprMetricConfig.model_validate(metric)
        except ValidationError:
            return None
        if not dataset_fp or self.env.datasets.get(dataset_fp) is None:
            return None
        um = getattr(self.env, "user_metrics", None)
        if um is None:
            return None
        try:        # a formula that does not parse is reported by the Try, before any metric runs
            analyze_expr(cfg.expr, dict(cfg.vars), registry=um.registry_without(cfg.id))
        except ValueError:
            return None
        for ref in cfg.vars.values():
            if not default_registry.has(ref):
                continue
            outcome = await self.env.results.get(ref, {}, dataset_fp, model_fp)
            if not isinstance(outcome, MetricResult):
                label = default_registry.get(ref)[0].label or ref
                return f"{label}: {outcome.text}"
        return None

    def _try_blocking(self, metric, dataset_fp, model_fp, progress, cancelled) -> dict:
        """The Try itself, on a thread: resolve the inputs for the selected
        dataset and prediction, then evaluate in chunks on the metric worker."""
        from pydantic import ValidationError

        from ffast.config.models import ExprMetricConfig
        from ffast.metrics.registry import default_registry
        from ffast.metrics.try_formula import TryError, try_formula

        um = getattr(self.env, "user_metrics", None)
        if um is None:
            raise TryError("This server keeps no user metrics")
        try:
            cfg = ExprMetricConfig.model_validate(metric)
        except ValidationError as exc:
            raise TryError(str(exc.errors()[0].get("msg", exc)).removeprefix("Value error, ")) from None
        dataset = self.env.datasets.get(dataset_fp) if dataset_fp else None
        model = self.env.models.get(model_fp) if model_fp else None
        if dataset is None:
            raise TryError("Select a dataset to try the formula on")
        data = self.env.data
        resolver = data.inputResolver

        def resolve(ref):
            if default_registry.has(ref):
                # Got before this thread started; only the cache dropping it
                # meanwhile can leave it missing.
                result = self.env.results.peek(ref, {}, dataset_fp, model_fp)
                if result is not None:
                    return result.values
                raise TryError(f"{ref} could not be computed here")
            return resolver.resolve(ref, model=model, dataset=dataset)

        n_atoms = resolver.resolve("n_atoms", dataset=dataset)
        if n_atoms is None:
            raise TryError("The dataset's atom counts are not available")
        # The formula runs where saved metrics run (ADR 0079).
        executor = data.metricExecutor
        result = try_formula(
            cfg.expr, dict(cfg.vars), um.registry_without(cfg.id), resolve, n_atoms,
            on_progress=progress, cancelled=cancelled,
            compute=lambda fn, kwargs: executor.call(fn, kwargs, label=cfg.id))
        return result.to_dict()

    # ── user metrics a session carries (ADR 0057 rules 15, 16) ──────────────

    def _offer_session_metrics(self, loop, path) -> None:
        """After a session load: if it carries user metrics this server lacks
        (or has under another formula), ask the window that loaded whether to add
        them. Runs on the task's worker thread. Declining is just not answering:
        the session then behaves as if it had carried nothing."""
        import os
        import uuid

        from ffast.config.user_metrics import SESSION_FILE, read_formulas
        from ffast.protocol.rpc import pack

        um = getattr(self.env, "user_metrics", None)
        if um is None:
            return
        try:
            plan = um.plan_session(read_formulas(os.path.join(path, SESSION_FILE)))
        except Exception as exc:
            logger.warning("Session metrics of %r unreadable: %s", path, exc)
            return
        if not plan:
            return
        offer_id = f"offer-{uuid.uuid4().hex[:12]}"
        self._session_offers = {offer_id: plan}      # only the latest offer stays open
        data = pack(control.SESSION_METRICS, [], {"offer_id": offer_id, "metrics": plan.describe()})
        loop.call_soon_threadsafe(self._emit_or_drop, data, "SESSION_METRICS")

    async def _on_add_session_metrics(self, offer_id, **kwargs) -> None:
        plan = self._session_offers.pop(offer_id, None)
        if plan is None:
            from ffast.protocol.rpc import pack
            await self._emit(pack(control.USER_METRIC_SAVED, [], {
                "ok": False, "action": "add-session", "id": None,
                "error": "That offer is no longer open; load the session again", "conflict": False}))
            return
        await self._change_metrics("add-session", None, lambda um: um.add_session(plan))


class _LoadAnswer:
    """One load's final reply, ``LOAD_RESULT``, to the window that asked.

    ``kind`` is 'dataset', 'prediction', 'model', 'zero' or 'example'. What
    the server holds for the file is noted when the request arrives and again
    when the load ends: something new is the load's result; the same thing
    means it was already loaded; nothing at all means it failed without
    saying why (it logged it).
    """

    def __init__(self, session, kind, path, dataset_fp):
        self._session = session
        self._kind = kind
        self._path = path
        self._dataset_fp = dataset_fp
        self._request_id = replies.current()
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._sent = False
        self._before = set() if kind == "example" else self._held()

    def _held(self) -> set:
        """What the server holds for this request: fingerprints, and for a
        prediction whether it has numbers for the asked dataset."""
        env = self._session.env
        if self._kind == "zero":
            from ffast.loaders.zero import ZeroModelLoader
            fp = ZeroModelLoader.fingerprint
            return {fp} if env.models.exists(fp) else set()
        if self._kind in ("dataset", "example"):
            return {d.fingerprint for d in env.datasets.all(excludeSubs=True)
                    if getattr(d, "path", None) == self._path}
        held = {m.fingerprint for m in env.models.all()
                if getattr(m, "path", None) == self._path}
        if self._kind == "prediction":
            feed = self._session.feed
            return {fp for fp in held if self._dataset_fp in feed.datasets_of(fp)}
        return held

    def done(self) -> None:
        if self._kind == "example":
            return   # the example task sends its own pair
        after = self._held()
        new = after - self._before
        if new:
            self.send(kind=self._kind, path=self._path, fingerprint=sorted(new)[0],
                      already=False)
        elif after:
            self.send(kind=self._kind, path=self._path, fingerprint=sorted(after)[0],
                      already=True)
        else:
            self.failed("it did not load (see the server log)")

    def failed(self, exc) -> None:
        logger.warning("Loading %s %r failed: %s", self._kind, self._path, exc,
                       extra={"popup": False})
        self.send(kind=self._kind, path=self._path, error=str(exc))

    def send(self, error=None, **kwargs) -> None:
        with self._lock:
            if self._sent:
                return
            self._sent = True
        data = replies.reply(control.LOAD_RESULT, (), kwargs, error=error,
                             request_id=self._request_id)
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if not on_loop:   # the load task's thread
            asyncio.run_coroutine_threadsafe(self._session._emit(data), self._loop)
            return
        try:
            self._session.outbound.put_nowait(data)
        except asyncio.QueueFull:
            self._loop.create_task(self._session._emit(data))
