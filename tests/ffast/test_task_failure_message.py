"""A failed task is logged as a sentence a pop-up can show (ADR 0066 rule 7):
its name and the error, not the task object."""
import asyncio
import logging

from ffast.core.tasks import TaskManager


def _run_failing(exc):
    async def run():
        tm = TaskManager()

        async def work(taskID=None):
            raise exc

        tm.newTask(work, name="Loading dataset")
        task = next(iter(tm.runningTasks.values()))["task"]
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)          # let the done callback run

    asyncio.run(run())


def _failures(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_failed_task_says_its_name_and_error(caplog):
    with caplog.at_level(logging.ERROR, logger="FFAST"):
        _run_failing(FileNotFoundError("no such file: a.xyz"))
    [record] = _failures(caplog)
    assert record.getMessage() == "Loading dataset failed: no such file: a.xyz"
    assert getattr(record, "popup", True) is True


def test_a_failure_its_window_was_told_of_does_not_pop_up_again(caplog):
    exc = FileNotFoundError("no such file: a.xyz")
    exc.ffast_reported = True           # the load's on_error told the window
    with caplog.at_level(logging.ERROR, logger="FFAST"):
        _run_failing(exc)
    [record] = _failures(caplog)
    assert record.popup is False
