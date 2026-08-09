"""Typed transport-message contracts for the ffast renderer/server protocol.

Renderer-neutral wire payloads exchanged over the RPC Channel (``ffast.protocol.rpc``).
The visualization-specific contract (View Commands, Scene Snapshots/Patches,
capability negotiation) lives in ``ffast.visualization``; this package holds the
remaining control/announcement messages as they are progressively migrated off
hand-rolled untyped dicts (see the internal legacy-thinning plan, Slice 3). Event
names for all of it live in ``ffast.protocol.control`` and
``ffast.protocol.notifications`` (ADR 0033).
"""

from ffast.protocol.notifications import BROADCAST_EVENTS
from ffast.protocol.messages import (
    CloseViewRequest,
    DatasetKeysResponse,
    DatasetLengthResponse,
    DatasetMeta,
    DeclareSubsetRequest,
    DeleteObjectRequest,
    DirEntry,
    DirListing,
    EmptyRequest,
    ExportSubsetRequest,
    ListDirRequest,
    LoadDatasetRequest,
    LoadModelRequest,
    LoadPredictionRequest,
    LoadSessionRequest,
    MetricCatalog,
    MetricCatalogEntry,
    MetricParameter,
    MetricResultMessage,
    ModelMeta,
    OpenViewRequest,
    ProbeDatasetKeysRequest,
    ProbeDatasetLengthRequest,
    RequestMetricRequest,
    RequestPredictionArraysRequest,
    RequestSubdatasetArraysRequest,
    SaveSessionRequest,
    TabLayout,
)

__all__ = [
    "DatasetMeta",
    "ModelMeta",
    "MetricParameter",
    "MetricCatalogEntry",
    "MetricCatalog",
    "TabLayout",
    "MetricResultMessage",
    "DatasetKeysResponse",
    "DatasetLengthResponse",
    "DirEntry",
    "DirListing",
    "BROADCAST_EVENTS",
    "LoadDatasetRequest",
    "LoadModelRequest",
    "DeleteObjectRequest",
    "RequestSubdatasetArraysRequest",
    "ProbeDatasetKeysRequest",
    "ProbeDatasetLengthRequest",
    "ListDirRequest",
    "LoadPredictionRequest",
    "RequestPredictionArraysRequest",
    "OpenViewRequest",
    "CloseViewRequest",
    "RequestMetricRequest",
    "SaveSessionRequest",
    "LoadSessionRequest",
    "ExportSubsetRequest",
    "EmptyRequest",
]
