"""The trace file (ffast-server --trace).

Off by default: nothing is written. On, each command a window sends, how its
handler ended (time or error with traceback), and each line the page reports
land in one file, in order, with large payloads shortened. Run against a real
ServerSession with a fake environment and a queue — no socket.
"""
import asyncio

import numpy as np
import pytest

from ffast.protocol import control
from ffast.session import trace
from ffast.session.server_session import ServerSession


@pytest.fixture
def trace_file(tmp_path):
    path = trace.start(tmp_path)
    yield path
    trace.stop()


def _run(coro):
    # Not asyncio.run: that leaves no current event loop, and a later test in
    # this worker (TaskManager) still asks for one.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _session():
    s = ServerSession(env=None, outbound=asyncio.Queue())
    s.peer = "127.0.0.1:5000"
    return s


def test_off_by_default_writes_nothing(tmp_path):
    assert not trace.enabled()
    trace.record("ui", "click")  # no file, no error


def test_dispatch_records_the_command_and_its_time(trace_file):
    s = _session()

    async def handler(name):
        pass

    s._handlers[control.DELETE_TAB] = s._handlers[control.DELETE_TAB].__class__(handler, ["name"], None)
    _run(s.dispatch(control.DELETE_TAB, [], {"name": "Compare"}))

    lines = trace_file.read_text().splitlines()
    assert "in     DELETE_TAB {name: 'Compare'}  [127.0.0.1:5000]" in lines[0]
    assert "done   DELETE_TAB" in lines[1] and lines[1].endswith(" ms")


def test_a_failing_handler_is_recorded_with_its_traceback(trace_file):
    s = _session()

    async def handler(name):
        raise KeyError("no such tab")

    s._handlers[control.DELETE_TAB] = s._handlers[control.DELETE_TAB].__class__(handler, ["name"], None)
    with pytest.raises(KeyError):
        _run(s.dispatch(control.DELETE_TAB, [], {"name": "Compare"}))

    text = trace_file.read_text()
    assert "error  DELETE_TAB failed: KeyError('no such tab')" in text
    assert "| Traceback" in text and "in handler" in text


def test_a_line_from_the_page_is_written_as_sent(trace_file):
    _run(_session().dispatch(control.TRACE, [], {"kind": "ui", "text": 'click button "Duplicate"'}))
    lines = trace_file.read_text().splitlines()
    assert len(lines) == 1
    assert 'ui     click button "Duplicate"  [127.0.0.1:5000]' in lines[0]


def test_payloads_are_shortened():
    wire_array = {"__ndarray__": True, "dtype": "float32", "shape": [5000, 3], "data": b"x" * 60000}
    assert trace.summarize(wire_array) == "<array 5000×3 float32>"
    assert trace.summarize(np.zeros((4, 2))) == "<array 4×2 float64>"
    assert trace.summarize(list(range(500))) == "<500 numbers>"
    assert trace.summarize(b"abc") == "<3 bytes>"
    assert trace.summarize("x" * 500).endswith("…'")
    assert "…2 more" in trace.summarize({str(i): i for i in range(10)})


def test_only_the_last_ten_files_are_kept(tmp_path):
    for i in range(12):
        (tmp_path / f"trace-20260101-0000{i:02d}.log").write_text("")
    trace.start(tmp_path)
    trace.stop()
    assert len(list(tmp_path.glob("trace-*.log"))) == trace.KEEP
