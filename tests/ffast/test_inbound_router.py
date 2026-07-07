"""Unit tests for InboundEventRouter (ADR 0032).

The client listener's dispatch of non-reply server events (task namespacing,
phantom-task registration, the TASK_DONE repaint delay) used to be an inline
``if`` chain inside ``ServerConnection._listen`` — untestable without a live
socket. It now routes through a built-once table, testable here with a fake
env and no socket.
"""
import asyncio
from unittest.mock import patch

from cluster.inbound_router import InboundEventRouter


class FakeTaskManager:
    def __init__(self):
        self.registered = []

    def registerPhantomTask(self, task_id):
        self.registered.append(task_id)


class FakeEnv:
    def __init__(self):
        self.tm = FakeTaskManager()
        self.pushed = []

    def eventPush(self, event, *args, **kwargs):
        self.pushed.append((event, args, kwargs))


def _run(coro):
    return asyncio.run(coro)


def test_forward_passes_through_unknown_events():
    async def scenario():
        env = FakeEnv()
        router = InboundEventRouter(env)
        await router.route("SCENE_SNAPSHOT", [], {"a": 1})
        assert env.pushed == [("SCENE_SNAPSHOT", (), {"a": 1})]

    _run(scenario())


def test_task_progress_namespaces_the_task_id():
    async def scenario():
        env = FakeEnv()
        router = InboundEventRouter(env)
        await router.route("TASK_PROGRESS", [1], {"message": "hi"})
        assert env.pushed == [("TASK_PROGRESS", ("remote_1",), {"message": "hi"})]

    _run(scenario())


def test_task_created_registers_phantom_task_and_namespaces():
    async def scenario():
        env = FakeEnv()
        router = InboundEventRouter(env)
        await router.route("TASK_CREATED", [7], {})
        assert env.tm.registered == ["remote_7"]
        assert env.pushed == [("TASK_CREATED", ("remote_7",), {})]

    _run(scenario())


def test_task_done_delays_before_forwarding():
    # The 2s repaint delay is real prod behaviour; patch it out so the test
    # verifies the delay is *requested*, not that the suite waits on it.
    async def scenario():
        env = FakeEnv()
        router = InboundEventRouter(env)
        with patch("cluster.inbound_router.asyncio.sleep") as mock_sleep:
            async def _instant(_seconds):
                pass
            mock_sleep.side_effect = _instant
            await router.route("TASK_DONE", [3], {})
        mock_sleep.assert_called_once_with(2.0)
        assert env.pushed == [("TASK_DONE", ("remote_3",), {})]

    _run(scenario())


def test_remote_dataset_meta_forwards_without_namespacing():
    async def scenario():
        env = FakeEnv()
        router = InboundEventRouter(env)
        await router.route("REMOTE_DATASET_META", ["fp123"], {"n": 10})
        assert env.pushed == [("REMOTE_DATASET_META", ("fp123",), {"n": 10})]

    _run(scenario())


def test_task_progress_with_empty_args_does_not_crash():
    # The `if not args: return args` guard in _namespace_task_id: a namespaced
    # event arriving with no taskID must forward as-is, not IndexError on args[0].
    async def scenario():
        env = FakeEnv()
        router = InboundEventRouter(env)
        await router.route("TASK_PROGRESS", [], {"message": "hi"})
        assert env.pushed == [("TASK_PROGRESS", (), {"message": "hi"})]

    _run(scenario())


def test_task_created_with_empty_args_skips_phantom_registration():
    # Empty args → no taskID to namespace or register; the `if args:` guard in
    # _on_task_created skips registerPhantomTask but still forwards the event.
    async def scenario():
        env = FakeEnv()
        router = InboundEventRouter(env)
        await router.route("TASK_CREATED", [], {})
        assert env.tm.registered == []
        assert env.pushed == [("TASK_CREATED", (), {})]

    _run(scenario())
