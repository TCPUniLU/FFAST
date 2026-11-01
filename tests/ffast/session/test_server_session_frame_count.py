"""The Load dialog's frame count and key probe on the server.

Plan: docs/implementation/2026-11-01-frame-index.md, part 1.

The server reads one message at a time (``server.py`` awaits each dispatch), so
a handler that counts for two minutes stops every other message, the key
probe's reply included. Now the count runs in the background: the handler
returns at once, partial counts stream to the page, and a newer count, or
CANCEL_PROBE_DATASET_LENGTH, stops an older one.
"""
import asyncio
import threading
import time
import types

import ase.io
import numpy as np
import pytest
from ase import Atoms

from ffast.io import frame_index as fi
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession


def _frames(n):
    rng = np.random.default_rng(2)
    out = []
    for i in range(n):
        a = Atoms("CH2", positions=rng.normal(size=(3, 3)))
        a.info["REF_energy"] = float(i)
        a.info["MACE_energy"] = float(i) + 0.1
        a.arrays["REF_forces"] = rng.normal(size=(3, 3))
        out.append(a)
    return out


@pytest.fixture(autouse=True)
def _own_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "frame-index")
    fi.clear_memory_cache()
    yield
    fi.clear_memory_cache()


def _write(tmp_path, name="d.extxyz", n=23):
    path = tmp_path / name
    ase.io.write(path, _frames(n), format="extxyz")
    return str(path)


def _session():
    return ServerSession(types.SimpleNamespace(), asyncio.Queue())


async def _collect(s, until, timeout=5.0):
    """Messages the session queues, until ``until(messages)`` or timeout."""
    out = []
    end = time.monotonic() + timeout
    while time.monotonic() < end and not until(out):
        try:
            data = await asyncio.wait_for(s.outbound.get(), 0.05)
        except asyncio.TimeoutError:
            continue
        out.append(unpack(data))
    return out


def _lengths(messages, path=None):
    return [kw for ev, args, kw in messages
            if ev == control.DATASET_LENGTH_RESPONSE and (path is None or args[0] == path)]


def _final(path):
    return lambda out: any(kw["done"] for kw in _lengths(out, path))


