"""Save and restore the in-process event bus's subscriber table.

``ffast.core.events.subs`` is one table per process (on purpose: one
Environment per process). A test run is one process, so tests/conftest.py
restores the table after every test: nothing a test subscribed stays
subscribed, so no event of a later test reaches it. The table is changed in
place: modules hold it by name.
"""
from ffast.core import events


def save_subscriptions():
    """A copy of the table: event name → its subscriber list."""
    return {event: list(table) for event, table in events.subs.items()}


# Subscribers taken off the table, kept alive until the run ends: they used to
# live that long (the table held them), and freeing Qt widgets a test left
# behind, at whatever moment the collector runs, crashed a later Qt test.
_dropped = []


def restore_subscriptions(saved):
    """Put the table back as ``saved`` had it."""
    kept = {id(entry) for table in saved.values() for entry in table}
    _dropped.extend(entry for table in events.subs.values() for entry in table
                    if id(entry) not in kept)
    for event in list(events.subs):
        if event not in saved:
            del events.subs[event]
    for event, table in saved.items():
        events.subs[event][:] = table
