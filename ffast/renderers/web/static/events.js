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
  LOAD_EXAMPLE: 'LOAD_EXAMPLE',
  LIST_DIR: 'LIST_DIR',
  PROBE_DATASET_KEYS: 'PROBE_DATASET_KEYS',
  OPEN_VIEW: 'OPEN_VIEW',
  CLOSE_VIEW: 'CLOSE_VIEW',
  VIEW_COMMAND: 'VIEW_COMMAND',
  SAVE_SESSION: 'SAVE_SESSION',
  LOAD_SESSION: 'LOAD_SESSION',
  EXPORT_SUBSET: 'EXPORT_SUBSET',
  CREATE_SUBSET: 'CREATE_SUBSET',
  DELETE_OBJECT: 'DELETE_OBJECT',
  DECLARE_SUBSET: 'DECLARE_SUBSET',
  FREEZE_SUBSET: 'FREEZE_SUBSET',
  SET_DATASET_UNIT: 'SET_DATASET_UNIT',
  REQUEST_METRIC_CATALOG: 'REQUEST_METRIC_CATALOG',
  REQUEST_TAB_LAYOUT: 'REQUEST_TAB_LAYOUT',
  REQUEST_METRIC: 'REQUEST_METRIC',
  // User tabs (ADR 0056)
  SAVE_TAB: 'SAVE_TAB',
  DELETE_TAB: 'DELETE_TAB',
  HIDE_TAB: 'HIDE_TAB',
  EXPORT_TAB: 'EXPORT_TAB',
  // User metrics and the Metrics pane (ADR 0057)
  REQUEST_USER_METRICS: 'REQUEST_USER_METRICS',
  SAVE_USER_METRIC: 'SAVE_USER_METRIC',
  DELETE_USER_METRIC: 'DELETE_USER_METRIC',
  EXPORT_USER_METRIC: 'EXPORT_USER_METRIC',
  REQUEST_METRIC_INPUTS: 'REQUEST_METRIC_INPUTS',
  CHECK_METRIC: 'CHECK_METRIC',
  TRY_METRIC: 'TRY_METRIC',
  CANCEL_TRY_METRIC: 'CANCEL_TRY_METRIC',
  ADD_SESSION_METRICS: 'ADD_SESSION_METRICS',
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
  // A dataset or prediction was deleted, in any window; args[0] its fingerprint.
  DATASET_DELETED: 'DATASET_DELETED',
  MODEL_DELETED: 'MODEL_DELETED',
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
  // A dataset or prediction load that failed, {kind, path, error}.
  LOAD_FAILED: 'LOAD_FAILED',
  // Outcome of SAVE_TAB / DELETE_TAB / HIDE_TAB, {ok, action, name, error},
  // and the TOML for EXPORT_TAB (ADR 0056).
  TAB_SAVED: 'TAB_SAVED',
  TAB_EXPORTED: 'TAB_EXPORTED',
  // User metrics (ADR 0057): the list of them, the outcome of a save or delete
  // ({ok, action, id, error, conflict}), a metric as TOML, what a formula can
  // use for a dataset, the check of a formula as typed, the result of a Try.
  USER_METRICS: 'USER_METRICS',
  // Who uses each metric, {users: {metric_id: [text]}} (ADR 0062).
  METRIC_USERS: 'METRIC_USERS',
  USER_METRIC_SAVED: 'USER_METRIC_SAVED',
  USER_METRIC_EXPORTED: 'USER_METRIC_EXPORTED',
  METRIC_INPUTS: 'METRIC_INPUTS',
  METRIC_CHECKED: 'METRIC_CHECKED',
  METRIC_TRIED: 'METRIC_TRIED',
  // After a session load: user metrics it carries that this server lacks.
  SESSION_METRICS: 'SESSION_METRICS',
  // Which subset a DECLARE_SUBSET made, to the window that sent it, so the
  // window that ticked SUB can show it.
  SUBSET_DECLARED: 'SUBSET_DECLARED',
  // A warning the server logged, {message, level}: shown as a pop-up.
  SERVER_WARNING: 'SERVER_WARNING',
  METRIC_RESULT: 'METRIC_RESULT',
});
