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

from ffast.protocol import control

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
        control.REMOTE_DATASET_META,
        control.REMOTE_MODEL_META,
        # Server-owned render path (ADR 0014): renderer-neutral scene data.
        # Forwarded verbatim to the Loupe scene adapter via eventPush; the
        # local env holds no matching objects, so the Loupe handlers consume
        # them directly rather than mutating env state.
        control.SCENE_SNAPSHOT,
        control.SCENE_PATCH,
        # Fired by server after LOAD_CONFIG loads new user metrics.
        control.METRICS_UPDATED,
    }
)


def _msgpack_default(obj):
    """Encode types msgpack can't handle natively.
    set -> sorted list (ScenePatch.changed, and any future set field)."""
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"Cannot msgpack-serialize {type(obj).__name__}")


def pack(event: str, args: tuple, kwargs: dict, *, reply_to=None,
         final: bool = True, error: str | None = None) -> bytes:
    """Serialize event + args/kwargs to msgpack bytes.

    With ``reply_to`` the message answers that request (ADR 0097): it also
    carries ``final`` and, when the request failed, ``error``. Use
    ``ffast.protocol.reply.reply`` rather than passing these by hand.
    """
    msg = {"event": event, "args": list(args), "kwargs": kwargs}
    if reply_to is not None:
        msg["reply_to"] = reply_to
        msg["final"] = bool(final or error is not None)
        if error is not None:
            msg["error"] = str(error)
    return msgpack.packb(msg, use_bin_type=True, default=_msgpack_default)


def unpack(data: bytes) -> tuple:
    """Deserialize msgpack bytes → (event, args, kwargs)."""
    msg = msgpack.unpackb(data, raw=False)
    return msg["event"], msg["args"], msg["kwargs"]


def unpack_with_id(data: bytes) -> tuple:
    """Deserialize msgpack bytes → (event, args, kwargs, id). ``id`` numbers
    the message: replies name it (ADR 0097) and the timing log uses it
    (ADR 0072). None from a page that numbers nothing."""
    msg = msgpack.unpackb(data, raw=False)
    return msg["event"], msg["args"], msg["kwargs"], msg.get("id")


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
        control.SUBDATASET_ARRAYS, (fingerprint,), {"arrays": encoded, **extra_kwargs}
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
        control.PREDICTION_ARRAYS,
        (dataset_fp, model_fp),
        {"arrays": encoded},
    )


# ── server-computed metric results (Stage 4a) ───────────────────────────────────

def pack_metric_result(request_id: str, metric_id: str, ok: bool, result=None,
                       error: str | None = None, kind: str | None = None) -> bytes:
    """Serialize a METRIC_RESULT response (server-owned metric computation).

    ``result`` is a ``MetricResult`` (or ``None`` when ``ok`` is False — e.g. the
    server can't source a real client-only model).  Its ``values`` array is
    numpy-encoded; the small identity metadata travels as plain primitives so the
    client can reconstruct an equivalent ``MetricResult``. ``error`` says why
    a metric that failed did (ADR 0066) and ``kind`` what sort of reason it is
    (ADR 0085); both are sent only when ``ok`` is False.

    ``request_id`` is the label the client sent with ``REQUEST_METRIC``; replies
    are matched by it (ADR 0061).

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
            values=_encode_array(np.asarray(result.values)),
            warnings=list(getattr(result, "warnings", None) or []) or None,
            tag=getattr(result, "_tag", None),
        )
    else:
        msg = MetricResultMessage(ok=bool(ok), error=error or None, kind=kind or None)
    return pack(
        control.METRIC_RESULT, (request_id, metric_id), msg.model_dump(exclude_none=True)
    )


def pack_plot_view(request_id: str, outcomes: dict, idx=None, n=None) -> bytes:
    """Serialize a PLOT_VIEW reply (ADR 0099). ``outcomes``: role → a
    ``MetricResult`` or ``(error, kind)``. With ``idx`` each result is sent
    at those indices only; a role's values are left out when another role
    has no result (the plot draws nothing then)."""
    from ffast.metrics.models import MetricResult
    from ffast.protocol.messages import MetricResultMessage, PlotViewMessage

    complete = all(isinstance(o, MetricResult) for o in outcomes.values())
    results = {}
    for role, o in outcomes.items():
        if isinstance(o, MetricResult):
            values = np.asarray(o.values)
            if idx is not None:
                values = values[idx]
            msg = MetricResultMessage(
                ok=True, metric_id=o.metric_id, shape=o.shape, dtype=o.dtype, unit=o.unit,
                compute_parameters=o.compute_parameters,
                implementation_hash=o.implementation_hash,
                values=_encode_array(values) if complete else None,
                warnings=list(o.warnings or []) or None,
            )
        else:
            error, kind = o
            msg = MetricResultMessage(ok=False, error=error or None, kind=kind or None)
        results[role] = msg.model_dump(exclude_none=True)
    reply = PlotViewMessage(
        ok=complete, results=results,
        idx=_encode_array(np.asarray(idx, dtype=np.int32)) if idx is not None and complete else None,
        n=n if idx is not None and complete else None,
    )
    return pack(control.PLOT_VIEW, (request_id,), reply.model_dump(exclude_none=True))


def pack_metric_unchanged(request_id: str, metric_id: str, tag: str) -> bytes:
    """Serialize a METRIC_RESULT that sends no values: the page already
    holds the stored result named ``tag`` (it asked with ``have``)."""
    from ffast.protocol.messages import MetricResultMessage

    msg = MetricResultMessage(ok=True, metric_id=metric_id, tag=tag, unchanged=True)
    return pack(
        control.METRIC_RESULT, (request_id, metric_id), msg.model_dump(exclude_none=True)
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
        values=values,
        warnings=msg.warnings or [],
    )
