"""Canonical event-name constants for the RPC Channel's Control and Array
messages (ADR 0033).

Complements ``ffast.protocol.notifications`` (which catalogues the *Broadcast
Events* — ``SERVER_TO_CLIENT`` in ``ffast.protocol.rpc``). This module covers
everything else that crosses the wire: client→server Control messages, their
server→client replies, the handshake, and the untyped Array messages — so a
send site never has to spell an event name as a bare string literal, and one
module answers "what can cross this wire?"

``CLIENT_TO_SERVER`` is drift-tested (see ``tests/ffast/test_control_events.py``):
it must equal ``ServerSession._handlers.keys()``.
"""

# ── client → server Control messages (ServerSession._handlers) ──────────────
LOAD_DATASET = "LOAD_DATASET"
LOAD_MODEL = "LOAD_MODEL"
DELETE_OBJECT = "DELETE_OBJECT"
CREATE_SUBSET = "CREATE_SUBSET"
DECLARE_SUBSET = "DECLARE_SUBSET"
# Freeze a live subset's frames as a frozen subset.
FREEZE_SUBSET = "FREEZE_SUBSET"
# Rename a dataset or prediction; it is announced again to every window.
RENAME_OBJECT = "RENAME_OBJECT"
# Cancel a running task, as the Tasks list's Cancel does.
CANCEL_TASK = "CANCEL_TASK"
# Name the unit of a dataset's energies or forces, which no file declares. The
# dataset is announced again (REMOTE_DATASET_META) to every window.
SET_DATASET_UNIT = "SET_DATASET_UNIT"
PROBE_DATASET_KEYS = "PROBE_DATASET_KEYS"
PROBE_DATASET_LENGTH = "PROBE_DATASET_LENGTH"
CANCEL_PROBE_DATASET_LENGTH = "CANCEL_PROBE_DATASET_LENGTH"
# A sample by energy (ADR 0096): the file's energies as a histogram, and what a
# load with given settings would keep. Answered with DATASET_ENERGIES and
# ENERGY_SAMPLE_PREVIEW, to the asking window.
PROBE_DATASET_ENERGIES = "PROBE_DATASET_ENERGIES"
PREVIEW_ENERGY_SAMPLE = "PREVIEW_ENERGY_SAMPLE"
LIST_DIR = "LIST_DIR"
LOAD_PREDICTION = "LOAD_PREDICTION"
# Load the SPICE pair (examples/data/variable-sized molecular: dataset.xyz, then prediction.xyz
# against it). The server knows where the examples live; the window does not.
LOAD_EXAMPLE = "LOAD_EXAMPLE"
# Load the zero baseline model (all energies and forces zero). Only ever sent
# by an explicit user action (File ▸ Load Zero Model), never automatically.
LOAD_ZERO_MODEL = "LOAD_ZERO_MODEL"
OPEN_VIEW = "OPEN_VIEW"
CLOSE_VIEW = "CLOSE_VIEW"
VIEW_COMMAND = "VIEW_COMMAND"
SAVE_SESSION = "SAVE_SESSION"
LOAD_SESSION = "LOAD_SESSION"
REQUEST_METRIC = "REQUEST_METRIC"
REQUEST_METRIC_CATALOG = "REQUEST_METRIC_CATALOG"
REQUEST_TAB_LAYOUT = "REQUEST_TAB_LAYOUT"
EXPORT_SUBSET = "EXPORT_SUBSET"
# User tabs (ADR 0056): save one built in the browser, delete one (for a tab
# that replaces another, that is "Reset to original"), hide or show any tab,
# and export any tab as TOML.
SAVE_TAB = "SAVE_TAB"
DELETE_TAB = "DELETE_TAB"
HIDE_TAB = "HIDE_TAB"
# The whole tab bar order, hidden tabs included; [] brings back the default
# order (ADR 0070).
SET_TAB_ORDER = "SET_TAB_ORDER"
EXPORT_TAB = "EXPORT_TAB"
# User metrics (ADR 0057): list them, save one (a new one, or an edit that keeps
# the id), delete one, export one as TOML.
REQUEST_USER_METRICS = "REQUEST_USER_METRICS"
SAVE_USER_METRIC = "SAVE_USER_METRIC"
DELETE_USER_METRIC = "DELETE_USER_METRIC"
EXPORT_USER_METRIC = "EXPORT_USER_METRIC"
# What a formula can use for a dataset and prediction (rule 10).
REQUEST_METRIC_INPUTS = "REQUEST_METRIC_INPUTS"
# Check a formula as it is typed (touches no data), try it on a whole dataset
# without saving, and stop a try (rules 11, 12).
CHECK_METRIC = "CHECK_METRIC"
TRY_METRIC = "TRY_METRIC"
CANCEL_TRY_METRIC = "CANCEL_TRY_METRIC"
# The page no longer wants a metric it asked for: {request_id} (ADR 0085).
CANCEL_METRIC = "CANCEL_METRIC"
# The keep boxes of a metric, or of a box change (ADR 0065): touches no data.
REQUEST_METRIC_BOXES = "REQUEST_METRIC_BOXES"
# Answer "Add them?" after a session load offered the user metrics it carries
# that this server lacks (rule 15).
ADD_SESSION_METRICS = "ADD_SESSION_METRICS"
# One line for the server's trace file (ffast-server --trace): what the user
# clicked or changed, or an error in the page. Ignored while the trace is off.
TRACE = "TRACE"
# Follow a live LAMMPS run folder on the server's disk (ADR 0087).
OPEN_LIVE_RUN = "OPEN_LIVE_RUN"
# "Stop now" for an open live run, and its stop-on-alarm switch (ADR 0086).
PREPARE_PRACTICE_INPUT = "PREPARE_PRACTICE_INPUT"
START_PRACTICE_RUN = "START_PRACTICE_RUN"
HEAT_PRACTICE_RUN = "HEAT_PRACTICE_RUN"
DELETE_PRACTICE_RUN = "DELETE_PRACTICE_RUN"
LIVE_STOP = "LIVE_STOP"
LIVE_SET_STOP_ON_ALARM = "LIVE_SET_STOP_ON_ALARM"
# What drove one live frame's score, computed when asked (ADR 0087).
LIVE_ATTRIBUTION = "LIVE_ATTRIBUTION"
# The "Live runs" list, and forgetting a run whose folder is gone (ADR 0087).
LIST_LIVE_RUNS = "LIST_LIVE_RUNS"
FORGET_LIVE_RUN = "FORGET_LIVE_RUN"
# Score a loaded dataset against a reference (ADR 0093): what the dialog needs, the
# scoring itself (starts a task; the scored item appears at once), and continuing an
# incomplete one.
SCORE_DIALOG_INFO = "SCORE_DIALOG_INFO"
# What the delete dialog of a dataset or prediction asks: the metric results
# only it used, in memory and on disk (answered with STORED_RESULTS_INFO).
STORED_RESULTS = "STORED_RESULTS"
SCORE_DATASET = "SCORE_DATASET"
CONTINUE_SCORING = "CONTINUE_SCORING"
# Fit a reference bundle and its LAMMPS include from two loaded datasets (ADR 0086).
MAKE_REFERENCE = "MAKE_REFERENCE"
# "Run with FFAST" (ADR 0086): check the picked input, job script and reference
# and show the copies as diffs (writes nothing); then write the approved copies.
PREPARE_RUN_WITH_FFAST = "PREPARE_RUN_WITH_FFAST"
WRITE_RUN_WITH_FFAST = "WRITE_RUN_WITH_FFAST"
# Submit a written job-script copy from the server's own node (the other path is
# the web launcher's /cluster/submit, over SSH from the laptop).
SUBMIT_JOB = "SUBMIT_JOB"
# Follow a submitted job until its run's first frame (then the page opens it).
WATCH_JOB = "WATCH_JOB"
# What Run with FFAST would use from a run folder: job scripts, the input each
# runs, references (reads only; an empty folder means the last one used).
SCAN_RUN_FOLDER = "SCAN_RUN_FOLDER"

