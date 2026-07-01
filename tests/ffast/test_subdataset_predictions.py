"""Regression: interactive subbing ("selects but doesn't plot").

Two coupled bugs, 2026-06-23 GUI test of the Panels tab:

A. A client-side ``SubDataset`` is unknown to the server. ``generateMetric``'s
   ghost branch fetched predictions for ``dataset.fingerprint`` (the *sub* fp) →
   server has no such dataset → empty arrays → "predictions still missing after
   server fetch" → the metric never computes → the sub selection never plots.
   Fix: fetch the *root parent's* predictions (the server holds those);
   ``getData`` already sub-slices them by the sub indices.

B. Every panel metric fires a concurrent ``request_prediction_arrays`` for the
   same ``(dataset, model)``. Without coalescing, each call overwrote the single
   pending future, orphaning the others (they hung to timeout) and the duplicate
   server replies logged "PREDICTION_ARRAYS for unknown". Fix: coalesce
   concurrent identical requests onto one in-flight future.
"""
import asyncio
import types

from client.data_service import DataService
import cluster.connection as cs


class _DS(DataService):
    def initialiseDataTypes(self):
        self.dataTypes = {}


class _Reg:
    def get(self, key):
        return None


class _Events:
    def eventPush(self, *a, **k):
        pass

    def push(self, *a, **k):
        pass


def _make():
    return _DS(cache={}, models=_Reg(), datasets=_Reg(), tm=None,
               events=_Events())


# ── Bug A ────────────────────────────────────────────────────────────────────
def test_generateMetric_fetches_root_parent_predictions_for_subdataset():
    ds = _make()
    fetched = []

    class _Src:
        available = True

        def fetch_metric_result(self, *a, **k):
            return False  # server can't compute it for a client-side sub

        def fetch_prediction_arrays(self, ds_fp, model_fp):
            fetched.append(ds_fp)
            return True

    class _IR:
        # Still "missing" after the fetch so generateMetric returns early — the
        # test only asserts *which* fingerprint was fetched.
        def missing_prediction_keys(self, *a, **k):
            return ["forces"]

    ds._source = _Src()
    ds._inputResolver = _IR()  # inputResolver is a lazy read-only property

    root = types.SimpleNamespace(isSubDataset=False, parent=None, fingerprint="ROOT")
    mid = types.SimpleNamespace(isSubDataset=True, parent=root, fingerprint="MID")
    sub = types.SimpleNamespace(isSubDataset=True, parent=mid, fingerprint="SUB")
    model = types.SimpleNamespace(isGhost=True, fingerprint="M")

    ds.generateMetric(
        "ffast.force_prediction", {}, model, sub,
        key="ffast.force_prediction__M__SUB",
    )

    # Walked up the whole sub chain to the root real dataset — not "SUB"/"MID".
    assert fetched == ["ROOT"]


# ── Bug B ────────────────────────────────────────────────────────────────────
def test_request_prediction_arrays_coalesces_concurrent_calls():
    async def scenario():
        fake = types.SimpleNamespace(_pending=cs.PendingRequests(), pushed=[])

        async def push_event(*a, **k):
            fake.pushed.append(a)

        fake.push_event = push_event
        req = cs.ServerConnection.request_prediction_arrays.__get__(fake)

        t1 = asyncio.create_task(req("DS", "M", timeout=5))
        t2 = asyncio.create_task(req("DS", "M", timeout=5))
        await asyncio.sleep(0)  # let both register / coalesce
        await asyncio.sleep(0)

        # Exactly one future is pending → the second call coalesced onto it.
        resolved = fake._pending.resolve(
            "PREDICTION_ARRAYS", ("DS", "M"), {"pred__forces__M": 1}
        )
        assert resolved                                 # one awaiter was found

        r1, r2 = await asyncio.gather(t1, t2)
        assert r1 == r2 == {"pred__forces__M": 1}      # both awaiters served
        assert len(fake.pushed) == 1                    # only ONE request sent

    asyncio.run(scenario())
