"""Metric results on the server go through ``env.results`` (ADR 0085).

The server's session code must not compute, store or key metric results a
second way: that is how the five copies disagreed about failures, predictions
and memory.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN = ("generateMetric(", "metric_failures", "make_metric_cache_key(", "metricExecutor.run(")


def _server_files():
    return sorted((ROOT / "ffast" / "session").rglob("*.py")) + [ROOT / "server.py"]


def test_the_server_computes_metric_results_only_through_the_result_store():
    found = [f"{path.relative_to(ROOT)}: {word}"
             for path in _server_files()
             for word in FORBIDDEN if word in path.read_text()]
    assert not found, ("metric results on the server go through env.results (ADR 0085): "
                       + "; ".join(found))
