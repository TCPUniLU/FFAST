"""Tests for visualization.buffers (M4: content-addressed Result Buffers)."""

import pytest
import numpy as np

from ffast.visualization.buffers import (
    BufferChunk,
    BufferCodec,
    BufferService,
    BufferTransfer,
    ResultBuffer,
)

try:
    import zstandard  # type: ignore[import]
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False


# ── ResultBuffer ───────────────────────────────────────────────────────────────

def test_buffer_id_is_deterministic():
    arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    buf1 = ResultBuffer.from_array(arr)
    buf2 = ResultBuffer.from_array(arr.copy())
    assert buf1.id == buf2.id


def test_buffer_id_changes_with_content():
    a = np.array([1.0], dtype=np.float32)
    b = np.array([2.0], dtype=np.float32)
    assert ResultBuffer.from_array(a).id != ResultBuffer.from_array(b).id


def test_buffer_id_changes_with_dtype():
    arr = np.array([1, 2, 3])
    buf_i32 = ResultBuffer.from_array(arr.astype(np.int32))
    buf_i64 = ResultBuffer.from_array(arr.astype(np.int64))
    assert buf_i32.id != buf_i64.id


def test_buffer_id_changes_with_shape():
    a = np.ones((2, 3), dtype=np.float32)
    b = np.ones((3, 2), dtype=np.float32)
    assert ResultBuffer.from_array(a).id != ResultBuffer.from_array(b).id


def test_buffer_roundtrip():
    arr = np.random.default_rng(0).standard_normal((4, 5)).astype(np.float32)
    buf = ResultBuffer.from_array(arr)
    recovered = buf.to_array()
    np.testing.assert_array_equal(arr, recovered)


def test_buffer_verify_passes():
    arr = np.arange(12, dtype=np.int32).reshape(3, 4)
    buf = ResultBuffer.from_array(arr)
    assert buf.verify()


def test_buffer_verify_fails_on_tampered_data():
    arr = np.arange(10, dtype=np.float64)
    buf = ResultBuffer.from_array(arr)
    tampered = ResultBuffer(id=buf.id, dtype=buf.dtype, shape=buf.shape, data=b"\x00" * len(buf.data))
    assert not tampered.verify()


# ── BufferTransfer / chunking ──────────────────────────────────────────────────

def test_transfer_none_codec_all_data_received():
    arr = np.arange(1000, dtype=np.float64)
    buf = ResultBuffer.from_array(arr)
    transfer = BufferTransfer(buffer=buf, codec=BufferCodec.none, chunk_size=512)

    received = b"".join(c.data for c in transfer.all_chunks())
    assert received == buf.data


def test_transfer_chunk_metadata():
    arr = np.ones(100, dtype=np.float32)
    buf = ResultBuffer.from_array(arr)
    transfer = BufferTransfer(buffer=buf, codec=BufferCodec.none, chunk_size=50)

    chunks = list(transfer.all_chunks())
    for i, chunk in enumerate(chunks):
        assert chunk.buffer_id == buf.id
        assert chunk.chunk_index == i
        assert chunk.total_chunks == transfer.total_chunks
        assert chunk.codec == BufferCodec.none


def test_transfer_resume_skips_verified_chunks():
    arr = np.arange(200, dtype=np.int32)
    buf = ResultBuffer.from_array(arr)
    transfer = BufferTransfer(buffer=buf, codec=BufferCodec.none, chunk_size=64)

    all_chunks = list(transfer.all_chunks())
    resumed = list(transfer.chunks_from(2))
    assert [c.chunk_index for c in resumed] == [c.chunk_index for c in all_chunks[2:]]


def test_transfer_resume_full_skip_yields_nothing():
    arr = np.array([42.0])
    buf = ResultBuffer.from_array(arr)
    transfer = BufferTransfer(buffer=buf, codec=BufferCodec.none, chunk_size=64)
    assert list(transfer.chunks_from(transfer.total_chunks)) == []


def test_transfer_single_chunk_for_small_array():
    arr = np.array([1, 2, 3], dtype=np.int8)
    buf = ResultBuffer.from_array(arr)
    transfer = BufferTransfer(buffer=buf, codec=BufferCodec.none, chunk_size=256 * 1024)
    assert transfer.total_chunks == 1


