from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ffast.session.token import ClientRole

PROTOCOL_VERSION = "1.0"

_SUPPORTED_CODECS = ["raw"]
_KNOWN_FEATURES = ["zstd_buffers", "result_buffers", "worker_pool"]
# Server-declared (not client-negotiated) capability: every connection gets its
# own outbound queue, ServerSession, and view namespace over the shared
# Environment (ADR 0044). The web pop-out reads this off HELLO_ACK to decide
# whether to open its own live controller connection or fall back to the
# BroadcastChannel satellite mirror (ADR 0043) for an older, single-client server.
MULTI_CLIENT_FEATURE = "multi_client"


class ClientCapabilities(BaseModel):
    """Capabilities advertised by a renderer client on connection."""
    model_config = ConfigDict(extra="forbid")
    protocol_version: str
    renderer: Literal["vispy", "webgl", "headless"]
    supported_codecs: list[str] = Field(default_factory=lambda: ["raw"])
    features: list[str] = Field(default_factory=list)
    session_token: Optional[str] = None
    # Explicit READ_ONLY viewer opt-in (ADR 0044 Phase 2, PRD story 73): drops
    # inbound control (mutating Control messages are ignored) but still opens
    # its own views and receives shared broadcasts. Independent of the token —
    # a client may hold a valid token and still ask to be a passive viewer.
    read_only: bool = False


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
    # Always advertised — every ffast-server since ADR 0044 Phase 1 is
    # multi-client, so this isn't gated on anything the client asked for.
    shared_features.append(MULTI_CLIENT_FEATURE)

    return ServerCapabilities(
        protocol_version=PROTOCOL_VERSION,
        accepted_client_version=client.protocol_version,
        supported_codecs=shared_codecs,
        features=shared_features,
    )
