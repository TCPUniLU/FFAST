"""Content-addressed Result Buffer service.

Buffer identity: SHA-256(dtype_str + packed_shape + canonical_uncompressed_data).
Compression codec and chunk parameters are transfer concerns, not identity.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

import numpy as np


class BufferCodec(str, Enum):
    none = "none"
    zstd = "zstd"


def _try_compress_zstd(data: bytes) -> bytes:
    try:
        import zstandard as zstd  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "zstandard package required for zstd codec: pip install zstandard"
        )
    return zstd.compress(data)


def _compute_buffer_id(dtype_str: str, shape: tuple[int, ...], canonical: bytes) -> str:
    """SHA-256(dtype_str bytes + packed int64 shape + canonical data)."""
    h = hashlib.sha256()
    h.update(dtype_str.encode())
    h.update(struct.pack(f"<{len(shape)}q", *shape))
    h.update(canonical)
    return h.hexdigest()


@dataclass(frozen=True)
class ResultBuffer:
    """Immutable content-addressed numpy payload.

    Identity is determined solely by dtype, shape, and uncompressed bytes.
    Codec and chunking are transfer concerns and do not change identity.
    """

    id: str                  # SHA-256 hex
    dtype: str               # numpy dtype string, e.g. "<f4"
    shape: tuple[int, ...]
    data: bytes              # canonical (uncompressed) bytes

    @classmethod
    def from_array(cls, array: np.ndarray) -> "ResultBuffer":
        arr = np.ascontiguousarray(array)
        canonical = arr.tobytes()
        buf_id = _compute_buffer_id(arr.dtype.str, tuple(arr.shape), canonical)
        return cls(id=buf_id, dtype=arr.dtype.str, shape=tuple(arr.shape), data=canonical)

    def to_array(self) -> np.ndarray:
        return np.frombuffer(self.data, dtype=np.dtype(self.dtype)).reshape(self.shape)

    def verify(self) -> bool:
        """Re-compute ID and check content integrity."""
        expected = _compute_buffer_id(self.dtype, self.shape, self.data)
        return self.id == expected


_DEFAULT_CHUNK_BYTES = 256 * 1024  # 256 KiB


@dataclass
class BufferChunk:
    """One chunk in a chunked buffer transfer."""

    buffer_id: str
    chunk_index: int
    total_chunks: int
    codec: BufferCodec
    data: bytes              # encoded (possibly compressed) bytes


@dataclass
class BufferTransfer:
    """Server-side chunked transfer state for one ResultBuffer.

    Reconnect resume: ``chunks_from(n)`` skips the first n verified chunks
    so the client can continue without re-receiving data it already holds.
    """

    buffer: ResultBuffer
    codec: BufferCodec
    chunk_size: int
    _chunks: list[bytes] = field(default_factory=list, repr=False, init=False)

    def __post_init__(self) -> None:
        payload = (
            _try_compress_zstd(self.buffer.data)
            if self.codec == BufferCodec.zstd
            else self.buffer.data
        )
        size = self.chunk_size
        self._chunks = [payload[i: i + size] for i in range(0, len(payload), size)]
        if not self._chunks:
            self._chunks = [b""]

    @property
    def total_chunks(self) -> int:
        return len(self._chunks)

    def chunk(self, index: int) -> BufferChunk:
        return BufferChunk(
            buffer_id=self.buffer.id,
            chunk_index=index,
            total_chunks=self.total_chunks,
            codec=self.codec,
            data=self._chunks[index],
        )

    def chunks_from(self, verified_count: int = 0) -> Iterator[BufferChunk]:
        """Yield chunks starting after verified_count already-received chunks."""
        for i in range(verified_count, self.total_chunks):
            yield self.chunk(i)

    def all_chunks(self) -> Iterator[BufferChunk]:
        return self.chunks_from(0)


class BufferService:
    """Server-side content-addressed store for ResultBuffers.

    Clients manage their own buffer memory budgets and may evict buffers;
    the service re-serves any buffer the client re-requests by ID.
    """

    def __init__(self, chunk_size: int = _DEFAULT_CHUNK_BYTES) -> None:
        self._buffers: dict[str, ResultBuffer] = {}
        self._chunk_size = chunk_size

    def store(self, array: np.ndarray) -> ResultBuffer:
        """Store a numpy array; return its ResultBuffer (idempotent by content)."""
        buf = ResultBuffer.from_array(array)
        self._buffers[buf.id] = buf
        return buf

    def get(self, buffer_id: str) -> ResultBuffer | None:
        return self._buffers.get(buffer_id)

    def transfer(
        self,
        buffer_id: str,
        codec: BufferCodec = BufferCodec.none,
        resume_from: int = 0,
    ) -> Iterator[BufferChunk] | None:
        """Return a chunk iterator for buffer_id, or None if unknown.

        ``resume_from`` is the number of verified chunks the client already
        holds; only the remaining chunks are yielded (reconnect resume).
        """
        buf = self._buffers.get(buffer_id)
        if buf is None:
            return None
        t = BufferTransfer(buffer=buf, codec=codec, chunk_size=self._chunk_size)
        return t.chunks_from(resume_from)

    def evict(self, buffer_id: str) -> None:
        """Remove a buffer; clients may re-request it by ID at any time."""
        self._buffers.pop(buffer_id, None)

    def __contains__(self, buffer_id: str) -> bool:
        return buffer_id in self._buffers
