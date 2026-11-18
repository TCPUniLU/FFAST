"""The launcher's timing log file (ADR 0072).

The page posts its timing lines to /timing; the launcher appends them to one
file per run. Only a page the launcher opened (it sends the launch id) may
write, as for /cluster/.
"""

import json
import urllib.error
import urllib.request

import pytest

from ffast.renderers.web.serve import start_static_server
from ffast.renderers.web.timing_log import KEEP, TimingLogFile


from tests.free_ports import free_port as _free_port  # see tests/free_ports.py


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
    httpd = start_static_server(
        port, host="127.0.0.1", cluster_secret="launch-1"
    )
    httpd.timing_log = TimingLogFile(tmp_path)
    yield f"http://127.0.0.1:{port}/timing", httpd.timing_log
    httpd.shutdown()


def _post(url, body, launch=None):
    headers = {"Content-Type": "application/json"}
    if launch:
        headers["X-FFAST-Launch"] = launch
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
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


def test_startup_durations_reach_the_file_and_terminal(served, caplog):
    import logging

    url, log = served
    timing = {
        "page": "page-1",
        "phase": "socket",
        "browser": "Firefox",
        "elapsed_ms": 20123,
        "scripts_ms": 19500,
        "interface_ms": 200,
        "target_ms": 400,
        "secret": "must not be logged",
    }
    with caplog.at_level(logging.INFO):
        assert _post(url, {"startup": timing}, launch="launch-1") == 200
    record = json.loads(log.path.read_text())
    assert record["scripts_ms"] == 19500
    assert record["target_ms"] == 400
    assert record["event"] == "page_startup"
    assert "secret" not in record
    assert "page_startup" in caplog.text
    assert "must not be logged" not in caplog.text


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"elapsed_ms": -1},
        {"elapsed_ms": float("nan")},
        {"phase": "secret"},
        {"page": "bad\nline"},
    ],
)
def test_invalid_startup_reports_are_refused(served, bad):
    url, log = served
    timing = {
        "page": "page-1",
        "phase": "open",
        "browser": "Firefox",
        "elapsed_ms": 100,
    }
    value = {**timing, **bad} if isinstance(bad, dict) and bad else bad
    assert _post(url, {"startup": value}, launch="launch-1") == 400
    assert log.path is None


def test_startup_reports_need_the_launch_secret(served):
    url, log = served
    timing = {
        "page": "page-1",
        "phase": "open",
        "browser": "Firefox",
        "elapsed_ms": 100,
    }
    assert _post(url, {"startup": timing}) == 403
    assert log.path is None


@pytest.mark.integration
async def test_browser_reports_each_startup_stage_with_its_own_duration(
    served,
):
    from playwright.async_api import async_playwright

    url, log = served
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # Exercise the real telemetry script with a controlled clock;
            # no server or browser wall-clock synchronisation is needed.
            await page.route(
                "**/?*",
                lambda route: route.fulfill(
                    content_type="text/html",
                    body='<script src="startup.js"></script>',
                ),
            )
            await page.clock.install()
            await page.goto(url.removesuffix("/timing") + "/?launch=launch-1")
            for phase, elapsed in [
                ("modules", 200),
                ("interface", 50),
                ("target", 1000),
                ("socket", 0),
                ("open", 300),
            ]:
                await page.clock.run_for(elapsed)
                if phase == "open":
                    async with page.expect_response(
                        lambda r: r.url.endswith("/timing")
                        and r.request.post_data_json.get("startup", {}).get(
                            "phase"
                        )
                        == "open"
                    ):
                        await page.evaluate(
                            "phase => window.ffastStartup(phase)", phase
                        )
                else:
                    await page.evaluate(
                        "phase => window.ffastStartup(phase)", phase
                    )
            records = [
                json.loads(line) for line in log.path.read_text().splitlines()
            ]
            opened = next(r for r in records if r["phase"] == "open")
            for key, expected in [
                ("scripts_ms", 200),
                ("interface_ms", 50),
                ("target_ms", 1000),
                ("socket_ms", 300),
            ]:
                assert opened[key] == pytest.approx(expected, abs=10)
        finally:
            await browser.close()


def test_a_server_not_started_by_the_launcher_keeps_no_log():
    port = _free_port()
    httpd = start_static_server(port, host="127.0.0.1")
    try:
        assert (
            _post(
                f"http://127.0.0.1:{port}/timing", {"lines": ["x"]}, launch="x"
            )
            == 404
        )
    finally:
        httpd.shutdown()
