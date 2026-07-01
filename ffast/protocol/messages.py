from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


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
