"""Where predictions and metrics are computed (ADR 0020).

``DataService`` does not branch on where a metric or prediction is computed.
It holds an injected ``PredictionSource``. The only one left is
``InProcessSource``: metrics and predictions are computed in-process. The
server process uses it, as does a standalone/headless Environment.

The seam stays so a different source can be wired in without changing
``DataService``.
"""


class PredictionSource:
    """Interface: a place metric results and prediction arrays can come from."""

    @property
    def available(self):
        """True when this source can currently supply server-side results."""
        return False

    def fetch_metric_result(self, metric_id, params, model, dataset, key):
        """Try to source a metric result; return True if it was cached locally."""
        return False

    def fetch_prediction_arrays(self, dataset_fp, model_fp):
        """Try to source prediction arrays for a (dataset, model); True on success."""
        return False


class InProcessSource(PredictionSource):
    """No server: everything is computed in-process (inherits the False defaults)."""
