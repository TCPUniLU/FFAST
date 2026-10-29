"""WarningRelay: server warnings go to every window as SERVER_WARNING."""

from __future__ import annotations

import logging

from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session import warning_relay
from ffast.session.warning_relay import WarningRelay


class _Loop:
    """Runs what is handed to it at once, as the event loop would later."""

    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


def _relay():
    sent = []
    relay = WarningRelay(_Loop(), sent.append)
    log = logging.getLogger("ffast.test_warning_relay")
    log.addHandler(relay)
    log.propagate = False
    return log, relay, sent


def _messages(sent):
    return [unpack(data) for data in sent]


def test_a_warning_goes_out_with_its_text_and_level():
    log, relay, sent = _relay()
    try:
        log.warning("atom-color: displacement unavailable — %s", "structures differ")
    finally:
        log.removeHandler(relay)
    [(event, _args, kwargs)] = _messages(sent)
    assert event == control.SERVER_WARNING
    assert kwargs == {"message": "atom-color: displacement unavailable — structures differ",
                      "level": "warning"}


def test_info_stays_in_the_log():
    log, relay, sent = _relay()
    try:
        log.info("loaded")
    finally:
        log.removeHandler(relay)
    assert sent == []


def test_the_same_text_goes_out_once_a_minute(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(warning_relay.time, "monotonic", lambda: now[0])
    log, relay, sent = _relay()
    try:
        log.warning("same")
        log.warning("same")
        log.warning("other")
        now[0] += warning_relay.REPEAT_AFTER_S
        log.warning("same")
    finally:
        log.removeHandler(relay)
    assert [kw["message"] for _e, _a, kw in _messages(sent)] == ["same", "other", "same"]


def test_connection_noise_stays_in_the_log():
    sent = []
    relay = WarningRelay(_Loop(), sent.append)
    for name in ("websockets.server", "asyncio"):
        relay.handle(logging.LogRecord(name, logging.WARNING, "", 0, "noise", (), None))
    assert sent == []


def test_a_closed_loop_drops_the_warning_quietly():
    class _Closed:
        def call_soon_threadsafe(self, fn, *args):
            raise RuntimeError("Event loop is closed")

    relay = WarningRelay(_Closed(), lambda data: None)
    relay.handle(logging.LogRecord("FFAST", logging.WARNING, "", 0, "late", (), None))


def test_an_error_the_asking_window_shows_itself_stays_in_the_log():
    # A failed metric: its panel says why (ADR 0066), not every window.
    log, relay, sent = _relay()
    try:
        log.error("Could not compute X: bad", extra={"popup": False})
    finally:
        log.removeHandler(relay)
    assert sent == []