CLIENT_TO_SERVER = frozenset(
    {
        LOAD_DATASET, LOAD_MODEL, DELETE_OBJECT, CREATE_SUBSET, DECLARE_SUBSET,
        FREEZE_SUBSET, RENAME_OBJECT, CANCEL_TASK, SET_DATASET_UNIT,
        PROBE_DATASET_KEYS, PROBE_DATASET_LENGTH, CANCEL_PROBE_DATASET_LENGTH, LIST_DIR, LOAD_PREDICTION,
        PROBE_DATASET_ENERGIES, PREVIEW_ENERGY_SAMPLE,
        LOAD_EXAMPLE, LOAD_ZERO_MODEL, OPEN_VIEW, CLOSE_VIEW, VIEW_COMMAND,
        SAVE_SESSION, LOAD_SESSION, REQUEST_METRIC, CANCEL_METRIC,
        REQUEST_METRIC_CATALOG, REQUEST_TAB_LAYOUT, EXPORT_SUBSET,
        SAVE_TAB, DELETE_TAB, HIDE_TAB, SET_TAB_ORDER, EXPORT_TAB,
        REQUEST_USER_METRICS, SAVE_USER_METRIC, DELETE_USER_METRIC, EXPORT_USER_METRIC,
        REQUEST_METRIC_INPUTS, CHECK_METRIC, TRY_METRIC, CANCEL_TRY_METRIC,
        REQUEST_METRIC_BOXES, ADD_SESSION_METRICS, TRACE, OPEN_LIVE_RUN,
        PREPARE_PRACTICE_INPUT, START_PRACTICE_RUN, HEAT_PRACTICE_RUN, DELETE_PRACTICE_RUN, LIVE_STOP, LIVE_SET_STOP_ON_ALARM, LIVE_ATTRIBUTION, LIST_LIVE_RUNS, FORGET_LIVE_RUN,
        MAKE_REFERENCE, PREPARE_RUN_WITH_FFAST, WRITE_RUN_WITH_FFAST, SUBMIT_JOB, WATCH_JOB,
        SCAN_RUN_FOLDER, SCORE_DIALOG_INFO, SCORE_DATASET, CONTINUE_SCORING,
        STORED_RESULTS,
    }
)

