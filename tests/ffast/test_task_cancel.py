"""A cancelled task stops and reports done (web parity plan 2026-10-29)."""
import asyncio

from ffast.core import events
from ffast.core.tasks import TaskManager


def test_a_cancelled_task_stops_and_reports_done():
    async def scenario():
        tm = TaskManager()
        done = []
        tm.eventSubscribe("TASK_DONE", lambda task_id: done.append(task_id))

        async def slow(taskID=None):
            await asyncio.sleep(30)

        tm.newTask(slow, name="Slow", visual=True)
        task_id = tm.latestTaskID
        await asyncio.sleep(0)
        tm.eventPush("TASK_CANCEL", task_id)
        for _ in range(200):
            await tm.eventHandle()
            if task_id in done and tm.getTask(task_id) is None:
                break
            await asyncio.sleep(0.01)
        return tm, task_id, done

    tm, task_id, done = asyncio.run(scenario())
    try:
        assert task_id in done
        assert tm.getTask(task_id) is None
    finally:
        # The bus is module-global: drop this manager's subscriptions.
        for event, items in events.subs.items():
            items[:] = [s for s in items if s[0] is not tm]
