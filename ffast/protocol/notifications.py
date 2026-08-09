"""Catalogue of server→client broadcast events (the ``SERVER_TO_CLIENT`` signals).

These events are fire-and-forget *notifications*, not structured data payloads:
the server announces "something happened" plus an identifier, and clients react
by looking that id up. They are emitted by ``eventPush(NAME, *args, **kwargs)``
from many call sites across the legacy app and forwarded verbatim by the generic
broadcast loop in ``server._main()``. Unlike the typed messages in
:mod:`ffast.protocol.messages`, there is no single producer to type and no
multi-field payload that can drift — so they are *catalogued*, not modelled.

Kept honest two ways:

* **The event-name set is enforced** — ``test_protocol_messages`` asserts the
  keys below equal ``ffast.protocol.rpc.SERVER_TO_CLIENT``, so adding or removing a
  broadcast event without updating this catalogue fails the test suite.
* **The field lists are descriptive only** — the generic loop forwards the
  positional ``args`` / ``kwargs`` verbatim and ``TASK_PROGRESS`` alone has ~30
  emitters with varying keyword fields, so nothing mechanically guarantees the
  fields. See the internal legacy-thinning plan (Slice 3 / bullet 2) for why these
  are catalogued rather than typed.
"""

# event name -> payload description. ``args`` are positional, ``kwargs`` keyword.
BROADCAST_EVENTS = {
    "TASK_CREATED":    "args=(taskID:str) — a new task was created",
    "TASK_PROGRESS":   "args=(taskID:str); kwargs: message:str?, error:bool?, prog:int?, progMax:int? — task progress update",
    "TASK_DONE":       "args=(taskID:str) — task finished successfully",
    "TASK_FAILED":     "args=(taskID:str) — task failed",
    "DATA_UPDATED":    "args=(cacheKey:str) — a cache entry (prediction/metric) changed",
    "DATASET_LOADED":  "args=(fingerprint:str) — a dataset finished loading",
    "MODEL_LOADED":    "args=(fingerprint:str) — a model finished loading",
    "DATASET_DELETED": "args=(key:str) — a dataset was removed",
    "MODEL_DELETED":   "args=(key:str) — a model was removed",
}