# Control messages that mutate the shared Environment or drive an existing
# view (ADR 0044 Phase 2). A READ_ONLY connection's inbound control is
# dropped for these — everything else in CLIENT_TO_SERVER is a read/query
# (open its own view, list a directory, request a metric) a viewer may still
# issue so it can see scenes and results without being able to change them.
MUTATING_CLIENT_EVENTS = frozenset(
    {
        LOAD_DATASET, LOAD_MODEL, DELETE_OBJECT, CREATE_SUBSET, DECLARE_SUBSET,
        FREEZE_SUBSET, RENAME_OBJECT, CANCEL_TASK, SET_DATASET_UNIT, LOAD_PREDICTION, LOAD_EXAMPLE, LOAD_ZERO_MODEL, VIEW_COMMAND, SAVE_SESSION, LOAD_SESSION, EXPORT_SUBSET,
        SAVE_TAB, DELETE_TAB, HIDE_TAB, SET_TAB_ORDER, SAVE_USER_METRIC, DELETE_USER_METRIC,
        ADD_SESSION_METRICS, OPEN_LIVE_RUN, PREPARE_PRACTICE_INPUT, START_PRACTICE_RUN, HEAT_PRACTICE_RUN, DELETE_PRACTICE_RUN, LIVE_STOP, LIVE_SET_STOP_ON_ALARM, FORGET_LIVE_RUN,
        MAKE_REFERENCE, PREPARE_RUN_WITH_FFAST, WRITE_RUN_WITH_FFAST, SUBMIT_JOB,
        SCORE_DATASET, CONTINUE_SCORING,
    }
)

