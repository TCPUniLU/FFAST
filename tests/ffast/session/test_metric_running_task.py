"""A metric computing on the server is a Running Task while it runs: one row
in every window's Tasks list, not cancellable, gone when the result is in."""

import asyncio
from types import SimpleNamespace

from ffast.core.tasks import TaskManager
from ffast.session.server_session import _compute_once


def test_a_computing_metric_is_one_running_task_until_it_ends():
    async def scenario():
        env = SimpleNamespace(tm=TaskManager())
        result = asyncio.get_running_loop().create_future()
        name = "Computing Force MAE · shard_03 / P1"

        # Two panels ask for the same metric: one computation, one row.
        first = asyncio.ensure_future(_compute_once("k1", lambda: result, env, name))
        second = asyncio.ensure_future(_compute_once("k1", lambda: result, env, name))
        await asyncio.sleep(0)
        during = dict(env.tm.runningTasks)

        result.set_result("k1")
        assert await first == "k1" and await second == "k1"
        await asyncio.sleep(0)
        return during, dict(env.tm.runningTasks)

    during, after = asyncio.run(scenario())
    assert list(during) == ["metric:k1"]
    task = during["metric:k1"]
    assert (task["name"], task["visual"], task["cancellable"]) == (
        "Computing Force MAE · shard_03 / P1", True, False)
    assert after == {}


def test_a_failed_metric_leaves_the_tasks_list_too():
    async def scenario():
        env = SimpleNamespace(tm=TaskManager())
        result = asyncio.get_running_loop().create_future()
        waiting = asyncio.ensure_future(_compute_once("k2", lambda: result, env, "x"))
        await asyncio.sleep(0)
        result.set_exception(RuntimeError("boom"))
        try:
            await waiting
        except RuntimeError:
            pass
        await asyncio.sleep(0)
        return dict(env.tm.runningTasks)

    assert asyncio.run(scenario()) == {}
