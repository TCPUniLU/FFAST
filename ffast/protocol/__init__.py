"""Typed transport-message contracts for the ffast renderer/server protocol.

Renderer-neutral wire payloads exchanged over the RPC channel (cluster/rpc).
The visualization-specific contract (View Commands, Scene Snapshots/Patches,
capability negotiation) lives in ``ffast.visualization``; this package holds the
remaining control/announcement messages as they are progressively migrated off
hand-rolled untyped dicts (see docs/legacy-thinning-plan.md, Slice 3).
"""

from ffast.protocol.notifications import BROADCAST_EVENTS
from ffast.protocol.messages import (
    DatasetKeysResponse,
    DatasetLengthResponse,
    DatasetMeta,
    DirEntry,
    DirListing,
    MetricCatalog,
    MetricCatalogEntry,
    MetricParameter,
    MetricResultMessage,
    ModelMeta,
)

__all__ = [
    "DatasetMeta",
    "ModelMeta",
    "MetricParameter",
    "MetricCatalogEntry",
    "MetricCatalog",
    "MetricResultMessage",
    "DatasetKeysResponse",
    "DatasetLengthResponse",
    "DirEntry",
    "DirListing",
    "BROADCAST_EVENTS",
]
