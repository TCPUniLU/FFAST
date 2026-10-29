"""A formula cannot take the server down (ADR 0064 step 1).

The check refuses a formula that is too long or nested too deeply, and Try
computes the formula in the metric worker process, so a formula that uses up
memory kills only the worker, which restarts.
"""
import functools
import os

import pytest

from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.expr import MAX_FORMULA_CHARS, MAX_FORMULA_DEPTH, parse_and_validate
from ffast.metrics.pool import WorkerCallError, WorkerProcessExecutor
from ffast.metrics.registry import MetricRegistry


def test_the_limits_are_the_ones_the_adr_names():
    assert (MAX_FORMULA_CHARS, MAX_FORMULA_DEPTH) == (2000, 50)


def test_a_formula_at_the_length_limit_is_accepted():
    parse_and_validate("a" + " " * (MAX_FORMULA_CHARS - 1))


def test_a_longer_formula_is_refused_naming_the_limit():
    with pytest.raises(ValueError, match="2,001 characters long; the limit is 2,000"):
        parse_and_validate("a" + " " * MAX_FORMULA_CHARS)


def _nested(levels):
    """``abs(abs(...a...))`` whose deepest path holds ``levels`` parts."""
    return "abs(" * (levels - 1) + "a" + ")" * (levels - 1)


def test_a_formula_at_the_depth_limit_is_accepted():
    parse_and_validate(_nested(MAX_FORMULA_DEPTH))


def test_a_deeper_formula_is_refused_naming_the_limit():
    with pytest.raises(ValueError, match="nested 51 levels deep; the limit is 50"):
        parse_and_validate(_nested(MAX_FORMULA_DEPTH + 1))


def test_a_long_chain_of_operators_counts_as_depth():
    # Python reads a + a + a ... as nested additions; 1,000 of them would
    # exhaust the evaluator's recursion, so the check refuses them first.
    with pytest.raises(ValueError, match="levels deep"):
        parse_and_validate("+".join(["a"] * 999))


def test_a_thousand_minus_signs_are_refused_not_crashed_on():
    with pytest.raises(ValueError, match="levels deep"):
        parse_and_validate("-" * 1500 + "a")


# ── the executor runs one function: here, or in the worker ───────────────────

def test_in_process_call_runs_the_function_here():
    assert InProcessExecutor(MetricRegistry()).call(os.getpid, {}) == os.getpid()


@pytest.fixture
def worker():
    ex = WorkerProcessExecutor(MetricRegistry())
    yield ex
    ex.shutdown()


def test_the_worker_call_runs_the_function_in_another_process(worker):
    assert worker.call(os.getpid, {}) != os.getpid()


def test_an_error_in_the_worker_names_it(worker):
    with pytest.raises(WorkerCallError, match="ZeroDivisionError"):
        worker.call(functools.partial(divmod, 1, 0), {})


def test_a_worker_that_dies_is_reported_and_replaced(worker):
    with pytest.raises(WorkerCallError, match="stopped"):
        worker.call(functools.partial(os._exit, 3), {})
    assert worker.call(os.getpid, {}) != os.getpid()      # a new worker answers


def test_try_runs_its_formula_in_the_worker(worker):
    import numpy as np

    from ffast.metrics import dims
    from ffast.metrics.try_formula import try_formula

    r = MetricRegistry()

    @r.metric(id="b.e", inputs={"x": "reference.energies"}, shape=(dims.N_frames,), unit="energy")
    def e(x):
        return x

    result = try_formula("e * 10", {"e": "b.e"}, r, lambda ref: np.array([1.0, 2.0, 4.0]),
                         np.array([1, 1, 1]), compute=worker.call)
    assert (result.min, result.max, result.bad_count) == (10.0, 40.0, 0)