def _slow_scan(monkeypatch, seconds=1.0):
    """A scan that takes ``seconds`` and stops when asked; records stopped paths."""
    real = fi.scan
    stopped = []

    def slow(path, *, progress=None, should_stop=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if should_stop and should_stop():
                stopped.append(str(path))
                raise fi.FrameIndexCancelled(path)
            time.sleep(0.01)
        return real(path, progress=progress, should_stop=should_stop)

    monkeypatch.setattr(fi, "scan", slow)
    return stopped


# ── the wire ────────────────────────────────────────────────────────────────

def test_cancelling_the_count_is_a_client_message_any_window_may_send():
    assert control.CANCEL_PROBE_DATASET_LENGTH == "CANCEL_PROBE_DATASET_LENGTH"
    assert control.CANCEL_PROBE_DATASET_LENGTH in control.CLIENT_TO_SERVER
    assert control.CANCEL_PROBE_DATASET_LENGTH not in control.MUTATING_CLIENT_EVENTS


# ── the count ───────────────────────────────────────────────────────────────

def test_the_count_streams_partial_numbers_then_the_total(tmp_path, monkeypatch):
    path = _write(tmp_path)
    monkeypatch.setattr(fi, "PROGRESS_EVERY", 10)

    async def scenario():
        s = _session()
        await s.dispatch(control.PROBE_DATASET_LENGTH, [path], {})
        return await _collect(s, _final(path))

    replies = _lengths(asyncio.run(scenario()), path)
    assert replies == [
        {"n": 10, "error": None, "done": False},
        {"n": 20, "error": None, "done": False},
        {"n": 23, "error": None, "done": True},
    ]


def test_the_count_handler_returns_before_the_count_ends(tmp_path, monkeypatch):
    """So the next message (the key probe, a Load click) is read at once."""
    path = _write(tmp_path)
    _slow_scan(monkeypatch, seconds=1.0)

    async def scenario():
        s = _session()
        t = time.monotonic()
        await s.dispatch(control.PROBE_DATASET_LENGTH, [path], {})
        returned = time.monotonic() - t
        await _collect(s, _final(path))
        return returned

    assert asyncio.run(scenario()) < 0.3


def test_a_new_count_stops_the_one_before(tmp_path, monkeypatch):
    first, second = _write(tmp_path, "a.extxyz"), _write(tmp_path, "b.extxyz", n=5)
    stopped = _slow_scan(monkeypatch, seconds=1.0)

    async def scenario():
        s = _session()
        await s.dispatch(control.PROBE_DATASET_LENGTH, [first], {})
        await asyncio.sleep(0.1)
        await s.dispatch(control.PROBE_DATASET_LENGTH, [second], {})
        return await _collect(s, _final(second))

    out = asyncio.run(scenario())
    assert _lengths(out, second)[-1] == {"n": 5, "error": None, "done": True}
    assert _lengths(out, first) == []
    assert stopped == [first]


def test_cancel_stops_the_count_and_nothing_more_is_sent(tmp_path, monkeypatch):
    path = _write(tmp_path)
    stopped = _slow_scan(monkeypatch, seconds=1.0)

    async def scenario():
        s = _session()
        await s.dispatch(control.PROBE_DATASET_LENGTH, [path], {})
        await asyncio.sleep(0.1)
        await s.dispatch(control.CANCEL_PROBE_DATASET_LENGTH, [], {})
        return await _collect(s, lambda out: bool(stopped), timeout=2.0)

    out = asyncio.run(scenario())
    assert stopped == [path]
    assert _lengths(out, path) == []


def test_a_cancelled_count_does_not_stop_a_load_of_the_same_file(tmp_path, monkeypatch):
    """Load clicked while counting: the load waits on the same scan, so
    closing the dialog (which cancels the dialog's count) must not stop it."""
    path = _write(tmp_path)
    stopped = _slow_scan(monkeypatch, seconds=0.5)
    loaded = {}

    def load():
        loaded["n"] = len(fi.get_frame_index(path))

    async def scenario():
        s = _session()
        await s.dispatch(control.PROBE_DATASET_LENGTH, [path], {})
        await asyncio.sleep(0.1)
        t = threading.Thread(target=load)
        t.start()
        await asyncio.sleep(0.1)
        await s.dispatch(control.CANCEL_PROBE_DATASET_LENGTH, [], {})
        await asyncio.to_thread(t.join, 5)

    asyncio.run(scenario())
    assert loaded == {"n": 23}
    assert stopped == []


def test_a_broken_file_answers_with_the_line_that_breaks_it(tmp_path):
    path = tmp_path / "bad.xyz"
    path.write_bytes(b"3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\nx\n\nC 0 0 0\n")

    async def scenario():
        s = _session()
        await s.dispatch(control.PROBE_DATASET_LENGTH, [str(path)], {})
        return await _collect(s, _final(str(path)))

    (reply,) = _lengths(asyncio.run(scenario()), str(path))
    assert reply["done"] is True
    assert reply["n"] is None
    assert reply["error"].startswith("Frame 2 (line 6) is not a valid frame")


def test_other_formats_are_still_counted(tmp_path):
    path = tmp_path / "d.traj"
    ase.io.write(path, _frames(4))

    async def scenario():
        s = _session()
        await s.dispatch(control.PROBE_DATASET_LENGTH, [str(path)], {})
        return await _collect(s, _final(str(path)))

    assert _lengths(asyncio.run(scenario()), str(path))[-1] == {"n": 4, "error": None, "done": True}


# ── the key probe ───────────────────────────────────────────────────────────

def test_the_key_probe_reads_only_the_first_frame(tmp_path):
    """Garbage after frame 0: ASE's frame-0 read walks the whole file and
    fails on it; the probe must not, and must list frame 0's keys."""
    path = _write(tmp_path, n=1)
    with open(path, "ab") as f:
        f.write(b"not a frame\n")

    async def scenario():
        s = _session()
        await s.dispatch(control.PROBE_DATASET_KEYS, [path, "ase (auto)"], {})
        return await _collect(s, lambda out: bool(out))

    ((event, args, kw),) = asyncio.run(scenario())
    assert event == control.DATASET_KEYS_RESPONSE
    assert kw["error"] is None
    assert {"REF_energy", "MACE_energy"} <= set(kw["energy_keys"])
