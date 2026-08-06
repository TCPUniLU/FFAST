/**
 * Wire event names, in one place (ADR 0050).
 *
 * These strings are the protocol: each must match a constant in
 * `ffast/protocol/control.py` or a key in `ffast/protocol/rpc.py`'s
 * `SERVER_TO_CLIENT`. They were previously written as ~30 bare literals spread
 * across `app.js`, so a typo failed silently — an outbound `send` with a
 * misspelt name is dropped by the server as an unknown event (logged there,
 * invisible here), and an inbound `on` handler registered under a misspelt name
 * simply never fires.
 *
 * `protocol.js` describes the *payload* of these messages as JSDoc typedefs;
 * this module names the messages themselves. Kept separate because the typedefs
 * vanish at runtime and these values do not.
 */

/** Client → server control messages. */
export const OUT = Object.freeze({
  LOAD_DATASET: 'LOAD_DATASET',
  LOAD_PREDICTION: 'LOAD_PREDICTION',
  LIST_DIR: 'LIST_DIR',
  PROBE_DATASET_KEYS: 'PROBE_DATASET_KEYS',
  OPEN_VIEW: 'OPEN_VIEW',
  VIEW_COMMAND: 'VIEW_COMMAND',
  SAVE_SESSION: 'SAVE_SESSION',
  LOAD_SESSION: 'LOAD_SESSION',
  EXPORT_SUBSET: 'EXPORT_SUBSET',
  CREATE_SUBSET: 'CREATE_SUBSET',
  DECLARE_SUBSET: 'DECLARE_SUBSET',
  REQUEST_METRIC_CATALOG: 'REQUEST_METRIC_CATALOG',
  REQUEST_TAB_LAYOUT: 'REQUEST_TAB_LAYOUT',
  REQUEST_METRIC: 'REQUEST_METRIC',
  GRACEFUL_DISCONNECT: 'GRACEFUL_DISCONNECT',
});

/** Server → client notifications and one-shot announcements. */
export const IN = Object.freeze({
  TASK_CREATED: 'TASK_CREATED',
  TASK_PROGRESS: 'TASK_PROGRESS',
  TASK_DONE: 'TASK_DONE',
  TASK_FAILED: 'TASK_FAILED',
  DATASET_LOADED: 'DATASET_LOADED',
  MODEL_LOADED: 'MODEL_LOADED',
  REMOTE_DATASET_META: 'REMOTE_DATASET_META',
  REMOTE_MODEL_META: 'REMOTE_MODEL_META',
  SCENE_SNAPSHOT: 'SCENE_SNAPSHOT',
  SCENE_PATCH: 'SCENE_PATCH',
  COMMAND_RESULT: 'COMMAND_RESULT',
  DIR_LISTING: 'DIR_LISTING',
  DATASET_KEYS_RESPONSE: 'DATASET_KEYS_RESPONSE',
  METRIC_CATALOG: 'METRIC_CATALOG',
  METRICS_UPDATED: 'METRICS_UPDATED',
  TAB_LAYOUT: 'TAB_LAYOUT',
  SUBSET_EXPORTED: 'SUBSET_EXPORTED',
  // Outcome of SAVE_SESSION / LOAD_SESSION, carrying {ok, path, error}.
  // Replaced guessing from TASK_DONE, which names no operation (ADR 0050).
  SESSION_SAVED: 'SESSION_SAVED',
  SESSION_LOADED: 'SESSION_LOADED',
  METRIC_RESULT: 'METRIC_RESULT',
});