# ── handshake / connection lifecycle (outside ServerSession.dispatch — the
# server intercepts these in server.py before role-gated dispatch runs) ─────
HELLO = "HELLO"
HELLO_ACK = "HELLO_ACK"
GRACEFUL_DISCONNECT = "GRACEFUL_DISCONNECT"
# The page's timing log (ADR 0072): the page asks for the server's clock, the
# server answers and reports, per message, when it was read, handled and sent.
TIMING_PING = "TIMING_PING"
TIMING = "TIMING"
# The page's heartbeat (ADR 0097): every 15 s the page asks, the server answers
# at once from its receive loop. A page that hears nothing for 60 s treats the
# connection as lost, which a silently stalled socket would never report.
HEARTBEAT = "HEARTBEAT"

# ── server → client replies ─────────────────────────────────────────────────
DATASET_KEYS_RESPONSE = "DATASET_KEYS_RESPONSE"
DATASET_LENGTH_RESPONSE = "DATASET_LENGTH_RESPONSE"
METRIC_RESULT = "METRIC_RESULT"
DIR_LISTING = "DIR_LISTING"

REPLY_EVENTS = frozenset(
    {
        DATASET_KEYS_RESPONSE, DATASET_LENGTH_RESPONSE,
        METRIC_RESULT, DIR_LISTING,
    }
)

