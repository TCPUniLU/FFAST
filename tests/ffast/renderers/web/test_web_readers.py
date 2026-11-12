"""A reader from a pip package shows in the Type list and loads a dataset (ADR 0095).

The toy reader is installed the way an outside reader is: a ``.dist-info``
folder with an ``ffast.readers`` entry point, on the server process's path.
"""
import os

import pytest
from playwright.async_api import async_playwright, expect

from tests.ffast.readers.toy_reader import make_frames, write_toy

from .test_web_runtime import _expect_connected, _file_menu, _spawn_server, _wait_for_server_ready

pytestmark = pytest.mark.integration


@pytest.fixture
def toy_reader_installed(tmp_path, monkeypatch):
    """Make the toy reader findable by ``entry_points`` in a spawned server."""
    dist = tmp_path / "site" / "ffast_toy-0.1.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: ffast-toy\nVersion: 0.1\n")
    (dist / "entry_points.txt").write_text(
        "[ffast.readers]\ntoy = tests.ffast.readers.toy_reader:ToyReader\n")
    old = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(tmp_path / "site"), old])))


async def test_web_a_readers_type_is_listed_and_loads_a_dataset(toy_reader_installed, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_toy(data / "run.toy", make_frames(6, variable=True))
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 820})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Load Dataset…")
                values = await page.locator("#fb-type option").evaluate_all(
                    "(os) => os.map((o) => o.value)")
                assert values == ["ase (auto)", "sGDML", "toy"]
                await page.locator("#fb-path").fill(str(data))
                await page.locator("#fb-path").press("Enter")
                await page.locator("#fb-list .fb-row", has_text="run.toy").click()
                await expect(page.locator("#fb-type")).to_have_value("toy")
                await expect(page.locator("#fb-keys")).to_be_hidden()
                await page.locator("#fb-load").click()
                row = page.locator("#dataset-list .obj-row")
                await expect(row).to_have_count(1, timeout=30000)
                await expect(row).to_contain_text("run")
            finally:
                await browser.close()


async def _toy_dataset_fp(ws_port, path):
    """Load ``path`` as a toy dataset through a headless client; its fingerprint."""
    from .test_web_runtime import _connect_headless_client, _wait_for_event, pack
    ws = await _connect_headless_client(ws_port)
    try:
        await ws.send(pack("LOAD_DATASET", (str(path), "toy"), {}))
        meta = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
        return meta["args"][0]
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()


async def test_web_a_toy_prediction_loads_from_load_prediction(toy_reader_installed, tmp_path):
    from .test_web_loading import _go_to, _sent
    from .test_web_runtime import _RECORD_SENDS, _open_loupe
    data = tmp_path / "data"
    data.mkdir()
    write_toy(data / "ds.toy", make_frames(5, variable=True))
    write_toy(data / "pred.toy", make_frames(5, variable=True, extra=["mace"]))
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        dataset_fp = await _toy_dataset_fp(ws_port, data / "ds.toy")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 820})
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await page.evaluate(_RECORD_SENDS)
                await _file_menu(page, "Load Prediction…")
                await _go_to(page, data)
                await page.locator("#fb-list .fb-row", has_text="pred.toy").click()
                # the names the reader lists, paired: dft with dft
                await expect(page.locator("#fb-energy-key")).to_have_value("dft")
                await expect(page.locator("#fb-force-key")).to_have_value("dft")
                values = await page.locator("#fb-force-key option").evaluate_all(
                    "(os) => os.map((o) => o.value)")
                assert values == ["", "dft", "mace"]
                await page.locator("#fb-force-key").select_option("mace")
                await page.locator("#fb-energy-key").select_option("mace")
                await page.locator("#fb-load").click()
                await expect(page.locator("#model-list .obj-row", has_text="pred.toy")).to_be_visible(
                    timeout=30000)
                assert await _sent(page, "LOAD_PREDICTION") == [{
                    "selected_energy_key": "mace", "selected_force_key": "mace", "reader": "toy"}]
            finally:
                await browser.close()


async def test_web_a_toy_prediction_with_energies_only_can_load(toy_reader_installed, tmp_path):
    from .test_web_loading import _go_to
    from .test_web_runtime import _open_loupe
    data = tmp_path / "data"
    data.mkdir()
    write_toy(data / "ds.toy", make_frames(4))
    write_toy(data / "pred.toy", make_frames(4, forces=False))
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        dataset_fp = await _toy_dataset_fp(ws_port, data / "ds.toy")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 820})
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await _file_menu(page, "Load Prediction…")
                await _go_to(page, data)
                await page.locator("#fb-list .fb-row", has_text="pred.toy").click()
                await expect(page.locator("#fb-energy-key")).to_have_value("dft")
                await expect(page.locator("#fb-force-key")).to_have_value("")
                await expect(page.locator("#fb-load")).to_be_enabled()
                await page.locator("#fb-load").click()
                await expect(page.locator("#model-list .obj-row", has_text="pred.toy")).to_be_visible(
                    timeout=30000)
            finally:
                await browser.close()


