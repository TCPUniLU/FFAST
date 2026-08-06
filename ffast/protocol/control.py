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
REQUEST_SUBDATASET_ARRAYS = "REQUEST_SUBDATASET_ARRAYS"
PROBE_DATASET_KEYS = "PROBE_DATASET_KEYS"
PROBE_DATASET_LENGTH = "PROBE_DATASET_LENGTH"
LIST_DIR = "LIST_DIR"
LOAD_PREDICTION = "LOAD_PREDICTION"
REQUEST_PREDICTION_ARRAYS = "REQUEST_PREDICTION_ARRAYS"
OPEN_VIEW = "OPEN_VIEW"
CLOSE_VIEW = "CLOSE_VIEW"
VIEW_COMMAND = "VIEW_COMMAND"
REQUEST_STATE_SYNC = "REQUEST_STATE_SYNC"
SAVE_SESSION = "SAVE_SESSION"
LOAD_SESSION = "LOAD_SESSION"
REQUEST_METRIC = "REQUEST_METRIC"
REQUEST_METRIC_CATALOG = "REQUEST_METRIC_CATALOG"
REQUEST_TAB_LAYOUT = "REQUEST_TAB_LAYOUT"
EXPORT_SUBSET = "EXPORT_SUBSET"

CLIENT_TO_SERVER = frozenset(
    {
        LOAD_DATASET, LOAD_MODEL, DELETE_OBJECT, CREATE_SUBSET, DECLARE_SUBSET,
        REQUEST_SUBDATASET_ARRAYS,
        PROBE_DATASET_KEYS, PROBE_DATASET_LENGTH, LIST_DIR, LOAD_PREDICTION,
        REQUEST_PREDICTION_ARRAYS, OPEN_VIEW, CLOSE_VIEW, VIEW_COMMAND,
        REQUEST_STATE_SYNC, SAVE_SESSION, LOAD_SESSION, REQUEST_METRIC,
        REQUEST_METRIC_CATALOG, REQUEST_TAB_LAYOUT, EXPORT_SUBSET,
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
        LOAD_PREDICTION, VIEW_COMMAND, SAVE_SESSION, LOAD_SESSION, EXPORT_SUBSET,
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
METRIC_CATALOG = "METRIC_CATALOG"
METRICS_UPDATED = "METRICS_UPDATED"
# Reply to REQUEST_TAB_LAYOUT (ADR 0045 Phase 3). Like METRIC_CATALOG it is a
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
