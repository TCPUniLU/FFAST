"""``Environment(headless=False)`` is kept on purpose (ADR 0098): no client
uses it since the desktop client went, but it may be needed again. It must
still build, with the in-process metric executor."""
import asyncio

from ffast.core.environment import Environment
from ffast.metrics.executor import InProcessExecutor


def test_a_non_headless_environment_builds_with_the_in_process_executor():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    env = Environment(headless=False)

    assert env.headless is False
    assert isinstance(env.data.metricExecutor, InProcessExecutor)
