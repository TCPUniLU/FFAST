"""A dropped connection is noticed and retried (ADR 0097 decision 3), against a
real server. The page's socket runs through Playwright's WebSocket routing, so
the test can close it the way a dying network would: from outside the page."""

import re

import pytest
from playwright.async_api import async_playwright, expect

from .test_web_runtime import (
    _expect_connected,
    _open_view,
    _preload_dataset,
    _shared_servers,  # noqa: F401
    shared_dataset_server,
    shared_server,  # noqa: F401
)

pytestmark = pytest.mark.integration


async def _route_sockets(page):
    """Pass every page socket through to the server; returns the open routes."""
    routes = []

    def handler(ws):
        ws.connect_to_server()
        routes.append(ws)

    await page.route_web_socket(re.compile(r"^ws://"), handler)
    return routes


async def test_web_a_dropped_connection_shows_a_banner_and_reconnects(
    shared_dataset_server,
):
    ws_port, web_port = shared_dataset_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            routes = await _route_sockets(page)
            await _open_view(page, ws_port, web_port, dataset_fp)
            banner = page.locator("#conn-lost")
            await expect(banner).to_be_hidden()

            await routes[-1].close(code=1001, reason="gone")
            await expect(banner).to_be_visible()
            await expect(page.locator("#conn-lost-text")).to_contain_text(
                "Connection lost"
            )
            await expect(page.locator("#disconnect-btn")).to_be_disabled()

            # The first retry comes after a second, by itself.
            await _expect_connected(page)
            await expect(banner).to_be_hidden()
            assert len(routes) == 2
            # The replay brings the dataset back, and the view it showed.
            row = page.locator(
                f"#dataset-list .obj-row[data-fp='{dataset_fp}']"
            )
            await expect(row).to_have_count(1)
            await expect(page.locator("#overlay")).to_have_class(
                re.compile(r"\bhidden\b")
            )
        finally:
            await browser.close()


async def test_web_reconnect_now_does_not_wait(shared_dataset_server):
    ws_port, web_port = shared_dataset_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            routes = await _route_sockets(page)
            await _open_view(page, ws_port, web_port, dataset_fp)
            # Hold the retries: a long first wait, so only the button reconnects.
            await page.evaluate(
                "() => { window.ffastApp._reconnector._setTimer = () => 0; }"
            )
            await routes[-1].close(code=1001, reason="gone")
            await expect(page.locator("#conn-lost")).to_be_visible()
            await page.locator("#conn-lost-now").click()
            await _expect_connected(page)
            await expect(page.locator("#conn-lost")).to_be_hidden()
        finally:
            await browser.close()


async def test_web_disconnecting_on_purpose_shows_no_banner(
    shared_dataset_server,
):
    ws_port, web_port = shared_dataset_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await _open_view(page, ws_port, web_port, dataset_fp)
            await page.evaluate("() => window.ffastApp._disconnect()")
            await expect(page.locator("#status")).to_have_text("Disconnected")
            await expect(page.locator("#conn-lost")).to_be_hidden()
            # Nothing is scheduled to retry.
            assert (
                await page.evaluate(
                    "() => window.ffastApp._reconnector.active"
                )
                is False
            )
        finally:
            await browser.close()


@pytest.mark.parametrize("launcher", ["replaced", "unavailable"])
async def test_an_old_local_tab_does_not_retry_websockets_when_its_launcher_is_gone(
    shared_server, launcher
):
    ws_port, web_port = shared_server
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        state = {"gone": False}

        async def broker(route):
            if not state["gone"]:
                await route.fulfill(json={"status": {"state": "idle"}})
            elif launcher == "replaced":
                await route.fulfill(status=403, json={"error": "Not allowed"})
            else:
                await route.abort("connectionrefused")

        await page.route("**/cluster/state", broker)
        routes = await _route_sockets(page)
        try:
            await page.goto(
                f"http://127.0.0.1:{web_port}/?port={ws_port}&launch=old"
            )
            await _expect_connected(page)
            # Drive the real retry with its button, without a timer race.
            await page.evaluate(
                "() => { window.ffastApp._reconnector._setTimer = () => 0; }"
            )
            state["gone"] = True
            await routes[-1].close(code=1001, reason="launcher exited")
            await expect(page.locator("#conn-lost")).to_be_visible()
            await page.locator("#conn-lost-now").click()
            text = (
                "latest link"
                if launcher == "replaced"
                else "Waiting for the local FFAST launcher"
            )
            await expect(page.locator("#conn-lost-text")).to_contain_text(text)
            assert len(routes) == 1
            active = await page.evaluate(
                "() => window.ffastApp._reconnector.active"
            )
            assert active is (launcher == "unavailable")
            if launcher == "unavailable":
                state["gone"] = False
                await page.locator("#conn-lost-now").click()
                await _expect_connected(page)
                assert len(routes) == 2
        finally:
            await browser.close()


async def test_an_expired_launch_link_does_not_open_a_websocket(shared_server):
    ws_port, web_port = shared_server
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route(
            "**/cluster/state",
            lambda route: route.fulfill(
                status=403, json={"error": "Not allowed"}
            ),
        )
        routes = await _route_sockets(page)
        try:
            await page.goto(
                f"http://127.0.0.1:{web_port}/?port={ws_port}&launch=old"
            )
            await expect(page.locator("#conn-message")).to_contain_text(
                "latest link"
            )
            assert not routes
        finally:
            await browser.close()


async def test_the_server_answers_a_heartbeat_with_its_number(shared_server):
    import asyncio

    import msgpack

    from .test_web_runtime import _connect_headless_client

    ws_port, _ = shared_server
    ws = await _connect_headless_client(ws_port)
    try:
        await ws.send(
            msgpack.packb(
                {"event": "HEARTBEAT", "args": [], "kwargs": {}, "id": 42}
            )
        )

        async def _beat():
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    decoded = msgpack.unpackb(msg, raw=False)
                    if decoded.get("reply_to") == 42:
                        return decoded

        beat = await asyncio.wait_for(_beat(), timeout=10)
    finally:
        await ws.close()
    assert beat["event"] == "HEARTBEAT"
    assert beat["final"] is True and "error" not in beat


def test_the_server_offers_the_heartbeat_to_a_page_that_asks():
    from ffast.visualization.protocol import ClientCapabilities, negotiate

    asked = negotiate(
        ClientCapabilities(
            protocol_version="1.0", renderer="webgl", features=["heartbeat"]
        )
    )
    not_asked = negotiate(
        ClientCapabilities(
            protocol_version="1.0", renderer="webgl", features=[]
        )
    )
    assert "heartbeat" in asked.features
    assert "heartbeat" not in not_asked.features
