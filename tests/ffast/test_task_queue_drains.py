"""Every waiting task starts on the next turn of the main loop.

Regression: one waiting task started per turn, and a turn lasts 0.1 s, so at
most ten tasks started a second. Every metric a plot asks the server for is a
task, so a zoom's ~34 results took ~3.4 s to arrive while the server computed
them in ~40 ms.
"""
import asyncio

from ffast.core.tasks import TaskManager


def test_one_turn_starts_every_waiting_task():
    async def turn():
        tm = TaskManager()

        async def work(taskID=None):
            await asyncio.sleep(0)

        for i in range(5):
            tm.queueTask(work, name=f"task {i}")
        await tm.handleTaskQueue()
        started = len(tm.runningTasks)
        await asyncio.gather(*(t["task"] for t in tm.runningTasks.values()))
        return started, tm.taskQueue.empty()

    assert asyncio.run(turn()) == (5, True)
