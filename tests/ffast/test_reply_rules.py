"""ADR 0097: every client message says how it ends, and every request whose
final reply names it is answered with ``replies.reply`` on the server."""
import re
from pathlib import Path

from ffast.protocol import control

SERVER_SESSION = Path(__file__).resolve().parents[2] / "ffast" / "session" / "server_session.py"


def test_every_client_message_is_in_exactly_one_table():
    tables = [set(control.FINAL_REPLY), set(control.OWN_ID_REPLY), set(control.NO_FINAL_REPLY)]
    covered = set().union(*tables)
    assert covered == set(control.CLIENT_TO_SERVER), (
        f"missing: {sorted(set(control.CLIENT_TO_SERVER) - covered)}, "
        f"unknown: {sorted(covered - set(control.CLIENT_TO_SERVER))}")
    assert sum(len(t) for t in tables) == len(covered), "a message is in two tables"


def test_final_replies_are_never_sent_as_plain_messages():
    """A final reply packed with rpc.pack names no request, so the window
    that asked would wait for it forever."""
    source = SERVER_SESSION.read_text()
    plain = sorted({event for event in set(control.FINAL_REPLY.values())
                    if re.search(rf"\bpack\(\s*control\.{event}\b", source)})
    assert plain == [], f"sent with pack(), not replies.reply(): {plain}"


def test_every_final_reply_is_sent_somewhere():
    source = SERVER_SESSION.read_text()
    unsent = sorted({event for event in set(control.FINAL_REPLY.values())
                     if f"control.{event}" not in source})
    assert unsent == []
