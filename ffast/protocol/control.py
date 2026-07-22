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

CLIENT_TO_SERVER = frozenset(
    {
        LOAD_DATASET, LOAD_MODEL, DELETE_OBJECT, CREATE_SUBSET, DECLARE_SUBSET,
        REQUEST_SUBDATASET_ARRAYS,
        PROBE_DATASET_KEYS, PROBE_DATASET_LENGTH, LIST_DIR, LOAD_PREDICTION,
        REQUEST_PREDICTION_ARRAYS, OPEN_VIEW, CLOSE_VIEW, VIEW_COMMAND,
        REQUEST_STATE_SYNC, SAVE_SESSION, LOAD_SESSION, REQUEST_METRIC,
        REQUEST_METRIC_CATALOG, REQUEST_TAB_LAYOUT,
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
