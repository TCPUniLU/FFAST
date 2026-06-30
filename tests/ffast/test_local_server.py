"""Tests for ffast.session.local.LocalServerProcess and LocalServerManager."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ffast.session.local import LocalServerManager, LocalServerProcess
from ffast.session.token import SessionToken


@pytest.fixture
def token():
    return SessionToken.generate()


class TestLocalServerProcess:
    def test_fields(self, token):
        proc = MagicMock()
        handle = LocalServerProcess(port=9999, token_plaintext=token.plaintext, process=proc)
        assert handle.port == 9999
        assert handle.token_plaintext == token.plaintext
        assert handle.process is proc


class TestLocalServerManager:
    def test_start_launches_ffast_server(self, token):
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            manager = LocalServerManager()
            handle = manager.start(port=8800, token=token)

        cmd = mock_popen.call_args[0][0]
        assert any("ffast-server" in c for c in cmd)
        assert "--port" in cmd and "8800" in cmd
        assert "--token-hash" in cmd and token.hash in cmd
        assert "--recovery-window" in cmd
        assert "--snapshot-interval" in cmd

    def test_start_returns_handle_with_token_plaintext(self, token):
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            handle = LocalServerManager().start(port=8800, token=token)

        assert handle.token_plaintext == token.plaintext
        assert handle.port == 8800

    def test_start_passes_recovery_window(self, token):
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            LocalServerManager().start(port=8800, token=token, recovery_window=60)

        cmd = mock_popen.call_args[0][0]
        idx = cmd.index("--recovery-window")
        assert cmd[idx + 1] == "60"

    def test_stop_terminates_process(self, token):
        proc = MagicMock()
        handle = LocalServerProcess(port=8800, token_plaintext=token.plaintext, process=proc)
        LocalServerManager().stop(handle)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()

    def test_stop_kills_if_terminate_fails(self, token):
        proc = MagicMock()
        proc.terminate.side_effect = Exception("boom")
        handle = LocalServerProcess(port=8800, token_plaintext=token.plaintext, process=proc)
        LocalServerManager().stop(handle)  # should not raise
        proc.kill.assert_called_once()
