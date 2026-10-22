"""Canonical event-name constants for the RPC Channel's Control and Array
messages (ADR 0033).

Complements ``ffast.protocol.notifications`` (which catalogues the *Broadcast
Events* — ``SERVER_TO_CLIENT`` in ``ffast.protocol.rpc``). This module covers
everything else that crosses the wire: client→server Control messages, their
server→client replies, the handshake, and the untyped Array messages — so a
send site never has to spell an event name as a bare string literal, and one
module answers "what can cross this wire?"

Two groups are drift-tested (see ``tests/ffast/test_control_events.py``):

- ``CLIENT_TO_SERVER`` must equal ``ServerSession._handlers.keys()``.
- ``REPLY_EVENTS`` must equal ``cluster.connection._REPLY_CHANNELS.keys()``.
"""

# ── client → server Control messages (ServerSession._handlers) ──────────────
LOAD_DATASET = "LOAD_DATASET"
LOAD_MODEL = "LOAD_MODEL"
DELETE_OBJECT = "DELETE_OBJECT"
CREATE_SUBSET = "CREATE_SUBSET"
DECLARE_SUBSET = "DECLARE_SUBSET"
# Freeze a live subset's frames as a frozen subset (the desktop's freeze).
FREEZE_SUBSET = "FREEZE_SUBSET"
# Name the unit of a dataset's energies or forces, which no file declares. The
# dataset is announced again (REMOTE_DATASET_META) to every window.
SET_DATASET_UNIT = "SET_DATASET_UNIT"
REQUEST_SUBDATASET_ARRAYS = "REQUEST_SUBDATASET_ARRAYS"
PROBE_DATASET_KEYS = "PROBE_DATASET_KEYS"
PROBE_DATASET_LENGTH = "PROBE_DATASET_LENGTH"
LIST_DIR = "LIST_DIR"
LOAD_PREDICTION = "LOAD_PREDICTION"
REQUEST_PREDICTION_ARRAYS = "REQUEST_PREDICTION_ARRAYS"
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
# Answer "Add them?" after a session load offered the user metrics it carries
# that this server lacks (rule 15).
ADD_SESSION_METRICS = "ADD_SESSION_METRICS"

CLIENT_TO_SERVER = frozenset(
    {
        LOAD_DATASET, LOAD_MODEL, DELETE_OBJECT, CREATE_SUBSET, DECLARE_SUBSET,
        FREEZE_SUBSET, SET_DATASET_UNIT, REQUEST_SUBDATASET_ARRAYS,
        PROBE_DATASET_KEYS, PROBE_DATASET_LENGTH, LIST_DIR, LOAD_PREDICTION,
        REQUEST_PREDICTION_ARRAYS, OPEN_VIEW, CLOSE_VIEW, VIEW_COMMAND,
        SAVE_SESSION, LOAD_SESSION, REQUEST_METRIC,
        REQUEST_METRIC_CATALOG, REQUEST_TAB_LAYOUT, EXPORT_SUBSET,
        SAVE_TAB, DELETE_TAB, HIDE_TAB, EXPORT_TAB,
        REQUEST_USER_METRICS, SAVE_USER_METRIC, DELETE_USER_METRIC, EXPORT_USER_METRIC,
        REQUEST_METRIC_INPUTS, CHECK_METRIC, TRY_METRIC, CANCEL_TRY_METRIC,
        ADD_SESSION_METRICS,
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
        FREEZE_SUBSET, SET_DATASET_UNIT, LOAD_PREDICTION, VIEW_COMMAND, SAVE_SESSION, LOAD_SESSION, EXPORT_SUBSET,
        SAVE_TAB, DELETE_TAB, HIDE_TAB, SAVE_USER_METRIC, DELETE_USER_METRIC,
        ADD_SESSION_METRICS,
    }
)

# ── handshake / connection lifecycle (outside ServerSession.dispatch — the
# server intercepts these in server.py before role-gated dispatch runs) ─────
HELLO = "HELLO"
HELLO_ACK = "HELLO_ACK"
GRACEFUL_DISCONNECT = "GRACEFUL_DISCONNECT"

# ── server → client replies (cluster.connection._REPLY_CHANNELS) ────────────
DATASET_KEYS_RESPONSE = "DATASET_KEYS_RESPONSE"
DATASET_LENGTH_RESPONSE = "DATASET_LENGTH_RESPONSE"
PREDICTION_ARRAYS = "PREDICTION_ARRAYS"
METRIC_RESULT = "METRIC_RESULT"
SUBDATASET_ARRAYS = "SUBDATASET_ARRAYS"
DIR_LISTING = "DIR_LISTING"

REPLY_EVENTS = frozenset(
    {
        DATASET_KEYS_RESPONSE, DATASET_LENGTH_RESPONSE, PREDICTION_ARRAYS,
        METRIC_RESULT, SUBDATASET_ARRAYS, DIR_LISTING,
    }
)

# ── server → client dedicated announcements + view-lifecycle push (not a
# reply, not a generic Broadcast Event — each has a single named producer) ──
REMOTE_DATASET_META = "REMOTE_DATASET_META"
REMOTE_MODEL_META = "REMOTE_MODEL_META"
SCENE_SNAPSHOT = "SCENE_SNAPSHOT"
SCENE_PATCH = "SCENE_PATCH"
COMMAND_RESULT = "COMMAND_RESULT"
# Pushed to a window on connect and to every window after a change (ADR 0062);
# also the answer to REQUEST_METRIC_CATALOG, which only the asker gets.
METRIC_CATALOG = "METRIC_CATALOG"
METRICS_UPDATED = "METRICS_UPDATED"
# Pushed on connect and after a tab change (ADR 0062); also the answer to
# REQUEST_TAB_LAYOUT (ADR 0045 Phase 3). Like METRIC_CATALOG it is a
# dedicated announcement, not a correlated reply channel — only the web client
# asks for it, and it carries the whole layout in one shot, so it stays out of
# REPLY_EVENTS (no cluster.connection correlator entry).
TAB_LAYOUT = "TAB_LAYOUT"
# Reply to EXPORT_SUBSET (ADR 0045 Phase 4, issue 20). A dedicated one-shot
# announcement (like TAB_LAYOUT): the server writes the extxyz and reports the
# written path (or an error) so the browser can confirm the export. Only the
# web client asks for it, so it stays out of REPLY_EVENTS.
SUBSET_EXPORTED = "SUBSET_EXPORTED"
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
# Outcome of SAVE_TAB / DELETE_TAB / HIDE_TAB (ADR 0056): {ok, action, name,
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
# Reply to DECLARE_SUBSET, to the window that sent it: {fingerprint,
# parent_fingerprint, model_fp, name}, which subset the plot's view made, so
# the window that ticked SUB can show it in its main view. Every window learns
# of the subset itself from REMOTE_DATASET_META. Out of REPLY_EVENTS.
SUBSET_DECLARED = "SUBSET_DECLARED"