# ── server → client dedicated announcements + view-lifecycle push (not a
# reply, not a generic Broadcast Event — each has a single named producer) ──
REMOTE_DATASET_META = "REMOTE_DATASET_META"
REMOTE_MODEL_META = "REMOTE_MODEL_META"
SCENE_SNAPSHOT = "SCENE_SNAPSHOT"
SCENE_PATCH = "SCENE_PATCH"
COMMAND_RESULT = "COMMAND_RESULT"
# A live run's state and score series, to every window (ADR 0087); also the
# answer to OPEN_LIVE_RUN, with ``error`` set when the folder is not a run.
LIVE_RUN_UPDATE = "LIVE_RUN_UPDATE"
# The answer to LIVE_ATTRIBUTION, to the asking window only; ``error`` when it cannot be made.
LIVE_ATTRIBUTION_RESULT = "LIVE_ATTRIBUTION_RESULT"
# The answer to LIST_LIVE_RUNS and FORGET_LIVE_RUN: the rows, to the asking window.
LIVE_RUNS = "LIVE_RUNS"
PRACTICE_INPUT_PREPARED = "PRACTICE_INPUT_PREPARED"
PRACTICE_RUN_STARTED = "PRACTICE_RUN_STARTED"
PRACTICE_RUN_RESULT = "PRACTICE_RUN_RESULT"
# The answers to SCORE_DIALOG_INFO (what the dialog shows) and to SCORE_DATASET and
# CONTINUE_SCORING (the item, or ``error``), to the asking window only.
SCORE_DIALOG_INFO_RESULT = "SCORE_DIALOG_INFO_RESULT"
# The answer to STORED_RESULTS, to the asking window: fingerprint, count,
# memory_bytes, disk_bytes.
STORED_RESULTS_INFO = "STORED_RESULTS_INFO"
# The answers to PROBE_DATASET_ENERGIES and PREVIEW_ENERGY_SAMPLE (ADR 0096), to
# the asking window, args=(path,). One-shot announcements, out of REPLY_EVENTS.
DATASET_ENERGIES = "DATASET_ENERGIES"
ENERGY_SAMPLE_PREVIEW = "ENERGY_SAMPLE_PREVIEW"
SCORE_STARTED = "SCORE_STARTED"
# The answer to MAKE_REFERENCE, to the asking window: paths written, or ``error``.
REFERENCE_MADE = "REFERENCE_MADE"
# The answers to PREPARE_RUN_WITH_FFAST and WRITE_RUN_WITH_FFAST, to the asking
# window: checks, diffs and originals' sha256, or a refusal naming the file.
RUN_WITH_FFAST_PREPARED = "RUN_WITH_FFAST_PREPARED"
RUN_WITH_FFAST_WRITTEN = "RUN_WITH_FFAST_WRITTEN"
# The answer to SUBMIT_JOB, to the asking window: ``job_id``, or ``error``.
JOB_SUBMITTED = "JOB_SUBMITTED"
# Pushed to the window that asked WATCH_JOB on each change: queued, running,
# started, frame (open the run folder now) or ended.
JOB_STATE = "JOB_STATE"
# The answer to SCAN_RUN_FOLDER, to the asking window.
RUN_FOLDER_SCANNED = "RUN_FOLDER_SCANNED"
# Pushed to a window on connect and to every window after a change (ADR 0062);
# also the answer to REQUEST_METRIC_CATALOG, which only the asker gets.
METRIC_CATALOG = "METRIC_CATALOG"
METRICS_UPDATED = "METRICS_UPDATED"
# Pushed on connect and after a tab change (ADR 0062); also the answer to
# REQUEST_TAB_LAYOUT (ADR 0045 Phase 3). Like METRIC_CATALOG it is a
# dedicated announcement, not a correlated reply channel — only the web client
# asks for it, and it carries the whole layout in one shot, so it stays out of
# REPLY_EVENTS.
TAB_LAYOUT = "TAB_LAYOUT"
# Which dataset and model loaders this server has (its plugins):
# {datasets: [{name, extension}], models: [{name, extension}]}, names sorted
# case-blind. Pushed to a window on connect only: plugins load once, at
# start (web parity plan 2026-10-29). A dedicated announcement, out of
# REPLY_EVENTS.
LOADER_TYPES = "LOADER_TYPES"
# Reply to EXPORT_SUBSET (ADR 0045 Phase 4, issue 20). A dedicated one-shot
# announcement (like TAB_LAYOUT): the server writes the extxyz and reports the
# written path (or an error) so the browser can confirm the export. Only the
# web client asks for it, so it stays out of REPLY_EVENTS.
SUBSET_EXPORTED = "SUBSET_EXPORTED"
# Outcome of CREATE_SUBSET, to the window that asked: {ok, text, error}. text
# is what was made ("C in 812 of 1000 frames", ADR 0067); error says why
# nothing was. A one-shot announcement, out of REPLY_EVENTS.
SUBSET_EXTRACTED = "SUBSET_EXTRACTED"
# Outcome of SAVE_SESSION / LOAD_SESSION (ADR 0050). Same shape as
# SUBSET_EXPORTED — {ok, path, error} — and for the same reason: the session
# write happens in a task, and TASK_DONE carries only a task id that the
# requesting client never learns, so a client waiting on its own save could not
# distinguish its completion from an unrelated dataset load's. These name the
# operation and its path explicitly. One-shot announcements, so they stay out of
# REPLY_EVENTS and out of SERVER_TO_CLIENT (emitted by the handler, not the
# generic broadcast loop).
SESSION_SAVED = "SESSION_SAVED"
SESSION_LOADED = "SESSION_LOADED"
# The final reply to LOAD_DATASET / LOAD_PREDICTION / LOAD_MODEL /
# LOAD_ZERO_MODEL / LOAD_EXAMPLE (ADR 0097), to the window that asked:
# {kind, path, fingerprint, already}; LOAD_EXAMPLE: {dataset_fp, model_fp}.
# A failed load's carries `error`. The dataset and model announcements still go
# to every window, before or after it.
LOAD_RESULT = "LOAD_RESULT"
# Sent to every window right after TASK_CREATED: args=(task_id,),
# {name, visual, cancellable}. TASK_CREATED carries only the id; the Tasks
# list shows visual tasks by name; a computing metric is not cancellable. One-shot announcement, out of REPLY_EVENTS.
TASK_INFO = "TASK_INFO"
# Outcome of SAVE_TAB / DELETE_TAB / HIDE_TAB / SET_TAB_ORDER (ADR 0056): {ok, action, name,
# error}, to the window that asked. On success the new layout then goes to
# every window as TAB_LAYOUT through the hub. Same shape and reasoning as
# SESSION_SAVED: a one-shot announcement, out of REPLY_EVENTS.
TAB_SAVED = "TAB_SAVED"
# Reply to EXPORT_TAB: {name, toml, error}, the tab as a project would write it.
TAB_EXPORTED = "TAB_EXPORTED"
# User metrics (ADR 0057). USER_METRICS is the whole list, pushed on connect and,
# after any change, to every window (ADR 0062); REQUEST_USER_METRICS still
# answers the asker alone. USER_METRIC_SAVED is the
# outcome of SAVE_USER_METRIC / DELETE_USER_METRIC to the window that sent it:
# {ok, action, id, error, conflict} (conflict: the server's copy is newer, the
# pane offers Reload / Overwrite). USER_METRIC_EXPORTED answers
# EXPORT_USER_METRIC with {id, toml, error}. One-shot announcements, out of
# REPLY_EVENTS, like the tab ones.
USER_METRICS = "USER_METRICS"
# Who uses each metric ("Used by"): {users: {metric_id: ["tab Basic, panel MAE",
# ...]}}, only metrics with a user. A piece of its own, so a colouring click
# does not make every window redraw its plots, as a new catalog would.
METRIC_USERS = "METRIC_USERS"
USER_METRIC_SAVED = "USER_METRIC_SAVED"
USER_METRIC_EXPORTED = "USER_METRIC_EXPORTED"
# Reply to REQUEST_METRIC_INPUTS: {dataset_fp, model_fp, inputs: [{ref, group,
# name, label, shape, only, origin}]}, to the window that asked.
METRIC_INPUTS = "METRIC_INPUTS"
# Reply to CHECK_METRIC: {check_id, ok, error, shape}. Reply to TRY_METRIC:
# {try_id, ok, error, cancelled, shape, count, bad_count, min, mean, max,
# bad_frames, histogram}; while it runs the window gets TASK_PROGRESS
# ([try_id], {message, progress}). Both to the window that asked only.
# Sent after SESSION_LOADED, to the window that loaded, when the session carries
# user metrics the server lacks (or has under another formula): {offer_id,
# metrics: [{id, label, expr, clash_with}]}. The window asks "Add them?" and
# answers with ADD_SESSION_METRICS {offer_id}; No sends nothing.
SESSION_METRICS = "SESSION_METRICS"
METRIC_CHECKED = "METRIC_CHECKED"
METRIC_TRIED = "METRIC_TRIED"
# Reply to REQUEST_METRIC_BOXES, to the window that asked: {request_id, base,
# base_label, setting, custom, allowed, hints, problem, id, expr, vars, label,
# unit, shape, exists} (ffast.metrics.boxes.box_state), or {request_id, error}.
METRIC_BOXES = "METRIC_BOXES"
# Reply to DECLARE_SUBSET, to the window that sent it: {fingerprint,
# parent_fingerprint, model_fp, name}, which subset the plot's view made, so
# the window that ticked SUB can show it in its main view. Every window learns
# of the subset itself from REMOTE_DATASET_META. Out of REPLY_EVENTS.
SUBSET_DECLARED = "SUBSET_DECLARED"
# A warning the server logged, to every window: {message, level} (level is
# "warning", "error" or "critical"). The web client shows it as a pop-up; the
# same text comes at most once a minute (ffast.session.warning_relay).
SERVER_WARNING = "SERVER_WARNING"


