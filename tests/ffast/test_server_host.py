"""ffast-server --host: the bind host flows from the CLI to _main (ADR 0045).

The web launcher passes --host 127.0.0.1 to keep the RPC loopback-only; the
default stays all-interfaces so the cluster/remote path is unchanged. Binding
itself is exercised by the launcher live-smoke; here we pin the wiring.
"""

from __future__ import annotations

import asyncio
import sys

import server


def _run_cli_capturing_main(monkeypatch, argv):
    captured = {}

    def fake_main(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(server, "_main", fake_main)
    monkeypatch.setattr(server.asyncio, "run", lambda coro: coro.close())
    monkeypatch.setattr(sys, "argv", argv)
    server.cli()
    return captured


def test_cli_forwards_host_to_main(monkeypatch):
    captured = _run_cli_capturing_main(
        monkeypatch, ["ffast-server", "--host", "127.0.0.1", "--port", "8765"]
    )
    assert captured["kwargs"]["host"] == "127.0.0.1"


def test_cli_host_defaults_to_all_interfaces(monkeypatch):
    captured = _run_cli_capturing_main(monkeypatch, ["ffast-server", "--port", "8765"])
    assert captured["kwargs"]["host"] == "0.0.0.0"