@pytest.mark.skipif(not HAS_ZSTD, reason="zstandard not installed")
def test_transfer_zstd_codec_recovers_original():
    arr = np.zeros(2000, dtype=np.float32)  # compressible
    buf = ResultBuffer.from_array(arr)
    transfer = BufferTransfer(buffer=buf, codec=BufferCodec.zstd, chunk_size=512)

    import zstandard as zstd
    compressed = b"".join(c.data for c in transfer.all_chunks())
    decompressed = zstd.decompress(compressed)
    assert decompressed == buf.data


def test_transfer_zstd_raises_without_package(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "zstandard":
            raise ImportError("mocked")
        return real_import(name, *args, **kwargs)

    arr = np.array([1.0, 2.0])
    buf = ResultBuffer.from_array(arr)
    monkeypatch.setattr(builtins, "__import__", mock_import)
    with pytest.raises(RuntimeError, match="zstandard"):
        BufferTransfer(buffer=buf, codec=BufferCodec.zstd, chunk_size=64)


# ── BufferService ─────────────────────────────────────────────────────────────

def test_service_store_and_get():
    svc = BufferService()
    arr = np.array([10, 20, 30], dtype=np.int64)
    buf = svc.store(arr)
    assert buf.id in svc
    retrieved = svc.get(buf.id)
    assert retrieved is not None
    np.testing.assert_array_equal(retrieved.to_array(), arr)


def test_service_store_is_idempotent():
    svc = BufferService()
    arr = np.array([1.0, 2.0])
    buf1 = svc.store(arr)
    buf2 = svc.store(arr.copy())
    assert buf1.id == buf2.id


def test_service_get_unknown_returns_none():
    svc = BufferService()
    assert svc.get("deadbeef") is None


def test_service_evict():
    svc = BufferService()
    arr = np.zeros(10)
    buf = svc.store(arr)
    svc.evict(buf.id)
    assert buf.id not in svc
    assert svc.get(buf.id) is None


def test_service_evict_unknown_is_noop():
    svc = BufferService()
    svc.evict("nonexistent")  # should not raise


def test_service_transfer_yields_all_data():
    svc = BufferService(chunk_size=32)
    arr = np.arange(100, dtype=np.float32)
    buf = svc.store(arr)
    chunks = list(svc.transfer(buf.id))
    received = b"".join(c.data for c in chunks)
    assert received == buf.data


def test_service_transfer_unknown_returns_none():
    svc = BufferService()
    assert svc.transfer("missing") is None


def test_service_transfer_resume():
    svc = BufferService(chunk_size=16)
    arr = np.arange(50, dtype=np.int32)
    buf = svc.store(arr)

    all_chunks = list(svc.transfer(buf.id))
    resumed_chunks = list(svc.transfer(buf.id, resume_from=2))
    assert [c.chunk_index for c in resumed_chunks] == list(range(2, len(all_chunks)))


@pytest.mark.skipif(not HAS_ZSTD, reason="zstandard not installed")
def test_service_transfer_zstd_codec_recovers_original():
    # End-to-end zstd through BufferService.transfer (the other service tests
    # only drive codec=none); the joined compressed chunks must decompress back
    # to the buffer's canonical bytes.
    svc = BufferService(chunk_size=512)
    arr = np.zeros(2000, dtype=np.float32)  # compressible
    buf = svc.store(arr)

    import zstandard as zstd
    chunks = list(svc.transfer(buf.id, codec=BufferCodec.zstd))
    assert all(c.codec == BufferCodec.zstd for c in chunks)
    compressed = b"".join(c.data for c in chunks)
    assert zstd.decompress(compressed) == buf.data


# ── empty-array edge ────────────────────────────────────────────────────────

def test_empty_array_roundtrips():
    arr = np.array([], dtype=np.float32)
    buf = ResultBuffer.from_array(arr)
    assert buf.data == b""
    assert buf.verify()
    np.testing.assert_array_equal(buf.to_array(), arr)


def test_empty_buffer_transfer_yields_single_empty_chunk():
    # An empty payload produces zero slices; the `if not self._chunks:
    # self._chunks = [b""]` guard must still emit exactly one (empty) chunk so
    # total_chunks is 1 and the transfer isn't mistaken for "nothing to send".
    buf = ResultBuffer.from_array(np.array([], dtype=np.float64))
    transfer = BufferTransfer(buffer=buf, codec=BufferCodec.none, chunk_size=64)
    assert transfer.total_chunks == 1
    chunks = list(transfer.all_chunks())
    assert [c.data for c in chunks] == [b""]
    assert b"".join(c.data for c in chunks) == buf.data
