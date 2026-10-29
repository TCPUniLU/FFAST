"""A failing browser test keeps the page's console and a screenshot (conftest)."""
import pytest
from playwright.async_api import async_playwright


async def test_a_failing_browser_test_keeps_its_console_and_a_screenshot(browser_evidence):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("<script>console.log('hello from the page')</script>")
        with pytest.raises(AssertionError):
            try:
                raise AssertionError("a check that failed")
            finally:
                await browser.close()
    [kept] = browser_evidence.kept
    assert (kept / "page0.png").stat().st_size > 0
    assert "hello from the page" in (kept / "page0-console.txt").read_text()


async def test_a_passing_browser_test_keeps_nothing(browser_evidence):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            await (await browser.new_page()).set_content("<p>fine</p>")
        finally:
            await browser.close()
    assert browser_evidence.kept == []
