"""Each test leaves the in-process event bus as it found it.

The bus is one subscriber table per process, holding subscribers strongly
(events.py; kept so on purpose, one Environment per process). A test run is
one process: an Environment a test built stayed subscribed for every later
test, its queue filling with their events, and a test that handled its own
queue could run another test's event. tests/conftest.py now restores the
table around every test.
"""
from ffast.core import events
from ffast.core.environment import Environment
from tests.event_bus import restore_subscriptions, save_subscriptions


def _environments():
    return [owner for table in events.subs.values() for owner, _func, _async in table
            if isinstance(owner, Environment)]


def test_an_environment_made_between_save_and_restore_leaves_no_subscription():
    table = events.subs
    before = _environments()
    saved = save_subscriptions()

    env = Environment()
    assert any(owner is env for owner in _environments())

    restore_subscriptions(saved)

    assert _environments() == before
    assert events.subs is table          # changed in place: importers keep the same table


def test_a_test_starts_with_no_environment_another_test_made():
    """What the suite-wide fixture gives every test (tests/conftest.py)."""
    assert _environments() == []
