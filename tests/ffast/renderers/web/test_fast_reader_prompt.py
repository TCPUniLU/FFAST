"""Reader setup UI and the real loader's preflight gate, without SSH or pip."""
import re

import pytest
from playwright.async_api import async_playwright, expect

from .test_web_tour_runner import STATIC_DIR, static_origin  # noqa: F401

pytestmark = pytest.mark.integration

STYLE = re.search(r'<style>(.*?)</style>', (STATIC_DIR / 'index.html').read_text(), re.S)[1]

SETUP = """async (origin) => {
  const { FastReaderPrompt } = await import(origin + '/fast_reader_prompt.js');
  const { RemoteBrowser } = await import(origin + '/remote_browser.js');
  window.calls = [];
  window.clusterName = 'Test cluster';
  window.cluster = true;
  window.connected = true;
  window.state = { ready: false, managed: true, missing: true, offer: true, busy: false };
  window.result = { ready: true };
  window.connection = { request: (event, kw, args, opts = {}) => {
    calls.push(kw.action);
    if (['install', 'wait'].includes(kw.action)) {
      return new Promise((resolve, reject) => {
        window.report = (line) => opts.onProgress?.({ kwargs: { line } });
        window.complete = () => resolve({ kwargs: result });
        window.drop = () => reject(Object.assign(new Error('connection lost'), { connectionLost: true }));
      });
    }
    return Promise.resolve({ kwargs: kw.action === 'skip' ? { ...state, offer: false } : state });
  } };
  window.prompt = new FastReaderPrompt({
    cluster: () => cluster ? { connection, name: clusterName } : null,
    current: (c) => connected && c === connection,
  });
  window.load = () => {
    window.answer = 'pending';
    Promise.resolve(prompt.beforeLoad('data.extxyz', 'dataset')).then((v) => { answer = v; });
  };
  window.loader = Object.create(RemoteBrowser.prototype);
  loader._ports = {
    beforeLoad: (path, kind) => prompt.beforeLoad(path, kind),
    cancelBeforeLoad: () => prompt.cancel(),
    request: (event) => { calls.push(event); return Promise.resolve({ kwargs: {} }); },
    setStatus: () => {},
  };
  loader._loadAnswered = () => { window.loaded = true; };
  loader._loadFailed = (_load, err) => { throw err; };
  window.startLoad = () => {
    window.loaded = false;
    loader._startLoad({ kind: 'dataset', name: 'data', path: 'data.extxyz' }, 'LOAD_DATASET', {}, []);
  };
}"""


@pytest.fixture
async def page(static_origin):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1000, "height": 700})
        try:
            await page.goto(static_origin + '/events.js')
            await page.set_content(f'<style>{STYLE}</style><button id="opener">Load dataset</button>')
            await page.evaluate(SETUP, static_origin)
            yield page
        finally:
            await browser.close()


async def test_install_finishes_before_the_real_load_starts(page):
    await page.locator('#opener').focus()
    await page.evaluate('startLoad()')
    dialog = page.get_by_role('dialog', name='Install the fast trajectory reader?')
    await expect(dialog).to_be_visible()
    await expect(page.get_by_role('button', name='Load with Python', exact=True)).to_be_focused()
    assert await page.evaluate('calls') == ['offer']
    await page.get_by_role('button', name='Install and load', exact=True).click()
    await page.evaluate("report('Downloading the matching wheel…')")
    await expect(page.get_by_role('status')).to_have_text('Downloading the matching wheel…')
    await expect(page.get_by_label('Don’t ask again for this cluster')).to_be_hidden()
    assert not await page.evaluate('loaded')
    await page.evaluate('complete()')
    await page.wait_for_function('loaded')
    assert await page.evaluate('calls') == ['offer', 'install', 'LOAD_DATASET']
    await expect(page.locator('#fast-reader-dialog')).to_have_count(0)
    await expect(page.locator('#opener')).to_be_focused()


async def test_decline_is_remembered_for_this_profile_and_other_profiles_still_prompt(page):
    await page.evaluate('load()')
    await page.get_by_label('Don’t ask again for this cluster').check()
    await page.get_by_role('button', name='Load with Python', exact=True).click()
    await page.wait_for_function('answer === true')
    await page.evaluate('load()')
    await page.wait_for_function('answer === true')
    assert await page.evaluate('calls') == ['offer', 'skip']
    await page.evaluate("clusterName = 'Other cluster'; load()")
    await expect(page.locator('#fast-reader-dialog')).to_be_visible()
    await page.keyboard.press('Escape')
    await page.wait_for_function('answer === false')
    assert 'install' not in await page.evaluate('calls')


