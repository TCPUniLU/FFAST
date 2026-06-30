"""Unit tests for the request/reply correlator (PendingRequests).

The five remote request/reply pipelines (key/length probes, prediction arrays,
metric results, subdataset arrays) used to live as five hand-rolled ``_pending_*``
dicts welded inside the live-WebSocket listener loop — untestable without a
socket. They now route through one ``PendingRequests`` correlator whose logic is
pure data and so testable here with no socket at all.
"""
import asyncio

import cluster.session as cs


def _run(coro_fn):
    asyncio.run(coro_fn())


def test_resolve_serves_an_awaiting_request():
    async def scenario():
        pr = cs.PendingRequests()
        sent = []

        async def send():
            sent.append(1)

        task = asyncio.create_task(pr.request("CH", "k", send, timeout=5))
        await asyncio.sleep(0)  # let the request register + send

        assert pr.resolve("CH", "k", {"v": 1}) is True
        assert await task == {"v": 1}
        assert len(sent) == 1                 # request sent exactly once
        assert ("CH", "k") not in pr._pending  # cleaned up after resolve

    _run(scenario)


def test_resolve_unknown_key_returns_false():
    pr = cs.PendingRequests()
    # A reply with no awaiter (duplicate / orphaned) is reported, not crashed.
    assert pr.resolve("CH", "nobody", {"v": 1}) is False


def test_timeout_drops_the_dead_future():
    async def scenario():
        pr = cs.PendingRequests()

        async def send():
            pass

        try:
            await pr.request("CH", "k", send, timeout=0.01)
            assert False, "expected TimeoutError"
        except asyncio.TimeoutError:
            pass

        # The listener only pops on a reply; on timeout the correlator must drop
        # the dead future itself, else a later identical request coalesces onto a
        # future that will never resolve.
        assert ("CH", "k") not in pr._pending

    _run(scenario)


def test_coalesce_joins_concurrent_identical_requests():
    async def scenario():
        pr = cs.PendingRequests()
        sent = []

        async def send():
            sent.append(1)

        t1 = asyncio.create_task(
            pr.request("CH", "k", send, timeout=5, coalesce=True)
        )
        t2 = asyncio.create_task(
            pr.request("CH", "k", send, timeout=5, coalesce=True)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert pr.resolve("CH", "k", {"v": 9}) is True
        r1, r2 = await asyncio.gather(t1, t2)
        assert r1 == r2 == {"v": 9}  # both awaiters served by one reply
        assert len(sent) == 1        # only ONE request put on the wire

    _run(scenario)


def test_channel_namespaces_the_key_space():
    # The two probe channels are both keyed by file path; a single un-namespaced
    # dict would collide them. The channel keeps them independent.
    async def scenario():
        pr = cs.PendingRequests()

        async def send():
            pass

        t_keys = asyncio.create_task(
            pr.request("DATASET_KEYS_RESPONSE", "/f.xyz", send, timeout=5)
        )
        t_len = asyncio.create_task(
            pr.request("DATASET_LENGTH_RESPONSE", "/f.xyz", send, timeout=5)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert pr.resolve("DATASET_KEYS_RESPONSE", "/f.xyz", {"keys": 1})
        assert await t_keys == {"keys": 1}
        assert not t_len.done()  # the length probe is untouched

        assert pr.resolve("DATASET_LENGTH_RESPONSE", "/f.xyz", {"n": 5})
        assert await t_len == {"n": 5}

    _run(scenario)
