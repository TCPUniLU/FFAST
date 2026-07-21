from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# ── Control message requests (client→server, ADR 0033) ──────────────────────
# Typed payloads for ServerSession._handlers. Validated at dispatch as a gate
# only (see ServerSession.dispatch) — a malformed message is dropped with the
# event named in the log, but the handler is still called with the resolved
# args/kwargs exactly as before, so presence-sensitive fields (e.g.
# OpenViewRequest.prediction_ref) keep their existing absent-vs-null meaning.


class DatasetMeta(BaseModel):
    """Typed payload for the ``REMOTE_DATASET_META`` transport message.

    The server announces each loaded dataset to connected renderer clients
    without transferring coordinate arrays (Stage 4c lazy proxy). This is the
    single typed contract for that payload — it mirrors the dict returned by
    ``DatasetLoader.toMetaDict`` / ``VariableDatasetLoader.toMetaDict`` so the
    wire shape is identical, while ``extra="forbid"`` makes any future drift in
    those producers a loud validation error instead of a silently untyped dict.

    The dataset ``fingerprint`` is NOT part of this model: it travels as the
    event's positional argument (``args[0]``), not inside the meta payload.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    n: Optional[int] = None
    has_forces: bool = True
    is_sub: bool = False
    variable: bool = False
    elements: Optional[list[int]] = None
    offsets: Optional[list[int]] = None
    path: Optional[str] = None
    source_type: Optional[str] = None


class ModelMeta(BaseModel):
    """Typed payload for the ``REMOTE_MODEL_META`` transport message.

    The server announces a registered (ghost or server-side) model and the
    dataset fingerprints it has cached predictions for, so the client can create
    a local ``GhostModelLoader`` proxy. As with :class:`DatasetMeta`, the model
    ``fingerprint`` travels as the event's positional argument, not in this
    payload, and ``extra="forbid"`` turns producer drift into a loud error.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    dataset_fingerprints: Optional[list[str]] = None


class MetricParameter(BaseModel):
    """One tunable parameter in a :class:`MetricCatalogEntry`.

    The flattened transport form of a ``ParameterSchema`` (see
    ``ffast.metrics.catalog._param_to_dict``). ``default`` / ``min`` / ``max`` are
    ``Any`` on purpose: they carry the parameter's native numeric type (int vs
    float) unchanged, so the catalog round-trips bit-for-bit.
    """

    model_config = ConfigDict(extra="forbid")

    type: Optional[str] = None
    default: Any = None
    label: str = ""
    choices: list = Field(default_factory=list)
    min: Any = None
    max: Any = None


class MetricCatalogEntry(BaseModel):
    """One metric in the ``METRIC_CATALOG`` message (see ffast.metrics.catalog)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    shape: str
    unit: Optional[str] = None
    parameters: dict[str, MetricParameter] = Field(default_factory=dict)


class MetricCatalog(BaseModel):
    """Payload for the ``METRIC_CATALOG`` message: every registered metric.

    The server owns the registry and announces it so clients build metric
    controls from the server's catalog rather than a local registry (ADR 0016).
    """

    model_config = ConfigDict(extra="forbid")

    metrics: list[MetricCatalogEntry] = Field(default_factory=list)


class DatasetKeysResponse(BaseModel):
    """Reply to ``PROBE_DATASET_KEYS`` — available energy/force key names in a file."""

    model_config = ConfigDict(extra="forbid")

    energy_keys: list[str] = Field(default_factory=list)
    force_keys: list[str] = Field(default_factory=list)
    has_calculator_energy: bool = False
    has_calculator_forces: bool = False
    error: Optional[str] = None


class DatasetLengthResponse(BaseModel):
    """Reply to ``PROBE_DATASET_LENGTH`` — total frame count, or an error."""

    model_config = ConfigDict(extra="forbid")

    n: Optional[int] = None
    error: Optional[str] = None


class DirEntry(BaseModel):
    """One filesystem entry in a :class:`DirListing`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    is_dir: bool
    size: int = 0


class DirListing(BaseModel):
    """Reply to ``LIST_DIR`` — a server-side directory listing for the file picker."""

    model_config = ConfigDict(extra="forbid")

    path: str
    parent: Optional[str] = None
    home: str
    entries: list[DirEntry] = Field(default_factory=list)
    error: Optional[str] = None


