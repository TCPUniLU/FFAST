"""Readers: any file format loads through a small class that returns plain
numpy arrays (ADR 0095). Authors import from here only::

    from ffast.readers import ReaderError, warn, check_reader
"""
from ffast.readers.check import check_reader
from ffast.readers.contract import CanWrite, Contents, ReaderError, warn

from ffast.readers.ase_reader import AseReader
from ffast.readers.npz_reader import NpzReader

# FFAST's own readers, in the order a file's ending asks them: an .npz is
# sGDML's before it is ASE's, which takes everything but .xyz and .extxyz.
BUNDLED: list = [NpzReader, AseReader]

__all__ = ["CanWrite", "Contents", "ReaderError", "check_reader", "warn", "BUNDLED"]
