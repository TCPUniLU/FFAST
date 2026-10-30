"""The launcher's timing log file (ADR 0072).

The page posts its timing lines to /timing; the launcher appends them to one
file per run. Only a page the launcher opened (it sends the launch id) may
write, as for /cluster/.
"""
import json
import socket
import urllib.error
import urllib.request

import pytest

from ffast.renderers.web.serve import start_static_server
from ffast.renderers.web.timing_log import KEEP, TimingLogFile


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_lines_go_to_one_file_made_on_the_first_write(tmp_path):
    log = TimingLogFile(tmp_path)
    assert log.path is None and not list(tmp_path.iterdir())
    assert log.write(["a", "b"]) and log.write(["c"])
    assert log.path.name.startswith("timing-")
    assert log.path.read_text() == "a\nb\nc\n"


def test_anything_but_a_list_of_strings_is_refused(tmp_path):
    log = TimingLogFile(tmp_path)
    assert not log.write("a")
    assert not log.write([1])
    assert not log.write(None)
    assert log.path is None


def test_only_the_newest_files_are_kept(tmp_path):
    for i in range(KEEP + 3):
        (tmp_path / f"timing-20260101-0000{i:02d}.log").write_text("old")
    log = TimingLogFile(tmp_path)
    log.write(["new"])
    files = sorted(tmp_path.glob("timing-*.log"))
    assert len(files) == KEEP and log.path in files


@pytest.fixture
def served(tmp_path):
    port = _free_port()
    httpd = start_static_server(port, host="127.0.0.1", cluster_secret="launch-1")
    httpd.timing_log = TimingLogFile(tmp_path)
    yield f"http://127.0.0.1:{port}/timing", httpd.timing_log
    httpd.shutdown()


def _post(url, body, launch=None):
    headers = {"Content-Type": "application/json"}
    if launch:
        headers["X-FFAST-Launch"] = launch
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_the_page_the_launcher_opened_may_write(served):
    url, log = served
    assert _post(url, {"lines": ["click: 120 ms"]}, launch="launch-1") == 200
    assert log.path.read_text() == "click: 120 ms\n"


def test_another_page_may_not(served):
    url, log = served
    assert _post(url, {"lines": ["x"]}) == 403
    assert _post(url, {"lines": ["x"]}, launch="wrong") == 403
    assert log.path is None


def test_a_bad_body_is_refused(served):
    url, _ = served
    assert _post(url, {"lines": "x"}, launch="launch-1") == 400


def test_a_server_not_started_by_the_launcher_keeps_no_log():
    port = _free_port()
    httpd = start_static_server(port, host="127.0.0.1")
    try:
        assert _post(f"http://127.0.0.1:{port}/timing", {"lines": ["x"]}, launch="x") == 404
    finally:
        httpd.shutdown()
