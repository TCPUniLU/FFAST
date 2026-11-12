"""Readers: any file format loads through a small class that returns plain
numpy arrays (ADR 0095). Authors import from here only::

    from ffast.readers import ReaderError, warn, check_reader
"""
from ffast.readers.check import check_reader
from ffast.readers.contract import CanWrite, Contents, ReaderError, warn

# FFAST's own readers; none until step 3 of docs/implementation/2026-11-12-file-readers.md.
BUNDLED: list = []

__all__ = ["CanWrite", "Contents", "ReaderError", "check_reader", "warn", "BUNDLED"]
