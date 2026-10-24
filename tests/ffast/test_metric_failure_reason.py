"""A failed metric's reason in words (ADR 0066 rule 6): what the panel and
the pop-up say, without the traceback."""
from ffast.metrics.models import MetricFailure

TB = '''Traceback (most recent call last):
  File "expr.py", line 9, in _reduce
    raise ValueError("by=atom needs structures with the same atoms")
ValueError: by=atom needs structures with the same atoms
'''


def _failure(tb):
    return MetricFailure(metric_id="m", traceback=tb)


def test_a_traceback_gives_its_last_line_without_the_exception_name():
    assert _failure(TB).reason == "by=atom needs structures with the same atoms"


def test_a_failed_dependency_gives_its_own_reason():
    assert _failure(f"Dependency 'base' failed:\n{TB}").reason == (
        "by=atom needs structures with the same atoms")


def test_a_sentence_stays_as_it_is():
    assert _failure("Required input 'reference.forces' is missing").reason == (
        "Required input 'reference.forces' is missing")
