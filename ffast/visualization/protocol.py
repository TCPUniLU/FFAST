from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ffast.session.token import ClientRole

PROTOCOL_VERSION = "1.0"

_SUPPORTED_CODECS = ["raw"]
_KNOWN_FEATURES = ["zstd_buffers", "result_buffers", "worker_pool"]


class ClientCapabilities(BaseModel):
    """Capabilities advertised by a renderer client on connection."""
    model_config = ConfigDict(extra="forbid")
    protocol_version: str
    renderer: Literal["vispy", "webgl", "headless"]
    supported_codecs: list[str] = Field(default_factory=lambda: ["raw"])
    features: list[str] = Field(default_factory=list)
    session_token: Optional[str] = None


class ServerCapabilities(BaseModel):
    """Capabilities returned by the server after capability negotiation."""
    model_config = ConfigDict(extra="forbid")
    protocol_version: str
    accepted_client_version: str
    supported_codecs: list[str]
    features: list[str] = Field(default_factory=list)
    role: ClientRole = ClientRole.READ_ONLY


def negotiate(client: ClientCapabilities) -> ServerCapabilities:
    """Negotiate capabilities; role is READ_ONLY until the handler validates the token."""
    shared_codecs = [c for c in client.supported_codecs if c in _SUPPORTED_CODECS]
    if not shared_codecs:
        shared_codecs = ["raw"]

    shared_features = [f for f in client.features if f in _KNOWN_FEATURES]

    return ServerCapabilities(
        protocol_version=PROTOCOL_VERSION,
        accepted_client_version=client.protocol_version,
        supported_codecs=shared_codecs,
        features=shared_features,
    )
