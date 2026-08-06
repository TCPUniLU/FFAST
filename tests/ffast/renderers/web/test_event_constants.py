"""The web client's event-name constants must match the Python protocol (ADR 0050).

``ffast/renderers/web/static/events.js`` names every wire message the browser
sends or listens for. Those strings are only meaningful if the server agrees:
an outbound name the server does not know is dropped as an unknown event (logged
server-side, invisible in the browser), and an inbound handler registered under a
name the server never sends simply never fires. Both fail silently, which is why
they are checked here rather than trusted.

Parsed with a regex rather than executed — there is no JS runtime in the test
environment, and ADR 0045 keeps the web client build-free and npm-free.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ffast.protocol import control
from ffast.protocol.rpc import SERVER_TO_CLIENT


EVENTS_JS = (
    Path(__file__).resolve().parents[4]
    / "ffast" / "renderers" / "web" / "static" / "events.js"
)


def _parse_block(name: str) -> dict[str, str]:
    """Extract one `export const <name> = Object.freeze({...})` mapping."""
    src = EVENTS_JS.read_text()
    m = re.search(
        rf"export const {name} = Object\.freeze\(\{{(.*?)\}}\);",
        src,
        re.S,
    )
    assert m, f"{name} block not found in events.js"
    return dict(re.findall(r"^\s*(\w+):\s*'([^']+)',", m.group(1), re.M))


@pytest.fixture(scope="module")
def out_events() -> dict[str, str]:
    return _parse_block("OUT")


@pytest.fixture(scope="module")
def in_events() -> dict[str, str]:
    return _parse_block("IN")


def test_events_js_is_parseable(out_events, in_events):
    assert out_events, "OUT parsed empty — the regex and events.js have diverged"
    assert in_events, "IN parsed empty — the regex and events.js have diverged"


def test_constant_names_match_their_values(out_events, in_events):
    """`FOO: 'FOO'` — a mismatch would make call sites read correctly and behave wrong."""
    for key, value in {**out_events, **in_events}.items():
        assert key == value, f"events.js: {key} maps to {value!r}"


def test_outbound_events_are_known_to_the_server(out_events):
    """Every OUT name must be a control message the server accepts."""
    known = set(control.CLIENT_TO_SERVER) | {
        control.HELLO,
        control.GRACEFUL_DISCONNECT,
    }
    unknown = sorted(set(out_events.values()) - known)
    assert not unknown, (
        f"events.js OUT names the server does not accept: {unknown}. "
        "Add them to control.CLIENT_TO_SERVER or fix the spelling."
    )


def test_inbound_events_are_things_the_server_sends(in_events):
    """Every IN name must be a broadcast, a reply, or a dedicated announcement."""
    announcements = {
        control.REMOTE_DATASET_META, control.REMOTE_MODEL_META,
        control.SCENE_SNAPSHOT, control.SCENE_PATCH, control.COMMAND_RESULT,
        control.METRIC_CATALOG, control.METRICS_UPDATED, control.TAB_LAYOUT,
        control.SUBSET_EXPORTED, control.SESSION_SAVED, control.SESSION_LOADED,
    }
    known = set(SERVER_TO_CLIENT) | set(control.REPLY_EVENTS) | announcements
    unknown = sorted(set(in_events.values()) - known)
    assert not unknown, (
        f"events.js IN names the server never sends: {unknown}. "
        "Either the server should emit them or the spelling is wrong."
    )


def test_session_acks_are_declared_both_sides(out_events, in_events):
    """The ADR 0050 acks specifically — the bug they fixed was silent."""
    assert control.SAVE_SESSION in out_events.values()
    assert control.LOAD_SESSION in out_events.values()
    assert control.SESSION_SAVED in in_events.values()
    assert control.SESSION_LOADED in in_events.values()


def test_no_bare_event_strings_left_in_the_web_client():
    """`.on('FOO')` / `.send('FOO')` must go through the constants."""
    static = EVENTS_JS.parent
    offenders = []
    for js in sorted(static.glob("*.js")) + sorted(static.glob("panes/*.js")):
        if js.name in ("events.js", "msgpack.js"):
            continue
        for i, line in enumerate(js.read_text().splitlines(), 1):
            if re.search(r"\.(?:on|send)\('[A-Z_]{3,}'", line):
                offenders.append(f"{js.relative_to(static)}:{i}: {line.strip()}")
    assert not offenders, "bare wire-event strings found:\n" + "\n".join(offenders)
