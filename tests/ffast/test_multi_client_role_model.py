"""Tests for the multi-client role model (ADR 0044 Phase 2).

Covers the three pieces that changed in ``server.py``: the HELLO handshake no
longer restricts CONTROLLING to a single global slot, ``_may_dispatch`` gates
only mutating Control messages for a READ_ONLY connection, and the recovery
window arms on the hub going empty (last client) rather than on a CONTROLLING
role being released.
"""
from __future__ import annotations

import asyncio

import server
from ffast.protocol import control
from ffast.protocol.rpc import pack
from ffast.session.hub import ConnectionHub
from ffast.session.registry import ConnectionRegistry
from ffast.session.token import ClientRole, SessionToken


class _FakeWebSocket:
    """Enough of a websockets.ClientConnection to drive ``_do_hello_handshake``."""

    def __init__(self, messages):
        self._to_recv = list(messages)
        self.sent = []
        self.remote_address = ("127.0.0.1", 1234)

    async def recv(self):
        return self._to_recv.pop(0)

    async def send(self, data):
        self.sent.append(data)


def _hello_messages(**hello_kwargs):
    hello_kwargs.setdefault("protocol_version", "1.0")
    hello_kwargs.setdefault("renderer", "webgl")
    return ["ping", pack(control.HELLO, [], hello_kwargs)]


def _run(coro):
    return asyncio.run(coro)


# ── handshake: every valid-token connection becomes CONTROLLING ─────────────

class TestHandshakeRoleModel:
    def test_two_connections_both_get_controlling_no_token_hash(self):
        registry = ConnectionRegistry()
        ws_a = _FakeWebSocket(_hello_messages())
        ws_b = _FakeWebSocket(_hello_messages())

        role_a = _run(server._do_hello_handshake(ws_a, ws_a.remote_address, registry, ""))
        role_b = _run(server._do_hello_handshake(ws_b, ws_b.remote_address, registry, ""))

        assert role_a == ClientRole.CONTROLLING
        assert role_b == ClientRole.CONTROLLING

    def test_second_connection_with_valid_token_also_gets_controlling(self):
        token = SessionToken.generate()
        registry = ConnectionRegistry()
        ws_a = _FakeWebSocket(_hello_messages(session_token=token.plaintext))
        ws_b = _FakeWebSocket(_hello_messages(session_token=token.plaintext))

        role_a = _run(server._do_hello_handshake(ws_a, ws_a.remote_address, registry, token.hash))
        role_b = _run(server._do_hello_handshake(ws_b, ws_b.remote_address, registry, token.hash))

        assert role_a == ClientRole.CONTROLLING
        assert role_b == ClientRole.CONTROLLING

    def test_explicit_read_only_opt_in_wins_over_valid_token(self):
        registry = ConnectionRegistry()
        ws = _FakeWebSocket(_hello_messages(read_only=True))

        role = _run(server._do_hello_handshake(ws, ws.remote_address, registry, ""))

        assert role == ClientRole.READ_ONLY

    def test_invalid_token_gets_read_only(self):
        token = SessionToken.generate()
        registry = ConnectionRegistry()
        ws = _FakeWebSocket(_hello_messages(session_token="wrong"))

        role = _run(server._do_hello_handshake(ws, ws.remote_address, registry, token.hash))

        assert role == ClientRole.READ_ONLY


# ── _may_dispatch: mutating events gated for READ_ONLY, reads always pass ───

class TestMayDispatch:
    def test_controlling_may_dispatch_anything(self):
        assert server._may_dispatch(ClientRole.CONTROLLING, control.LOAD_DATASET)
        assert server._may_dispatch(ClientRole.CONTROLLING, control.OPEN_VIEW)

    def test_read_only_may_not_dispatch_mutating_events(self):
        for event in control.MUTATING_CLIENT_EVENTS:
            assert not server._may_dispatch(ClientRole.READ_ONLY, event), event

    def test_read_only_may_dispatch_read_events(self):
        for event in control.CLIENT_TO_SERVER - control.MUTATING_CLIENT_EVENTS:
            assert server._may_dispatch(ClientRole.READ_ONLY, event), event


# ── recovery window: arms on hub emptiness, not on role ─────────────────────

class TestRecoveryWindowLastClient:
    def test_shuts_down_when_hub_stays_empty(self):
        hub = ConnectionHub()
        quit_event = asyncio.Event()

        _run(server._recovery_window_task(hub, 0, quit_event))

        assert quit_event.is_set()

    def test_stays_alive_when_a_client_reconnected(self):
        hub = ConnectionHub()
        quit_event = asyncio.Event()
        hub.register(asyncio.Queue())  # a client reconnected during the window

        _run(server._recovery_window_task(hub, 0, quit_event))

        assert not quit_event.is_set()
