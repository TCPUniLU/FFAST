"""Evidence from a browser test that fails.

A failing browser test kept only its assertion and the server log, so a
failure that did not come back on a rerun (a race under load) left nothing
more to go on. Every page a test opens now records its console messages and
page errors; when the test closes its browser while failing (the usual
``try: … finally: await browser.close()``), each page still open is
screenshotted first. Both land in a pytest temp folder whose path, with the
last console lines, is printed into the failure report.
"""
from __future__ import annotations

import re
import sys
from types import SimpleNamespace

import pytest
from playwright.async_api import Browser


@pytest.fixture(autouse=True)
def browser_evidence(request, tmp_path_factory, monkeypatch):
    evidence = SimpleNamespace(kept=[])   # the folders written, for the self-test
    pages = []                            # (page, console lines)
    new_page, close = Browser.new_page, Browser.close

    def watch(page):
        lines = []
        page.on("console", lambda msg: lines.append(f"{msg.type}: {msg.text}"))
        page.on("pageerror", lambda exc: lines.append(f"pageerror: {exc}"))
        page.on("popup", watch)
        pages.append((page, lines))

    async def watched_new_page(self, *args, **kwargs):
        page = await new_page(self, *args, **kwargs)
        watch(page)
        return page

    async def keep(browser):
        name = re.sub(r"[^A-Za-z0-9_]+", "_", request.node.name)[:60]
        folder = tmp_path_factory.mktemp(f"evidence_{name}")
        evidence.kept.append(folder)
        for i, (page, lines) in enumerate(pages):
            if page.is_closed() or page.context.browser is not browser:
                continue
            try:
                await page.screenshot(path=folder / f"page{i}.png")
            except Exception as exc:   # a page that cannot draw still has its log
                lines.append(f"(no screenshot: {exc})")
            (folder / f"page{i}-console.txt").write_text("\n".join(lines) + "\n")
            print(f"[browser evidence] page {i} {page.url} -> {folder}")
            print("\n".join(lines[-40:]) or "(no console messages)")

    async def close_keeping_evidence(self, *args, **kwargs):
        if sys.exc_info()[0] is not None:   # closing on the way out of a failure
            try:
                await keep(self)
            except Exception as exc:
                print(f"[browser evidence] not kept: {exc}")
        return await close(self, *args, **kwargs)

    monkeypatch.setattr(Browser, "new_page", watched_new_page)
    monkeypatch.setattr(Browser, "close", close_keeping_evidence)
    return evidence