# ── which reply ends each request (ADR 0097) ────────────────────────────────
# A request's final reply carries reply_to (ffast.protocol.reply). Every
# client message is in exactly one of the three tables below; a test holds
# them to CLIENT_TO_SERVER and to the server's code.

#: request → the event of its final reply, sent with ``replies.reply``.
FINAL_REPLY = {
    LOAD_DATASET: LOAD_RESULT, LOAD_PREDICTION: LOAD_RESULT, LOAD_MODEL: LOAD_RESULT,
    LOAD_ZERO_MODEL: LOAD_RESULT, LOAD_EXAMPLE: LOAD_RESULT,
    SAVE_TAB: TAB_SAVED, DELETE_TAB: TAB_SAVED, HIDE_TAB: TAB_SAVED, SET_TAB_ORDER: TAB_SAVED,
    EXPORT_TAB: TAB_EXPORTED,
    SAVE_USER_METRIC: USER_METRIC_SAVED, DELETE_USER_METRIC: USER_METRIC_SAVED,
    ADD_SESSION_METRICS: USER_METRIC_SAVED, EXPORT_USER_METRIC: USER_METRIC_EXPORTED,
    LIST_DIR: DIR_LISTING, PROBE_DATASET_KEYS: DATASET_KEYS_RESPONSE,
    PROBE_DATASET_LENGTH: DATASET_LENGTH_RESPONSE, PROBE_DATASET_ENERGIES: DATASET_ENERGIES,
    PREVIEW_ENERGY_SAMPLE: ENERGY_SAMPLE_PREVIEW,
    SAVE_SESSION: SESSION_SAVED, LOAD_SESSION: SESSION_LOADED,
    CREATE_SUBSET: SUBSET_EXTRACTED, DECLARE_SUBSET: SUBSET_DECLARED, EXPORT_SUBSET: SUBSET_EXPORTED,
    STORED_RESULTS: STORED_RESULTS_INFO,
    PREPARE_PRACTICE_INPUT: PRACTICE_INPUT_PREPARED, START_PRACTICE_RUN: PRACTICE_RUN_STARTED,
    HEAT_PRACTICE_RUN: PRACTICE_RUN_RESULT, DELETE_PRACTICE_RUN: PRACTICE_RUN_RESULT,
    PREPARE_RUN_WITH_FFAST: RUN_WITH_FFAST_PREPARED, WRITE_RUN_WITH_FFAST: RUN_WITH_FFAST_WRITTEN,
    SUBMIT_JOB: JOB_SUBMITTED, SCAN_RUN_FOLDER: RUN_FOLDER_SCANNED,
    SCORE_DIALOG_INFO: SCORE_DIALOG_INFO_RESULT, SCORE_DATASET: SCORE_STARTED,
    CONTINUE_SCORING: SCORE_STARTED, MAKE_REFERENCE: REFERENCE_MADE,
    LIVE_ATTRIBUTION: LIVE_ATTRIBUTION_RESULT,
}