class MetricResultMessage(BaseModel):
    """Typed envelope for the ``METRIC_RESULT`` message (server-owned metric
    computation, Stage 4a).

    METRIC_RESULT is a *hybrid* message: structured metadata plus one numpy
    array. Per the **RPC Channel** boundary in CONTEXT.md it is typed on its
    metadata envelope while the ``values`` array rides inside it encoded by
    ``ffast.protocol.rpc._encode_array`` (the same binary form used by the untyped
    ``SUBDATASET_ARRAYS`` / ``PREDICTION_ARRAYS`` Array messages). So ``values``
    is an opaque ``{__ndarray__, dtype, shape, data}`` dict here, not a typed
    field.

    Single producer ([`ffast.protocol.rpc.pack_metric_result`], called by
    ``server._send_metric_result``); single consumer
    (``ffast.protocol.rpc.unpack_metric_result``, called by the listener in
    ``cluster.connection``). The metadata fields mirror
    ``ffast.metrics.models.MetricResult`` so the client can rebuild an
    equivalent result.

    When ``ok`` is False (the server can't source a real client-only model) the
    wire payload is just ``{"ok": False}`` — the metadata/values fields are
    ``None`` and dropped via ``model_dump(exclude_none=True)``, preserving the
    pre-typing wire shape exactly.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    metric_id: Optional[str] = None
    shape: Optional[str] = None
    dtype: Optional[str] = None
    unit: Optional[str] = None
    compute_parameters: Optional[dict[str, Any]] = None
    implementation_hash: Optional[str] = None
    checksum: Optional[str] = None
    values: Optional[dict] = None


# ── Control message requests (client→server) ─────────────────────────────────

class LoadDatasetRequest(BaseModel):
    """Typed payload for ``LOAD_DATASET`` (see ``ServerSession._on_load_dataset``).

    ``prediction_keys`` travels as a list of 2-element lists — msgpack has no
    tuple type — the handler restores them to tuples before calling
    ``env.taskLoadDataset``.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    dataset_type: str
    selected_energy_key: Optional[str] = None
    selected_force_key: Optional[str] = None
    prediction_keys: Optional[list] = None
    slice_num: Optional[int] = None


class LoadModelRequest(BaseModel):
    """Typed payload for ``LOAD_MODEL``."""

    model_config = ConfigDict(extra="forbid")

    path: str
    model_type: str


class DeleteObjectRequest(BaseModel):
    """Typed payload for ``DELETE_OBJECT``."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str


class CreateSubsetRequest(BaseModel):
    """Typed payload for ``CREATE_SUBSET`` (ADR 0045 issue 12).

    ``indices`` is the same mixed atom-filter spec the view "hide atoms" filter
    accepts — integer atom indices and element-symbol tokens ("C", "-H"),
    resolved server-side against the parent dataset. The result is a new
    ``AtomFilteredDataset`` announced via ``REMOTE_DATASET_META``.
    """

    model_config = ConfigDict(extra="forbid")

    parent_fingerprint: str
    indices: list[Union[int, str]]


class RequestSubdatasetArraysRequest(BaseModel):
    """Typed payload for ``REQUEST_SUBDATASET_ARRAYS``."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str


class ProbeDatasetKeysRequest(BaseModel):
    """Typed payload for ``PROBE_DATASET_KEYS``."""

    model_config = ConfigDict(extra="forbid")

    path: str
    dataset_type: str


class ProbeDatasetLengthRequest(BaseModel):
    """Typed payload for ``PROBE_DATASET_LENGTH``."""

    model_config = ConfigDict(extra="forbid")

    path: str


class ListDirRequest(BaseModel):
    """Typed payload for ``LIST_DIR``. ``path=None`` starts at the server
    user's home directory."""

    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = None


class LoadPredictionRequest(BaseModel):
    """Typed payload for ``LOAD_PREDICTION``."""

    model_config = ConfigDict(extra="forbid")

    path: str
    dataset_fp: str
    selected_energy_key: Optional[str] = None
    selected_force_key: Optional[str] = None


class RequestPredictionArraysRequest(BaseModel):
    """Typed payload for ``REQUEST_PREDICTION_ARRAYS``."""

    model_config = ConfigDict(extra="forbid")

    dataset_fp: str
    model_fp: str


class OpenViewRequest(BaseModel):
    """Typed payload for ``OPEN_VIEW``.

    All fields are optional — a fresh ``view_id`` is generated server-side
    when absent. ``prediction_ref`` is validated here but the handler still
    reads it from the raw kwargs to preserve the absent-vs-explicit-null
    distinction (presence clears the overlay; absence leaves it untouched).
    """

    model_config = ConfigDict(extra="forbid")

    view_id: Optional[str] = None
    dataset_ref: Optional[str] = None
    prediction_ref: Optional[str] = None


class CloseViewRequest(BaseModel):
    """Typed payload for ``CLOSE_VIEW``."""

    model_config = ConfigDict(extra="forbid")

    view_id: str


class RequestMetricRequest(BaseModel):
    """Typed payload for ``REQUEST_METRIC`` (server-owned metric computation,
    Stage 4a)."""

    model_config = ConfigDict(extra="forbid")

    metric_id: str
    key: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    model_fp: Optional[str] = None
    dataset_fp: Optional[str] = None


class SaveSessionRequest(BaseModel):
    """Typed payload for ``SAVE_SESSION``."""

    model_config = ConfigDict(extra="forbid")

    path: str


class LoadSessionRequest(BaseModel):
    """Typed payload for ``LOAD_SESSION``."""

    model_config = ConfigDict(extra="forbid")

    path: str


class EmptyRequest(BaseModel):
    """Typed payload for Control messages that carry no fields at all
    (``REQUEST_STATE_SYNC``, ``REQUEST_METRIC_CATALOG``) — ``extra="forbid"``
    still catches an unexpected stray kwarg."""

    model_config = ConfigDict(extra="forbid")
