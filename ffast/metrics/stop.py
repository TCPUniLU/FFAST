"""Stopping a metric whose data was deleted (ADR 0079 decision 3).

A step running in the server cannot be killed, so it checks now and then:
``check()`` raises ``Stopped`` once the dataset or prediction it reads has been
deleted.
"""

from __future__ import annotations

import contextvars
from typing import Callable

STOPPED = "Stopped: its dataset or prediction was deleted"


class Stopped(Exception):
    """The data a step reads was deleted."""


_current: contextvars.ContextVar[Callable[[], bool]] = contextvars.ContextVar(
    "ffast_metric_stopped", default=lambda: False)


def check() -> None:
    """Raise ``Stopped`` if the current step's data was deleted."""
    if _current.get()():
        raise Stopped(STOPPED)


def running_under(stopped: Callable[[], bool]):
    """Make ``check()`` follow ``stopped`` in this context; returns a token for
    ``reset``."""
    return _current.set(stopped)


def reset(token) -> None:
    _current.reset(token)
