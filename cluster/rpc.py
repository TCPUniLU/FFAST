"""
msgpack serialization for FFAST WebSocket RPC.

Control message wire format (msgpack-encoded dict):
    {"event": str, "args": list, "kwargs": dict}

Control message values must be msgpack-serializable primitives (str, int,
float, list, dict, bytes, None).  Qt objects must never cross the wire.

Array transfer uses the same envelope but encodes each numpy array inside
kwargs as {"__ndarray__": True, "dtype": str, "shape": list, "data": bytes}.
Use pack_arrays / unpack_arrays for the SUBDATASET_ARRAYS event.
"""
import msgpack
import numpy as np

# Events the server broadcasts to every connected client via the generic
# subscription loop in server._main().
SERVER_TO_CLIENT = frozenset(
    {
        "TASK_CREATED",
        "TASK_PROGRESS",
        "TASK_DONE",
        "TASK_FAILED",
        "DATA_UPDATED",
        "DATASET_LOADED",
        "MODEL_LOADED",
        "DATASET_DELETED",
        "MODEL_DELETED",
    }
)

# Events the client listener (ServerConnection.start_listener) may safely
# re-inject into the local env via eventPush().
#
# This is NOT a pure subset of SERVER_TO_CLIENT: REMOTE_DATASET_META and
# REMOTE_MODEL_META are sent by dedicated server-side handlers, not the
# generic subscription loop, so they don't appear there.
#
# Events in SERVER_TO_CLIENT but absent here are intentionally dropped by
# the client — data-lifecycle events (DATASET_LOADED, MODEL_LOADED, …)
# would cause AttributeError because the local env has no matching objects.
CLIENT_ENV_SAFE = frozenset(
    {
        # Task-progress events — task IDs are namespaced "remote_<n>"
        # by the listener before forwarding, so they can't collide with
        # local task IDs.
        "TASK_CREATED",
        "TASK_PROGRESS",
        "TASK_DONE",
        "TASK_FAILED",
        # Remote object announcements — handled by env._onRemoteDatasetMeta
        # and env._onRemoteModelMeta to create local proxy objects.
        "REMOTE_DATASET_META",
        "REMOTE_MODEL_META",
        # Server-owned render path (ADR 0014): renderer-neutral scene data.
        # Forwarded verbatim to the Loupe scene adapter via eventPush; the
        # local env holds no matching objects, so the Loupe handlers consume
        # them directly rather than mutating env state.
        "SCENE_SNAPSHOT",
        "SCENE_PATCH",
        # Fired by server after LOAD_CONFIG loads new user metrics.
        "METRICS_UPDATED",
    }
)


def _msgpack_default(obj):
    """Encode types msgpack can't handle natively.
    set -> sorted list (ScenePatch.changed, and any future set field)."""
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"Cannot msgpack-serialize {type(obj).__name__}")


def pack(event: str, args: tuple, kwargs: dict) -> bytes:
    """Serialize event + args/kwargs to msgpack bytes."""
    return msgpack.packb(
        {"event": event, "args": list(args), "kwargs": kwargs},
        use_bin_type=True,
        default=_msgpack_default,
    )


def unpack(data: bytes) -> tuple:
    """Deserialize msgpack bytes → (event, args, kwargs)."""
    msg = msgpack.unpackb(data, raw=False)
    return msg["event"], msg["args"], msg["kwargs"]


# ── numpy array transfer ──────────────────────────────────────────────────────

def _encode_array(arr):
    """Encode a single numpy array as a msgpack-serializable dict."""
    a = np.ascontiguousarray(arr)
    return {
        "__ndarray__": True,
        "dtype": str(a.dtype),
        "shape": list(a.shape),
        "data": a.tobytes(),
    }


def _decode_array(v):
    """Decode a dict produced by _encode_array back to a numpy array."""
    arr = np.frombuffer(bytes(v["data"]), dtype=v["dtype"])
    return arr.reshape(v["shape"]).copy()


