"""The web launcher's cluster broker (web parity plan 2026-10-29). No real
SSH: every SSH/SLURM step is a fake."""
from unittest import mock

from cluster.connection import ServerConnection
from ffast.protocol.rpc import unpack


def _conn_with_fakes():
    ws = mock.MagicMock()
    ws.send = mock.AsyncMock()
    ws.close = mock.AsyncMock()
    proc = mock.MagicMock()
    conn = ServerConnection(job_id="J1", ssh_proc=proc, websocket=ws, profile=None,
                            local_port=1, remote_port=2)
    return conn, ws, proc


async def test_close_websocket_keeps_the_tunnel():
    conn, ws, proc = _conn_with_fakes()
    await conn.close_websocket()
    assert ws.close.await_count == 1
    assert proc.terminate.call_count == 0
    assert ws.send.await_count == 1
    assert unpack(ws.send.await_args.args[0])[0] == "GRACEFUL_DISCONNECT"


async def test_disconnect_still_closes_both():
    conn, ws, proc = _conn_with_fakes()
    await conn.disconnect()
    assert ws.close.await_count == 1
    assert proc.terminate.call_count == 1
