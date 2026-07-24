"""Where predictions and metrics are computed (ADR 0020).

``DataService`` does not branch on ``if self.serverConnection`` to decide whether to
compute a metric/prediction locally or ask a server.  Instead it holds an
injected ``PredictionSource``:

* ``InProcessSource`` — no server reachable; metrics and predictions are computed
  in-process.  Used by a standalone/headless Environment and, conceptually, by
  the server process itself (it owns the real models).
* ``RemoteSource`` — a server session exists; metric results and prediction
  arrays are fetched from it.  ``available`` reflects the *current* session
  state, so the same source degrades to in-process behaviour when disconnected.

This is the seam that lets the same ``DataService`` code run as either a server
(computes in-process) or a connected client (delegates to the server) — and that
makes the planned "edit the system on the client, compute energy/forces on the
server" flow a wiring choice rather than a rewrite.
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


class RemoteSource(PredictionSource):
    """Delegates metric/prediction sourcing to a connected server session.

    ``remote`` is any object exposing the live-session surface
    (``active_session()``, ``_fetchMetricResultSync``,
    ``_fetchPredictionArraysSync``) — the ConnectionManager.
    """

    def __init__(self, remote):
        self._remote = remote

    @property
    def available(self):
        session, _ = self._remote.active_session()
        return session is not None

    def fetch_metric_result(self, metric_id, params, model, dataset, key):
        return self._remote._fetchMetricResultSync(
            metric_id, params, model, dataset, key
        )

    def fetch_prediction_arrays(self, dataset_fp, model_fp):
        return self._remote._fetchPredictionArraysSync(dataset_fp, model_fp)
