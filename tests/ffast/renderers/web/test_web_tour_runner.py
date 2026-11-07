"""The guided-tour runner on a plain test page (ADR 0088).

The runner knows nothing about FFAST, so it is tested without a server: a blank
page with three buttons far apart, and small tours made of those buttons.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import async_playwright, expect  # noqa: E402

pytestmark = pytest.mark.integration

STATIC_DIR = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"

_PAGE = """<!doctype html><title>tour</title>
<style>
  body { margin: 0; width: 100vw; height: 100vh; }
  #a, #b, #c { position: absolute; width: 90px; height: 32px; }
  #a { left: 40px; top: 40px; }
  #b { left: 420px; top: 300px; }
  #c { left: 760px; top: 560px; }
</style>
<button id="a">A</button><button id="b">B</button><button id="c">C</button>"""

# Click counters on the buttons, the runner on `window.tr`, and a helper that
# starts a tour whose steps are given as JS and records how it ended.
_SETUP = """async (origin) => {
  window.clicks = { a: 0, b: 0, c: 0 };
  for (const id of ['a', 'b', 'c']) {
    document.getElementById(id).addEventListener('click', () => { window.clicks[id] += 1; });
  }
  window.tr = await import(origin + '/tour_runner.js');
  window.events = new EventTarget();
  window.ended = null;
  window.run = (steps) => window.tr.startTour(
    { id: 't', title: 'Test', about: '', steps },
    { events: window.events, onEnd: (how, message) => { window.ended = { how, message }; } });
}"""


@pytest.fixture(scope="module")
def static_origin():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
async def page(static_origin):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        pg = await browser.new_page(viewport={"width": 1000, "height": 700})
        try:
            await pg.goto(f"{static_origin}/events.js")
            await pg.set_content(_PAGE)
            await pg.evaluate(_SETUP, static_origin)
            yield pg
        finally:
            await browser.close()


async def _centre(page, selector):
    box = await page.locator(selector).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def test_the_lit_target_takes_clicks_and_everything_else_does_not(page):
    await page.evaluate("""() => window.run([
      { title: 'One', body: 'Lit A', targets: ['#a'] },
      { title: 'Two', body: 'Lit B', targets: ['#b'] }])""")
    await expect(page.locator(".tour-title")).to_have_text("One")
    await page.mouse.click(*await _centre(page, "#c"))
    await page.mouse.click(*await _centre(page, "#b"))
    assert await page.evaluate("() => window.clicks") == {"a": 0, "b": 0, "c": 0}
    await page.mouse.click(*await _centre(page, "#a"))
    assert await page.evaluate("() => window.clicks.a") == 1


async def test_a_do_this_step_waits_for_its_event_and_then_moves_on(page):
    await page.evaluate("""() => window.run([
      { title: 'One', body: '', targets: ['#a'], until: 'go', wait: 'Click A' },
      { title: 'Two', body: '', targets: ['#b'] }])""")
    await expect(page.locator(".tour-next")).to_be_hidden()
    await expect(page.locator(".tour-wait")).to_have_text("Click A")
    await page.evaluate("() => window.events.dispatchEvent(new CustomEvent('go'))")
    await expect(page.locator(".tour-title")).to_have_text("Two")


async def test_back_runs_the_previous_steps_undo_and_that_step_waits_again(page):
    await page.evaluate("""() => { window.undone = 0; window.run([
      { title: 'One', body: '', targets: ['#a'], until: 'go', wait: 'Click A',
        undo: () => { window.undone += 1; } },
      { title: 'Two', body: '', targets: ['#b'] }]); }""")
    await page.evaluate("() => window.events.dispatchEvent(new CustomEvent('go'))")
    await expect(page.locator(".tour-title")).to_have_text("Two")
    await page.locator(".tour-back").click()
    await expect(page.locator(".tour-title")).to_have_text("One")
    assert await page.evaluate("() => window.undone") == 1
    await expect(page.locator(".tour-next")).to_be_hidden()
    await expect(page.locator(".tour-wait")).to_have_text("Click A")


async def test_an_undo_that_takes_time_shows_undoing_until_it_settles(page):
    await page.evaluate("""() => { window.run([
      { title: 'One', body: '', targets: ['#a'],
        undo: () => new Promise((resolve) => { window.finishUndo = resolve; }) },
      { title: 'Two', body: '', targets: ['#b'] }]); }""")
    await page.locator(".tour-next").click()
    await expect(page.locator(".tour-title")).to_have_text("Two")
    await page.locator(".tour-back").click()
    await expect(page.locator(".tour-wait")).to_have_text("Undoing…")
    await expect(page.locator(".tour-back")).to_be_hidden()
    await expect(page.locator(".tour-next")).to_be_hidden()
    await page.evaluate("() => window.finishUndo()")
    await expect(page.locator(".tour-title")).to_have_text("One")
    await expect(page.locator(".tour-next")).to_be_visible()


async def test_back_to_a_do_this_step_already_done_without_undo_shows_next(page):
    await page.evaluate("""() => window.run([
      { title: 'One', body: '', targets: ['#a'] },
      { title: 'Two', body: '', targets: ['#b'], until: 'go', wait: 'Click B' },
      { title: 'Three', body: '', targets: ['#c'] }])""")
    await page.locator(".tour-next").click()
    await page.evaluate("() => window.events.dispatchEvent(new CustomEvent('go'))")
    await expect(page.locator(".tour-title")).to_have_text("Three")
    await page.locator(".tour-back").click()
    await expect(page.locator(".tour-title")).to_have_text("Two")
    await expect(page.locator(".tour-next")).to_be_visible()
    await expect(page.locator(".tour-wait")).to_be_hidden()


async def test_back_is_hidden_on_the_first_step_and_the_last_step_says_done(page):
    await page.evaluate("""() => window.run([
      { title: 'One', body: '', targets: ['#a'] },
      { title: 'Two', body: '', targets: ['#b'] }])""")
    await expect(page.locator(".tour-back")).to_be_hidden()
    await expect(page.locator(".tour-next")).to_have_text("Next")
    await page.locator(".tour-next").click()
    await expect(page.locator(".tour-back")).to_be_visible()
    await expect(page.locator(".tour-next")).to_have_text("Done")
    await page.locator(".tour-next").click()
    assert await page.evaluate("() => window.ended.how") == "done"


async def test_escape_skips_the_tour_and_removes_the_layer(page):
    await page.evaluate("""() => window.run([{ title: 'One', body: '', targets: ['#a'] }])""")
    await expect(page.locator(".tour-box")).to_be_visible()
    await page.keyboard.press("Escape")
    assert await page.evaluate("() => window.ended.how") == "skip"
    assert await page.locator(".tour-box, .tour-shade, .tour-ring").count() == 0


async def test_a_target_that_goes_away_stops_the_tour_with_the_step_title(page):
    await page.evaluate("""() => window.run([{ title: 'Press A', body: '', targets: ['#a'] }])""")
    await expect(page.locator(".tour-box")).to_be_visible()
    await page.evaluate("() => document.getElementById('a').remove()")
    await page.wait_for_function("() => window.ended !== null")
    ended = await page.evaluate("() => window.ended")
    assert ended["how"] == "stopped"
    assert "Press A" in ended["message"]
    assert await page.locator(".tour-box").count() == 0


async def test_a_disabled_target_on_a_do_this_step_stops_with_its_reason(page):
    await page.evaluate("""() => {
      const a = document.getElementById('a');
      a.disabled = true; a.title = 'Load a prediction first';
      window.run([{ title: 'One', body: '', targets: ['#a'], until: 'go', wait: 'Click A' }]);
    }""")
    await page.wait_for_function("() => window.ended !== null")
    assert await page.evaluate("() => window.ended") == {
        "how": "stopped", "message": "Load a prediction first"}


async def test_a_skipped_step_is_left_out_of_the_count(page):
    await page.evaluate("""() => window.run([
      { title: 'One', body: '', targets: ['#a'], skip: () => true },
      { title: 'Two', body: '', targets: ['#b'] },
      { title: 'Three', body: '', targets: ['#c'] }])""")
    await expect(page.locator(".tour-count")).to_have_text("Step 1 of 2")
    await expect(page.locator(".tour-title")).to_have_text("Two")
    assert await page.locator(".tour-dots > *").count() == 2


async def test_a_second_tour_does_not_start_while_one_runs(page):
    second = await page.evaluate("""() => {
      window.run([{ title: 'One', body: '', targets: ['#a'] }]);
      return window.run([{ title: 'Other', body: '', targets: ['#b'] }]);
    }""")
    assert second is None
    await expect(page.locator(".tour-title")).to_have_text("One")
    assert await page.locator(".tour-box").count() == 1


async def test_a_step_that_stays_shows_next_after_its_event_and_waits_for_it(page):
    await page.evaluate("""() => window.run([
      { title: 'One', body: '', targets: ['#a'], until: 'go', wait: 'Click A', stay: true },
      { title: 'Two', body: '', targets: ['#b'] }])""")
    await page.locator("#a").focus()
    await expect(page.locator(".tour-next")).to_be_hidden()
    for _ in range(2):   # the action can be repeated; the step stays
        await page.evaluate("() => window.events.dispatchEvent(new CustomEvent('go'))")
        await expect(page.locator(".tour-next")).to_be_visible()
        await expect(page.locator(".tour-wait")).to_be_hidden()
        await expect(page.locator(".tour-title")).to_have_text("One")
    await expect(page.locator("#a")).to_be_focused()   # focus stays on the control in use
    await page.locator(".tour-next").click()
    await expect(page.locator(".tour-title")).to_have_text("Two")


async def test_accept_ignores_an_event_whose_detail_does_not_match(page):
    await page.evaluate("""() => window.run([
      { title: 'One', body: '', targets: ['#a'], until: 'tab', wait: 'Open B',
        accept: (d) => d.name === 'B' },
      { title: 'Two', body: '', targets: ['#b'] }])""")
    await page.evaluate("() => window.events.dispatchEvent(new CustomEvent('tab', { detail: { name: 'A' } }))")
    await expect(page.locator(".tour-title")).to_have_text("One")
    await expect(page.locator(".tour-wait")).to_have_text("Open B")
    await page.evaluate("() => window.events.dispatchEvent(new CustomEvent('tab', { detail: { name: 'B' } }))")
    await expect(page.locator(".tour-title")).to_have_text("Two")


async def test_a_body_function_is_read_each_time_the_step_shows(page):
    await page.evaluate("""() => { window.n = 0; window.run([
      { title: 'One', body: () => `Shown ${++window.n}`, targets: ['#a'] },
      { title: 'Two', body: 'Lit B', targets: ['#b'] }]); }""")
    await expect(page.locator(".tour-body")).to_have_text("Shown 1")
    await page.locator(".tour-next").click()
    await expect(page.locator(".tour-title")).to_have_text("Two")
    await page.locator(".tour-back").click()
    await expect(page.locator(".tour-body")).to_have_text("Shown 2")


async def test_a_refused_event_keeps_the_step_waiting_and_says_why(page):
    await page.evaluate("""() => window.run([
      { title: 'One', body: 'Do it', targets: ['#a'], until: 'go', wait: () => 'Send the right one',
        accept: (d) => d.ok, refused: (d) => `Not ${d.name}` },
      { title: 'Two', body: 'Lit B', targets: ['#b'] }])""")
    await expect(page.locator(".tour-wait")).to_have_text("Send the right one")
    await page.evaluate("() => window.events.dispatchEvent(new CustomEvent('go', { detail: { ok: false, name: 'x' } }))")
    await expect(page.locator(".tour-wait")).to_have_text("Not x")
    await expect(page.locator(".tour-title")).to_have_text("One")
    await page.evaluate("() => window.events.dispatchEvent(new CustomEvent('go', { detail: { ok: true } }))")
    await expect(page.locator(".tour-title")).to_have_text("Two")


@pytest.mark.parametrize("event, detail, message", [
    ("a-fail", "{ error: 'the server said no' }", "the server said no"),
    ("b-fail", "{ name: 'f.xyz' }", "f.xyz was refused"),
])
async def test_any_of_several_fail_events_stops_the_tour(page, event, detail, message):
    await page.evaluate("""() => window.run([
      { title: 'One', body: 'Do it', targets: ['#a'], until: 'go',
        failOn: ['a-fail', 'b-fail'], failText: (d) => `${d.name} was refused` }])""")
    await page.evaluate(f"() => window.events.dispatchEvent(new CustomEvent('{event}', {{ detail: {detail} }}))")
    await page.wait_for_function("() => window.ended !== null")
    assert await page.evaluate("() => window.ended") == {"how": "stopped", "message": message}


async def test_without_room_beside_the_lit_area_the_box_covers_as_little_of_it_as_it_can(page):
    """A lit area as big as the file dialog: the box must not sit on its top-left corner."""
    await page.evaluate("""() => {
      const big = document.createElement('div');
      big.id = 'big';
      Object.assign(big.style, { position: 'absolute', left: '100px', top: '100px', width: '800px', height: '500px' });
      document.body.append(big);
      window.run([{ title: 'Big', body: 'Lit big', targets: ['#big'], side: 'right' }]);
    }""")
    await expect(page.locator(".tour-title")).to_have_text("Big")
    hit = await page.evaluate("() => document.elementFromPoint(116, 116)?.id")
    assert hit == "big"
    box = await page.locator(".tour-box").bounding_box()
    covered = (max(0, min(box["x"] + box["width"], 906) - max(box["x"], 94))
               * max(0, min(box["y"] + box["height"], 606) - max(box["y"], 94)))
    assert covered < box["width"] * box["height"] / 2
