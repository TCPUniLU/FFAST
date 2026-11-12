"""Unit tests for the request/reply correlator (PendingRequests).

The five remote request/reply pipelines (key/length probes, prediction arrays,
metric results, subdataset arrays) used to live as five hand-rolled ``_pending_*``
dicts welded inside the live-WebSocket listener loop — untestable without a
socket. They now route through one ``PendingRequests`` correlator whose logic is
pure data and so testable here with no socket at all.
"""
import asyncio

import cluster.connection as cs


def _run(coro_fn):
    asyncio.run(coro_fn())


def test_resolve_serves_an_awaiting_request():
    async def scenario():
        pr = cs.PendingRequests()
        sent = []

        async def send():
            sent.append(1)

        task = asyncio.create_task(pr.request("CH", "k", send, timeout=5, same=None))
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
            await pr.request("CH", "k", send, timeout=0.01, same=None)
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
            pr.request("CH", "k", send, timeout=5, same=None)
        )
        t2 = asyncio.create_task(
            pr.request("CH", "k", send, timeout=5, same=None)
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
            pr.request("DATASET_KEYS_RESPONSE", "/f.xyz", send, timeout=5, same=None)
        )
        t_len = asyncio.create_task(
            pr.request("DATASET_LENGTH_RESPONSE", "/f.xyz", send, timeout=5, same=None)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert pr.resolve("DATASET_KEYS_RESPONSE", "/f.xyz", {"keys": 1})
        assert await t_keys == {"keys": 1}
        assert not t_len.done()  # the length probe is untouched

        assert pr.resolve("DATASET_LENGTH_RESPONSE", "/f.xyz", {"n": 5})
        assert await t_len == {"n": 5}

    _run(scenario)


# ── request lifetime (architecture review Nov 2026, candidate 03) ───────────

def test_a_second_identical_request_joins_instead_of_orphaning_the_first():
    """Without coalesce=True the second request replaced the first's future:
    one reply served the second, and the first waited until its timeout."""
    async def scenario():
        pr = cs.PendingRequests()
        sent = []

        async def send():
            sent.append(1)

        t1 = asyncio.create_task(pr.request("CH", "fp", send, timeout=5, same=None))
        t2 = asyncio.create_task(pr.request("CH", "fp", send, timeout=5, same=None))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert pr.resolve("CH", "fp", {"v": 1})
        assert await asyncio.wait_for(asyncio.gather(t1, t2), 1) == [{"v": 1}, {"v": 1}]
        assert sent == [1]

    _run(scenario)


def test_a_failed_send_fails_every_waiter_and_leaves_nothing_behind():
    async def scenario():
        pr = cs.PendingRequests()
        go = asyncio.Event()

        async def send():
            await go.wait()
            raise ConnectionError("socket closed")

        owner = asyncio.create_task(pr.request("CH", "k", send, timeout=5, same=None))
        await asyncio.sleep(0)
        joiner = asyncio.create_task(pr.request("CH", "k", send, timeout=5, same=None))
        await asyncio.sleep(0)
        go.set()

        for task in (owner, joiner):
            try:
                await asyncio.wait_for(task, 1)
                assert False, "expected the send's error"
            except ConnectionError as exc:
                assert "socket closed" in str(exc)
        assert pr._pending == {}

        # A later request sends again instead of waiting on the dead one.
        sent = []

        async def send_ok():
            sent.append(1)

        later = asyncio.create_task(pr.request("CH", "k", send_ok, timeout=5, same=None))
        await asyncio.sleep(0)
        assert sent == [1]
        assert pr.resolve("CH", "k", "ok")
        assert await later == "ok"

    _run(scenario)


def test_cancelled_while_sending_fails_the_joiners_and_leaves_nothing():
    async def scenario():
        pr = cs.PendingRequests()
        never = asyncio.Event()

        async def send():
            await never.wait()

        owner = asyncio.create_task(pr.request("CH", "k", send, timeout=5, same=None))
        await asyncio.sleep(0)
        joiner = asyncio.create_task(pr.request("CH", "k", send, timeout=5, same=None))
        await asyncio.sleep(0)
        owner.cancel()

        try:
            await asyncio.wait_for(joiner, 1)
            assert False, "expected an error"
        except ConnectionError as exc:
            assert "stopped while being sent" in str(exc)
        assert owner.cancelled()
        assert pr._pending == {}

    _run(scenario)


def test_one_waiter_timing_out_leaves_the_request_to_the_others():
    async def scenario():
        pr = cs.PendingRequests()

        async def send():
            pass

        long = asyncio.create_task(pr.request("CH", "k", send, timeout=5, same=None))
        await asyncio.sleep(0)                  # long sent it; short joins
        short = asyncio.create_task(pr.request("CH", "k", send, timeout=0.01, same=None))
        try:
            await short
            assert False, "expected TimeoutError"
        except asyncio.TimeoutError:
            pass

        assert pr.resolve("CH", "k", "late but wanted")
        assert await asyncio.wait_for(long, 1) == "late but wanted"
        assert pr._pending == {}

    _run(scenario)


def test_a_different_request_with_the_same_reply_key_waits_its_turn():
    """The key probe's reply names only the path: a probe of the same path for
    another dataset type must not take the first one's reply."""
    async def scenario():
        pr = cs.PendingRequests()
        sent = []

        def send(typ):
            async def push():
                sent.append(typ)
            return push

        first = asyncio.create_task(pr.request("KEYS", "/f.xyz", send("ase"), timeout=5, same="ase"))
        await asyncio.sleep(0)
        second = asyncio.create_task(pr.request("KEYS", "/f.xyz", send("npz"), timeout=5, same="npz"))
        await asyncio.sleep(0)
        assert sent == ["ase"]                  # the second waits, unsent

        assert pr.resolve("KEYS", "/f.xyz", "for ase")
        assert await first == "for ase"
        for _ in range(3):
            await asyncio.sleep(0)
        assert sent == ["ase", "npz"]
        assert pr.resolve("KEYS", "/f.xyz", "for npz")
        assert await asyncio.wait_for(second, 1) == "for npz"

    _run(scenario)


def test_the_length_probe_answer_is_the_last_reply_not_the_first():
    """The server replies as its count grows (done=False), then once with
    done=True; taking the first gave a partial frame count."""
    assert cs._reply_length(["/f.xyz"], {"n": 1000, "done": False}) == cs._Progress("/f.xyz")
    assert cs._reply_length(["/f.xyz"], {"n": 5000, "done": True}) == ("/f.xyz", {"n": 5000, "done": True})
    assert cs._reply_length(["/f.xyz"], {"n": 5000}) == ("/f.xyz", {"n": 5000})   # an older server


def test_progress_restarts_the_timeout():
    """A count of a big file can outlast the timeout; while the server keeps
    reporting progress the request is not given up on."""
    async def scenario():
        pr = cs.PendingRequests()

        async def send():
            pass

        task = asyncio.create_task(pr.request("LEN", "/big.xyz", send, timeout=1.0, same=None))
        for _ in range(8):                       # 1.6 s in all, past the 1 s timeout;
            await asyncio.sleep(0.2)             # each gap leaves 0.8 s to spare
            assert pr.touch("LEN", "/big.xyz")
        assert not task.done()
        assert pr.resolve("LEN", "/big.xyz", {"n": 5_000_000, "done": True})
        assert (await task)["n"] == 5_000_000

    _run(scenario)


def test_without_progress_the_timeout_still_ends_it():
    async def scenario():
        pr = cs.PendingRequests()

        async def send():
            pass

        try:
            await pr.request("LEN", "/f.xyz", send, timeout=0.05, same=None)
            assert False, "expected TimeoutError"
        except asyncio.TimeoutError:
            pass
        assert pr.touch("LEN", "/f.xyz") is False      # nothing left to restart
        assert pr._pending == {}

    _run(scenario)


def test_every_caller_must_say_what_its_key_leaves_out():
    """``same`` has no default: a caller whose key does not name its whole
    request would otherwise silently share another request's reply."""
    import inspect
    param = inspect.signature(cs.PendingRequests.request).parameters["same"]
    assert param.default is inspect.Parameter.empty