def pack_arrays(fingerprint: str, arrays: dict, **extra_kwargs) -> bytes:
    """Serialize a SUBDATASET_ARRAYS response message.

    ``arrays`` values may be numpy arrays or None (e.g. when forces are
    unavailable).  Returns msgpack bytes using the standard event envelope so
    the receiver's existing ``unpack`` call can dispatch on ``event``.

    Any ``extra_kwargs`` are included in the outer msgpack kwargs dict
    alongside ``arrays`` — use this to pass small primitive payloads such as
    ``model_names`` (a plain dict of str→str) without re-encoding as arrays.

    Example::

        data = pack_arrays(
            fp,
            {"R": coords, "F": forces, "z": atomic_nums},
            model_names={"abc123": "SO3LR"},
        )
        await websocket.send(data)
    """
    encoded = {}
    for k, v in arrays.items():
        encoded[k] = None if v is None else _encode_array(v)
    return pack(
        "SUBDATASET_ARRAYS", (fingerprint,), {"arrays": encoded, **extra_kwargs}
    )


def unpack_arrays(kwargs: dict) -> dict:
    """Decode the ``arrays`` payload from a SUBDATASET_ARRAYS kwargs dict.

    Returns a plain dict mapping key → numpy array (or None).

    Example::

        event, args, kw = unpack(data)
        if event == "SUBDATASET_ARRAYS":
            fp = args[0]
            arrays = unpack_arrays(kw)
    """
    result = {}
    for k, v in kwargs["arrays"].items():
        if v is None:
            result[k] = None
        elif isinstance(v, dict) and v.get("__ndarray__"):
            result[k] = _decode_array(v)
        else:
            result[k] = v
    return result


def pack_prediction_arrays(
    dataset_fp: str, model_fp: str, arrays: dict
) -> bytes:
    """Serialize a PREDICTION_ARRAYS response (prediction-only channel).

    ``arrays`` should have keys ``pred__energy__<model_fp>`` and/or
    ``pred__forces__<model_fp>``.  Uses the same numpy encoding as
    :func:`pack_arrays` but emits a ``PREDICTION_ARRAYS`` event so the
    listener can distinguish it from ``SUBDATASET_ARRAYS``.

    The receiver unpacks with the standard :func:`unpack_arrays` helper.
    """
    encoded = {}
    for k, v in arrays.items():
        encoded[k] = None if v is None else _encode_array(v)
    return pack(
        "PREDICTION_ARRAYS",
        (dataset_fp, model_fp),
        {"arrays": encoded},
    )


# ── server-computed metric results (Stage 4a) ───────────────────────────────────

def pack_metric_result(key: str, metric_id: str, ok: bool, result=None) -> bytes:
    """Serialize a METRIC_RESULT response (server-owned metric computation).

    ``result`` is a ``MetricResult`` (or ``None`` when ``ok`` is False — e.g. the
    server can't source a real client-only model).  Its ``values`` array is
    numpy-encoded; the small identity metadata travels as plain primitives so the
    client can reconstruct an equivalent ``MetricResult``.

    Typed at the pack site against ``MetricResultMessage`` (the sole producer).
    ``model_dump(exclude_none=True)`` reproduces the pre-typing wire shape:
    ``{"ok": False}`` when not ok, the full metadata + encoded array when ok.
    """
    from ffast.protocol.messages import MetricResultMessage

    if ok and result is not None:
        msg = MetricResultMessage(
            ok=True,
            metric_id=result.metric_id,
            shape=result.shape,
            dtype=result.dtype,
            unit=result.unit,
            compute_parameters=result.compute_parameters,
            implementation_hash=result.implementation_hash,
            checksum=result.checksum,
            values=_encode_array(np.asarray(result.values)),
        )
    else:
        msg = MetricResultMessage(ok=bool(ok))
    return pack(
        "METRIC_RESULT", (key, metric_id), msg.model_dump(exclude_none=True)
    )


def unpack_metric_result(kwargs: dict):
    """Reconstruct a ``MetricResult`` from a METRIC_RESULT payload, or ``None``.

    Validates the incoming payload against ``MetricResultMessage`` (loud error on
    producer drift) before rebuilding the ``MetricResult``.
    """
    from ffast.protocol.messages import MetricResultMessage

    msg = MetricResultMessage.model_validate(kwargs)
    if not msg.ok:
        return None
    from ffast.metrics.models import MetricResult

    v = msg.values
    values = (
        _decode_array(v)
        if isinstance(v, dict) and v.get("__ndarray__")
        else v
    )
    return MetricResult(
        metric_id=msg.metric_id,
        shape=msg.shape,
        dtype=msg.dtype,
        unit=msg.unit,
        compute_parameters=msg.compute_parameters,
        implementation_hash=msg.implementation_hash,
        checksum=msg.checksum,
        values=values,
    )
