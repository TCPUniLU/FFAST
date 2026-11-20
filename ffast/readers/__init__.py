"""Readers: any file format loads through a small class that returns plain
numpy arrays (ADR 0095). Authors import from here only::

    from ffast.readers import ReaderError, warn, check_reader
"""
from ffast.readers.check import check_reader
from ffast.readers.contract import CanWrite, Contents, ReaderError, warn

from ffast.readers.ase_reader import AseReader
from ffast.readers.bundle_reader import BundleReader
from ffast.readers.npz_reader import NpzReader
from ffast.readers.xyz_reader import XyzReader

# FFAST's own readers, in the order a file's ending asks them: an .npz is
# sGDML's before it is ASE's; .xyz and .extxyz are the xyz reader's, ASE's only
# when its fast parser cannot read the file; a bundle's .ffast.json is the
# bundle reader's, before ASE's catch-all.
BUNDLED: list = [NpzReader, XyzReader, BundleReader, AseReader]

__all__ = ["CanWrite", "Contents", "ReaderError", "check_reader", "warn", "BUNDLED"]