async def test_web_a_mismatched_toy_prediction_says_what_differs(toy_reader_installed, tmp_path):
    from .test_web_loading import _go_to
    from .test_web_runtime import _open_loupe
    data = tmp_path / "data"
    data.mkdir()
    write_toy(data / "ds.toy", make_frames(6))
    write_toy(data / "short.toy", make_frames(5))
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        dataset_fp = await _toy_dataset_fp(ws_port, data / "ds.toy")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 820})
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await _file_menu(page, "Load Prediction…")
                await _go_to(page, data)
                await page.locator("#fb-list .fb-row", has_text="short.toy").click()
                await expect(page.locator("#fb-force-key")).to_have_value("dft")
                await page.locator("#fb-load").click()
                await expect(page.locator("#status")).to_contain_text(
                    "has 5 frames, but the dataset's file has 6", timeout=30000)
                await expect(page.locator("#model-list .obj-row")).to_have_count(0)
            finally:
                await browser.close()


async def test_web_a_toy_file_with_more_names_loads_them_as_predictions(toy_reader_installed, tmp_path):
    from .test_web_loading import _go_to, _sent
    from .test_web_runtime import _RECORD_SENDS
    data = tmp_path / "data"
    data.mkdir()
    write_toy(data / "run.toy", make_frames(4, extra=["mace"]))
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await page.evaluate(_RECORD_SENDS)
                await _file_menu(page, "Load Dataset…")
                await _go_to(page, data)
                await page.locator("#fb-list .fb-row", has_text="run.toy").click()
                await expect(page.locator("#fb-type")).to_have_value("toy")
                await expect(page.locator("#fb-keys")).to_be_visible()      # two names: a choice
                assert await page.locator("#fb-ds-energy-key").input_value() == "dft"
                await page.locator("#fb-add-pred").click()
                rows = page.locator("#fb-pred-rows .fb-pred-row")
                await expect(rows.locator(".fb-pred-energy")).to_have_value("mace")
                await rows.locator(".fb-pred-name").fill("MACE")
                await page.locator("#fb-load").click()
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=30000)
                await expect(page.locator("#model-list .obj-row", has_text="MACE")).to_be_visible(timeout=30000)
                sent = (await _sent(page, "LOAD_DATASET"))[0]
                assert sent["selected_energy_key"] == "dft" and sent["prediction_keys"][0][:2] == ["mace", "mace"]
            finally:
                await browser.close()


@pytest.fixture
def more_toy_readers_installed(toy_reader_installed, tmp_path):
    """The field reader, and two readers that both claim ``*.dat``."""
    entry = tmp_path / "site" / "ffast_toy-0.1.dist-info" / "entry_points.txt"
    entry.write_text(
        "[ffast.readers]\n"
        "toy = tests.ffast.readers.toy_reader:ToyReader\n"
        "toyfield = tests.ffast.readers.toy_reader:ToyFieldReader\n"
        "toydat-a = tests.ffast.readers.toy_reader:ToyDatNo\n"
        "toydat-b = tests.ffast.readers.toy_reader:ToyDatYes\n")


async def test_web_the_type_starts_on_the_reader_that_can_read_the_file(more_toy_readers_installed, tmp_path):
    from .test_web_loading import _go_to, _sent
    from .test_web_runtime import _RECORD_SENDS
    data = tmp_path / "data"
    data.mkdir()
    write_toy(data / "x.dat", make_frames(3))
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 820})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Load Dataset…")
                await _go_to(page, data)
                await page.locator("#fb-list .fb-row", has_text="x.dat").click()
                # the ending alone picks toydat-a (listed first); its can_read says no
                await expect(page.locator("#fb-type")).to_have_value("toydat-b")
                # a Type the user chose is kept
                await page.locator("#fb-type").select_option("toydat-a")
                await page.wait_for_timeout(300)
                await expect(page.locator("#fb-type")).to_have_value("toydat-a")
            finally:
                await browser.close()


async def test_web_a_reader_field_is_a_metric_input_with_its_values(more_toy_readers_installed, tmp_path):
    from .test_metrics_pane import _floats, _request_values, _server
    from .test_web_runtime import _connect_headless_client, _wait_for_event, pack
    path = write_toy(tmp_path / "a.toyf", make_frames(4))
    cm, _metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, _web_port):
        ws = await _connect_headless_client(ws_port)
        try:
            await ws.send(pack("SAVE_USER_METRIC", (), {"metric": {
                "id": "user.score", "label": "Score", "unit": "",
                "expr": "s", "vars": {"s": "reference.info.score"}}}))
            assert (await _wait_for_event(ws, "USER_METRIC_SAVED"))["kwargs"]["ok"]
            await ws.send(pack("LOAD_DATASET", (str(path), "toyfield"), {}))
            meta = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
            fp = meta["args"][0]
            assert meta["kwargs"]["units"] == {"energy": "eV", "force": "eV/A"}
            assert _floats(await _request_values(ws, fp, "user.score")) == [0.0, 0.5, 1.0, 1.5]
        finally:
            await ws.close()