async def test_failed_install_keeps_details_and_waits_for_explicit_python_fallback(page):
    await page.set_viewport_size({"width": 360, "height": 640})
    await page.evaluate('startLoad()')
    await page.get_by_role('button', name='Install and load').click()
    await page.evaluate("report('Rust is required to build the fast reader.'); result = { ready: false, error: 'Rust was not found on the cluster.' }; complete()")
    await expect(page.get_by_role('dialog')).to_contain_text('Rust was not found on the cluster.')
    await page.get_by_text('Installation details', exact=True).click()
    await expect(page.locator('.fast-reader-details pre')).to_have_text('Rust is required to build the fast reader.')
    assert not await page.evaluate('loaded')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.get_by_role('button', name='Continue with Python').click()
    await page.wait_for_function('loaded')
    assert await page.evaluate('calls') == ['offer', 'install', 'LOAD_DATASET']


async def test_manually_managed_environment_offers_instructions_without_install(page):
    await page.evaluate('state.managed = false; load()')
    await expect(page.get_by_role('dialog')).to_contain_text('ffast install-fast-reader')
    await expect(page.get_by_role('button', name='Install and load')).to_have_count(0)
    await page.get_by_role('button', name='Load with Python', exact=True).click()
    await page.wait_for_function('answer === true')
    assert await page.evaluate('calls') == ['offer']


async def test_cancelling_a_load_during_install_never_sends_the_load(page):
    await page.evaluate('startLoad()')
    await page.get_by_role('button', name='Install and load').click()
    await page.get_by_role('button', name='Cancel load').click()
    await page.evaluate('complete()')
    assert not await page.evaluate('loaded')
    assert 'LOAD_DATASET' not in await page.evaluate('calls')


async def test_disconnect_closes_the_prompt_and_does_not_load_on_another_connection(page):
    await page.evaluate('startLoad()')
    await page.get_by_role('button', name='Install and load').click()
    await page.evaluate('connected = false; prompt.cancel(); drop()')
    await expect(page.locator('#fast-reader-dialog')).to_have_count(0)
    assert not await page.evaluate('loaded')
    assert 'LOAD_DATASET' not in await page.evaluate('calls')


async def test_an_install_started_in_another_window_is_waited_for(page):
    await page.evaluate('state.offer = false; state.busy = true; startLoad()')
    await expect(page.get_by_role('status')).to_contain_text('another window')
    assert await page.evaluate('calls') == ['offer', 'wait']
    await page.evaluate('complete()')
    await page.wait_for_function('loaded')
    assert 'install' not in await page.evaluate('calls')


async def test_local_connections_other_formats_and_ready_readers_do_not_prompt(page):
    assert await page.evaluate("prompt.beforeLoad('data.npz', 'dataset')")
    assert await page.evaluate("prompt.beforeLoad('model.xyz', 'model')")
    await page.evaluate('cluster = false; load()')
    await page.wait_for_function('answer === true')
    assert await page.evaluate('calls') == []
    await page.evaluate('cluster = true; state.ready = true; load()')
    await page.wait_for_function('answer === true')
    await expect(page.locator('#fast-reader-dialog')).to_have_count(0)


async def test_cluster_file_browser_waits_for_the_choice_before_loading():
    from .test_web_runtime import (
        DATASET_PATH, _expect_connected, _file_menu, _spawn_server,
        _wait_for_server_ready,
    )

    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1100, "height": 800})
            try:
                await page.goto(f'http://127.0.0.1:{web_port}/?port={ws_port}', wait_until='networkidle')
                await _expect_connected(page)
                await page.evaluate("""() => {
                  const app = window.ffastApp, c = app._conn;
                  // Simulate the cluster handover; all file I/O still uses
                  // the real server. No installer is called in this test.
                  app._clusterServerUrl = c._url;
                  app._clusterProfileName = 'Test cluster';
                  const request = c.request.bind(c);
                  c.request = (event, ...args) => event === 'FAST_READER'
                    ? Promise.resolve({ kwargs: { ready: false, managed: true, missing: true, offer: true } })
                    : request(event, ...args);
                }""")
                await _file_menu(page, 'Load Dataset…')
                await page.locator('#fb-path').fill(str(DATASET_PATH.parent))
                await page.locator('#fb-path').press('Enter')
                await page.locator('#fb-list .fb-row', has_text='dataset.xyz').click()
                await page.locator('#fb-load').click()
                await expect(page.locator('#fast-reader-dialog')).to_be_visible()
                assert await page.evaluate('window.ffastApp._datasets.size') == 0
                await page.get_by_role('button', name='Load with Python', exact=True).click()
                await page.wait_for_function('window.ffastApp._datasets.size === 1')
            finally:
                await browser.close()
