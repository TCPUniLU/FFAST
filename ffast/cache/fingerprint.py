"""Content fingerprinting for cache/model identity (ADR 0047, ADR 0107).

``fingerprintFromArraysAndStrings`` hashes a mix of numpy arrays and strings
into a stable hex digest: the identity of prediction data (ghost-model
fingerprints keyed on E/F arrays) and other cached artefacts. Pure
``hashlib`` + ``numpy``, GUI-free; kept out of the old flat ``utils.py`` so
the server-closure loaders and Loading Coordinator can reach it without
importing the client spine.

The recipe (ADR 0107): every column is cut into chunks of ``CHUNK_ROWS`` rows
(frame rows for per-frame values, atom rows for per-atom values), each chunk is
hashed with SHA-256, and the column digest is the hash of ``SCHEME`` followed
by the chunk digests in order. The cut depends only on row numbers, so a whole
array, a column fed in windows by a memory-saving reader, and a column
restored half-way after an interrupted scan all give the same digest. A
string part is the hash of its UTF-8 bytes; a combined fingerprint is the hash
of ``SCHEME`` followed by the part digests (ADR 0058: the formula rules are
unchanged).

The digest covers the bytes of the rows in the dtype the caller hashes. It
does not cover the dtype name or the row width: two columns with the same
bytes and different row shapes may give the same digest. The callers keep
those apart, as they did with md5.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np

CHUNK_ROWS = 16384
SCHEME = b"ffast-chunks-v1-sha256-16384"
_THREAD_BYTES = 2**20
_THREADS = 4


class ChunkHasher:
    """The chunked digest of one column, fed rows in order, in any pieces.

    ``update`` hashes each completed chunk at once and keeps a *copy* of the
    rows of the unfinished chunk (never a view: a parser buffer is reused).
    ``digests()`` and ``tail()`` are what a scan saves; ``restore`` takes them
    back, and the rest of the rows then give the same digest as an
    uninterrupted run.
    """

    def __init__(self, tail_shape=(), dtype=np.float64):
        self.tail_shape = tuple(tail_shape)
        self.dtype = np.dtype(dtype)
        self._width = int(np.prod(self.tail_shape, dtype=np.int64))
        self._done = []
        self._pending = []
        self._pending_rows = 0

    def _rows(self, rows):
        rows = np.ascontiguousarray(rows, dtype=self.dtype)
        return rows.reshape(rows.shape[0] if rows.ndim else 1, self._width)

    def update(self, rows):
        rows = self._rows(rows)
        n = rows.shape[0]
        at = 0
        if self._pending_rows and self._pending_rows + n >= CHUNK_ROWS:
            take = CHUNK_ROWS - self._pending_rows
            h = hashlib.sha256()
            for piece in self._pending:
                h.update(piece)
            h.update(rows[:take])
            self._done.append(h.digest())
            self._pending, self._pending_rows = [], 0
            at = take
        while n - at >= CHUNK_ROWS:
            self._done.append(hashlib.sha256(rows[at:at + CHUNK_ROWS]).digest())
            at += CHUNK_ROWS
        if at < n:
            self._pending.append(rows[at:].copy())
            self._pending_rows += n - at

    def digests(self):
        """The digests of the completed chunks, in order (32 bytes each)."""
        return list(self._done)

    def all_digests(self):
        """The digest of every chunk including the unfinished one: what
        ``column_digest_of_chunks`` needs to rebuild ``hexdigest()``."""
        out = list(self._done)
        if self._pending_rows:
            h = hashlib.sha256()
            for piece in self._pending:
                h.update(piece)
            out.append(h.digest())
        return out

    def tail(self):
        """The rows of the unfinished chunk, as one array."""
        if not self._pending:
            return np.empty((0, *self.tail_shape), dtype=self.dtype)
        rows = np.concatenate(self._pending) if len(self._pending) > 1 else self._pending[0]
        return rows.reshape(-1, *self.tail_shape)

    @property
    def rows(self):
        """How many rows have been fed (or restored)."""
        return len(self._done) * CHUNK_ROWS + self._pending_rows

    def restore(self, digests, tail):
        self._done = [bytes(d) for d in digests]
        tail = self._rows(tail)
        self._pending = [tail.copy()] if len(tail) else []
        self._pending_rows = len(tail)

    def hexdigest(self):
        """The column digest. Does not change the hasher."""
        column = hashlib.sha256(SCHEME)
        for d in self._done:
            column.update(d)
        if self._pending_rows:
            h = hashlib.sha256()
            for piece in self._pending:
                h.update(piece)
            column.update(h.digest())
        return column.hexdigest()


def column_digest_of_chunks(chunk_digests):
    """The column digest from the digest of every chunk, in order (32 bytes
    each, the last chunk possibly short): the inverse of ``all_digests()``."""
    column = hashlib.sha256(SCHEME)
    for d in chunk_digests:
        column.update(bytes(d))
    return column.hexdigest()


def array_digest(array):
    """The column digest of a whole array; rows are its first axis. Large
    arrays hash their chunks on up to four threads (``hashlib`` lets go of the
    GIL for big buffers); the result equals one ``ChunkHasher``."""
    array = np.asarray(array)
    if array.ndim == 0:
        array = array.reshape(1)
    n = array.shape[0]
    flat = (np.ascontiguousarray(array).reshape(n, -1) if n
            else np.empty((0, 1), dtype=array.dtype))
    whole = n - n % CHUNK_ROWS
    starts = range(0, whole, CHUNK_ROWS)

    def chunk(a):
        return hashlib.sha256(flat[a:a + CHUNK_ROWS]).digest()

    if flat.nbytes >= _THREAD_BYTES and len(starts) > 1:
        with ThreadPoolExecutor(max_workers=_THREADS) as pool:
            digests = list(pool.map(chunk, starts))
    else:
        digests = [chunk(a) for a in starts]
    column = hashlib.sha256(SCHEME)
    for d in digests:
        column.update(d)
    if n % CHUNK_ROWS:
        column.update(hashlib.sha256(flat[whole:]).digest())
    return column.hexdigest()


def fingerprintFromParts(digests):
    """Combine one raw digest (bytes) per input, in order, into the fingerprint."""
    fp = hashlib.sha256(SCHEME)
    for d in digests:
        fp.update(d)
    return fp.hexdigest()


def _array_part(arg):
    """The column digest of an array or list, as raw bytes. A list of arrays
    (variable-sized datasets) is hashed as their concatenation along the first
    axis, without joining them."""
    if isinstance(arg, np.ndarray):
        return bytes.fromhex(array_digest(arg))
    if isinstance(arg, list):
        if arg and isinstance(arg[0], np.ndarray):
            hasher = ChunkHasher(arg[0].shape[1:], arg[0].dtype)
            for piece in arg:
                hasher.update(piece)
            return bytes.fromhex(hasher.hexdigest())
        return bytes.fromhex(array_digest(np.array(arg)))
    raise TypeError(f"fingerprintFromArraysAndStrings cannot hash {type(arg).__name__}: {arg!r}")


def fingerprintFromArraysAndStrings(*args):
    """Hash arrays, strings and bytes, each on its own, then those digests in order."""
    from ffast.io.batched import BatchedArray

    parts = []
    for arg in args:
        if isinstance(arg, BatchedArray):
            parts.append(bytes.fromhex(arg.content_digest()))
        elif isinstance(arg, str):
            parts.append(hashlib.sha256(arg.encode("utf8")).digest())
        elif isinstance(arg, bytes):
            parts.append(hashlib.sha256(arg).digest())
        else:
            parts.append(_array_part(arg))
    return fingerprintFromParts(parts)


def sgdml_md5(*arrays):
    """sGDML's own recipe, kept only for the ``md5`` field of sGDML files,
    which sGDML checks: the md5 of the md5 of each input's flattened bytes."""
    from ffast.io.batched import BatchedArray

    fp = hashlib.md5()
    for arg in arrays:
        digest = (bytes.fromhex(arg.content_digest()) if isinstance(arg, BatchedArray)
                  else hashlib.md5(_sgdml_bytes(arg)).digest())
        fp.update(digest)
    return fp.hexdigest()


def _sgdml_bytes(arg):
    if isinstance(arg, str):
        return arg.encode("utf8")
    if isinstance(arg, bytes):
        return arg
    if isinstance(arg, np.ndarray):
        return arg.ravel()
    if isinstance(arg, list):
        if len(arg) > 0 and isinstance(arg[0], np.ndarray):
            return np.concatenate([a.ravel() for a in arg])
        return np.array(arg).ravel()
    raise TypeError(f"sgdml_md5 cannot hash {type(arg).__name__}: {arg!r}")
