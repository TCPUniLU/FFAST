"""The camera's angles and place (to_fix 9): one convention for the whole
browser client, as pure functions.

``camera_angles.js`` has no DOM, so each rule is evaluated in one browser
launch, as in test_colorbar_layout.py.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import json
import math
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

STATIC_DIR = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"

ANGLES = [(0, 0, 0), (30, 20, 0), (-150, -60, 45), (179, 89, -170), (90, -10, 90), (-45, 75, 180)]

CASES = {
    # angles -> place -> angles
    "round_trip": ("%s.map(([az, el, roll]) => { const p = ca.poseFromAngles({center: [1, 2, 3], distance: 7,"
                   " azimuth: az, elevation: el, roll});"
                   " return ca.anglesFromPose(p.position, [1, 2, 3], p.up); })" % json.dumps(ANGLES)),
    # at the poles the place comes back, though the numbers may not
    "poles": ("[[40, 90, 25], [-120, -90, 70]].map(([az, el, roll]) => {"
              " const p = ca.poseFromAngles({azimuth: az, elevation: el, roll, distance: 5});"
              " const a = ca.anglesFromPose(p.position, [0, 0, 0], p.up);"
              " const q = ca.poseFromAngles({...a, center: [0, 0, 0]}); return {p, q, a}; })"),
    "roll": ("(() => { const flat = ca.poseFromAngles({azimuth: 0, elevation: 0, roll: 0, distance: 1});"
             " const turned = ca.poseFromAngles({azimuth: 0, elevation: 0, roll: 90, distance: 1});"
             " const tilted = ca.poseFromAngles({azimuth: 30, elevation: 40, roll: 33, distance: 1});"
             " return {flat, turned, tilted}; })()"),
    # a missing roll is 0, and roll 0 is the turntable camera of before
    "turntable": ("[[0, 30], [120, -45], [-70, 80]].map(([az, el]) => ca.poseFromAngles({azimuth: az, elevation: el, distance: 2}))"),
    # an up that is not square to the view counts only across it
    "loose_up": "ca.anglesFromPose([0, 0, 5], [0, 0, 0], [0.5, 1, 3])",
}


@pytest.fixture(scope="module")
def results():
    playwright_api = pytest.importorskip("playwright.async_api")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    script = (
        "async () => {\n"
        f"  const ca = await import('{origin}/camera_angles.js');\n"
        "  const out = {};\n"
        + "".join(f"  out[{json.dumps(n)}] = {e};\n" for n, e in CASES.items())
        + "  return out;\n}"
    )

    async def _collect():
        async with playwright_api.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            await page.goto(f"{origin}/events.js")
            await page.set_content("<!doctype html><title>helpers</title>")
            try:
                out = await page.evaluate(script)
            finally:
                await browser.close()
            assert not errors, f"page errors: {errors}"
            return out

    try:
        yield asyncio.run(_collect())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_angles_come_back_from_the_cameras_place(results):
    for (az, el, roll), got in zip(ANGLES, results["round_trip"]):
        assert got["distance"] == pytest.approx(7)
        assert got["azimuth"] == pytest.approx(az, abs=1e-6)
        assert got["elevation"] == pytest.approx(el, abs=1e-6)
        assert got["roll"] == pytest.approx(roll, abs=1e-6)


def test_at_the_poles_the_same_picture_comes_back(results):
    for case in results["poles"]:
        assert case["q"]["position"] == pytest.approx(case["p"]["position"], abs=1e-9)
        assert case["q"]["up"] == pytest.approx(case["p"]["up"], abs=1e-9)
        assert case["a"]["roll"] == 0                    # the whole turn is azimuth
        assert abs(case["a"]["elevation"]) == pytest.approx(90)


def test_roll_turns_the_up_about_the_view(results):
    r = results["roll"]
    assert r["flat"]["up"] == pytest.approx([0, 1, 0], abs=1e-12)
    # rolled 90°, the world's -x points up: the picture turned clockwise
    assert r["turned"]["up"] == pytest.approx([-1, 0, 0], abs=1e-12)
    assert r["turned"]["position"] == pytest.approx(r["flat"]["position"], abs=1e-12)
    tilted = r["tilted"]
    # the up stays square to the view and of unit length
    assert sum(u * p for u, p in zip(tilted["up"], tilted["position"])) == pytest.approx(0, abs=1e-12)
    assert math.hypot(*tilted["up"]) == pytest.approx(1)


def test_roll_zero_is_the_turntable_camera_of_before(results):
    for (az, el), pose in zip([(0, 30), (120, -45), (-70, 80)], results["turntable"]):
        # the formula renderer.js used with OrbitControls
        phi, theta = math.radians(90 - el), math.radians(az)
        old = [2 * math.sin(phi) * math.sin(theta), 2 * math.cos(phi), 2 * math.sin(phi) * math.cos(theta)]
        assert pose["position"] == pytest.approx(old, abs=1e-12)
        # three.js lookAt with up = world y: y minus its part along the view
        z = [p / 2 for p in old]
        up = [(1 if i == 1 else 0) - z[1] * z[i] for i in range(3)]
        n = math.hypot(*up)
        assert pose["up"] == pytest.approx([u / n for u in up], abs=1e-12)


def test_only_the_up_across_the_view_counts(results):
    r = results["loose_up"]
    assert r["elevation"] == pytest.approx(0) and r["azimuth"] == pytest.approx(0)
    assert r["roll"] == pytest.approx(-math.degrees(math.atan2(0.5, 1)))
