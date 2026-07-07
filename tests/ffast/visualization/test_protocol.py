import pytest

from ffast.visualization.protocol import (
    PROTOCOL_VERSION,
    ClientCapabilities,
    ServerCapabilities,
    negotiate,
)


# --- PROTOCOL_VERSION ---

def test_protocol_version_is_string():
    assert isinstance(PROTOCOL_VERSION, str)
    assert len(PROTOCOL_VERSION) > 0


# --- ClientCapabilities ---

def test_client_capabilities_defaults():
    caps = ClientCapabilities(protocol_version="1.0", renderer="vispy")
    assert "raw" in caps.supported_codecs
    assert caps.features == []


@pytest.mark.parametrize("renderer", ["vispy", "webgl", "headless"])
def test_client_capabilities_accepts_known_renderer(renderer):
    caps = ClientCapabilities(protocol_version="1.0", renderer=renderer)
    assert caps.renderer == renderer


def test_client_capabilities_invalid_renderer():
    with pytest.raises(Exception):
        ClientCapabilities(protocol_version="1.0", renderer="qt")


def test_client_capabilities_rejects_extra_fields():
    with pytest.raises(Exception):
        ClientCapabilities(protocol_version="1.0", renderer="vispy", unknown=True)


def test_client_capabilities_with_features():
    caps = ClientCapabilities(
        protocol_version="1.0", renderer="vispy",
        features=["zstd_buffers"],
    )
    assert "zstd_buffers" in caps.features


def test_client_capabilities_with_zstd_codec():
    caps = ClientCapabilities(
        protocol_version="1.0", renderer="vispy",
        supported_codecs=["raw", "zstd"],
    )
    assert "zstd" in caps.supported_codecs


# --- ServerCapabilities ---

def test_server_capabilities_fields():
    caps = ServerCapabilities(
        protocol_version="1.0",
        accepted_client_version="1.0",
        supported_codecs=["raw"],
    )
    assert caps.protocol_version == "1.0"
    assert caps.accepted_client_version == "1.0"
    assert "raw" in caps.supported_codecs


def test_server_capabilities_empty_features():
    caps = ServerCapabilities(
        protocol_version="1.0",
        accepted_client_version="1.0",
        supported_codecs=["raw"],
    )
    assert caps.features == []


# --- negotiate ---

def test_negotiate_returns_server_caps():
    client = ClientCapabilities(protocol_version="1.0", renderer="vispy")
    server = negotiate(client)
    assert isinstance(server, ServerCapabilities)


def test_negotiate_sets_protocol_version():
    client = ClientCapabilities(protocol_version="1.0", renderer="vispy")
    server = negotiate(client)
    assert server.protocol_version == PROTOCOL_VERSION


def test_negotiate_reflects_client_version():
    client = ClientCapabilities(protocol_version="0.9", renderer="vispy")
    server = negotiate(client)
    assert server.accepted_client_version == "0.9"


def test_negotiate_raw_codec_always_included():
    client = ClientCapabilities(protocol_version="1.0", renderer="vispy", supported_codecs=["raw"])
    server = negotiate(client)
    assert "raw" in server.supported_codecs


def test_negotiate_unknown_codec_excluded():
    client = ClientCapabilities(
        protocol_version="1.0", renderer="vispy",
        supported_codecs=["raw", "lz4"],   # lz4 not supported server-side
    )
    server = negotiate(client)
    assert "lz4" not in server.supported_codecs
    assert "raw" in server.supported_codecs


def test_negotiate_unknown_feature_excluded():
    client = ClientCapabilities(
        protocol_version="1.0", renderer="vispy",
        features=["zstd_buffers", "unknown_feature"],
    )
    server = negotiate(client)
    assert "unknown_feature" not in server.features
    assert "zstd_buffers" in server.features


def test_negotiate_no_shared_codecs_falls_back_to_raw():
    client = ClientCapabilities(
        protocol_version="1.0", renderer="vispy",
        supported_codecs=["lz4"],
    )
    server = negotiate(client)
    assert "raw" in server.supported_codecs
