"""Digest of a byte range of a source file (ADR 0111).

``SCHEME`` names the recipe. The digest of a range is the SHA-256 of the
scheme tag followed by the SHA-256 of each ``PIECE``-sized piece of the range,
counted from the range's start, the last piece short. An empty range hashes
the tag only. The compiled frame finder (``ffast_xyz.find_frames``) computes
the same digest for the bytes it consumed; this module is the definition and
the way to check a range later.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

SCHEME = "ffast-bytes-v1-sha256-8M"
PIECE = 8 * 2**20


def range_digest(data) -> bytes:
    """The digest of the bytes ``data`` (bytes or a memoryview)."""
    view = memoryview(data).cast("B")
    outer = hashlib.sha256(SCHEME.encode())
    for at in range(0, len(view), PIECE):
        outer.update(hashlib.sha256(view[at:at + PIECE]).digest())
    return outer.digest()


def file_range_digests(path, ranges, threads=None) -> list[bytes]:
    """One digest per ``(start, end)`` of ``ranges`` (sorted, not overlapping).

    Reads the file once, in order, at most ``PIECE`` bytes per read, into
    buffers that are reused; pieces are hashed on a thread pool, so memory is
    bounded by about ``threads * PIECE``, not by the size of a range. A file
    that ends before a range does gives the digest of what was there, so the
    caller's size check, not this function, finds a short file.
    """
    import os
    threads = max(1, threads or min(8, os.cpu_count() or 1))
    ranges = [(int(a), int(b)) for a, b in ranges]
    out = []
    free = [bytearray(PIECE) for _ in range(threads + 1)]
    with open(path, "rb", buffering=0) as f, ThreadPoolExecutor(max_workers=threads) as pool:
        pending = []                       # (future, buffer) in read order

        def settle(keep):
            while len(pending) > keep:
                future, buffer = pending.pop(0)
                pieces.append(future.result())
                free.append(buffer)

        for a, b in ranges:
            pieces = []
            at = a
            f.seek(a)
            while at < b:
                if not free:
                    settle(len(pending) - 1)
                buffer = free.pop()
                want = min(PIECE, b - at)
                view = memoryview(buffer)
                got = 0
                while got < want:
                    n = f.readinto(view[got:want])
                    if not n:
                        break
                    got += n
                if got == 0:
                    free.append(buffer)
                    break
                pending.append((pool.submit(_hash_piece, buffer, got), buffer))
                at += got
                if got < want:
                    break
            settle(0)
            outer = hashlib.sha256(SCHEME.encode())
            for piece in pieces:
                outer.update(piece)
            out.append(outer.digest())
    return out


def _hash_piece(buffer, n):
    return hashlib.sha256(memoryview(buffer)[:n]).digest()