#: request → its reply, matched by an id of its own that predates reply_to
#: (request_id, try_id, check_id, view_id); these may fold into reply_to later.
OWN_ID_REPLY = {
    REQUEST_METRIC: METRIC_RESULT, REQUEST_METRIC_BOXES: METRIC_BOXES,
    CHECK_METRIC: METRIC_CHECKED, TRY_METRIC: METRIC_TRIED,
    REQUEST_METRIC_INPUTS: METRIC_INPUTS, OPEN_VIEW: SCENE_SNAPSHOT, VIEW_COMMAND: COMMAND_RESULT,
}

#: messages with no final reply, and why.
NO_FINAL_REPLY = {
    CANCEL_METRIC: "stops a request, which then ends itself",
    CANCEL_TRY_METRIC: "stops a Try, whose METRIC_TRIED says cancelled",
    CANCEL_PROBE_DATASET_LENGTH: "stops a count, whose final reply says cancelled",
    CANCEL_TASK: "the task's TASK_DONE goes to every window",
    CLOSE_VIEW: "nothing to say",
    TRACE: "a log line",
    DELETE_OBJECT: "every window hears DATASET_DELETED / MODEL_DELETED",
    RENAME_OBJECT: "every window hears the new metadata",
    FREEZE_SUBSET: "every window hears the new metadata",
    SET_DATASET_UNIT: "every window hears the new metadata",
    REQUEST_METRIC_CATALOG: "the answer is the catalog announcement",
    REQUEST_TAB_LAYOUT: "the answer is the layout announcement",
    REQUEST_USER_METRICS: "the answer is the list announcement",
    LIST_LIVE_RUNS: "LIVE_RUNS replaces the list shown, whoever asked",
    FORGET_LIVE_RUN: "LIVE_RUNS replaces the list shown, whoever asked",
    OPEN_LIVE_RUN: "LIVE_RUN_UPDATE follows the run; an error is a reply",
    LIVE_STOP: "LIVE_RUN_UPDATE follows the run; an error is a reply",
    LIVE_SET_STOP_ON_ALARM: "LIVE_RUN_UPDATE follows the run; an error is a reply",
    WATCH_JOB: "a subscription: JOB_STATE follows the job",
}
